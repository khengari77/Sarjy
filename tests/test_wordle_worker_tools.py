"""Worker Wordle function tools: backend call, popup sync and failure honesty."""

import asyncio
import json
from types import SimpleNamespace

import httpx
from livekit.agents import llm
from main import SarjyAgent
from wordle_tools import (
    WORDLE_FAILED,
    claims_game_state,
    extract_guess,
    has_guess_candidate,
    is_close_request,
    publish_wordle,
)


def build(handler, published, active=False):
    async def publish(action):
        published.append(action)

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    agent = SarjyAgent(
        client,
        {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
        instructions="Authoritative instructions.",
        publish_wordle=publish,
        wordle_active=lambda: active,
    )
    return client, agent


def ctx(call_id="call-1"):
    return SimpleNamespace(function_call=SimpleNamespace(call_id=call_id))


def test_tools_are_registered_on_the_agent():
    _, agent = build(lambda request: httpx.Response(200, json={}), [])
    names = {tool.info.name for tool in agent.tools}
    assert {"play_wordle", "submit_wordle_guess", "wordle_status", "close_wordle"} <= names


def test_play_posts_the_tool_route_and_opens_the_popup():
    seen: list[tuple[str, dict]] = []
    published: list[str] = []

    def handler(request):
        if request.url.path == "/internal/agent/turn/start":
            return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "in_progress", "ui": "open", "message": "Ready, 6 guesses left."})

    async def run():
        client, agent = build(handler, published)
        async with client:
            message = llm.ChatMessage(id="m1", role="user", content=["Can we play the daily Wordle?"])
            await agent.on_user_turn_completed(None, message)
            reply = await agent.play_wordle._func(agent, ctx())
            assert reply == "Ready, 6 guesses left."
            assert "did not say" in await agent.submit_wordle_guess._func(agent, ctx("call-2"), word="crane")
            agent.current_text = "Crane."
            await agent.submit_wordle_guess._func(agent, ctx("call-3"), word="crane")

    asyncio.run(run())
    assert [path for path, _ in seen] == ["/internal/agent/tool/wordle-play", "/internal/agent/tool/wordle-guess"]
    assert seen[1][1]["word"] == "crane" and seen[1][1]["turn_id"] == "turn-1"
    assert seen[0][1]["request_id"] != seen[1][1]["request_id"]
    assert published == ["open", "open"]


def test_backend_failure_is_reported_honestly_and_never_opens_the_popup():
    published: list[str] = []

    def handler(request):
        if request.url.path == "/internal/agent/turn/start":
            return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})
        return httpx.Response(503, json={})

    async def run():
        client, agent = build(handler, published)
        async with client:
            message = llm.ChatMessage(id="m1", role="user", content=["play wordle please"])
            await agent.on_user_turn_completed(None, message)
            assert await agent.wordle_status._func(agent, ctx()) == WORDLE_FAILED

    asyncio.run(run())
    assert published == []


def test_disabled_result_does_not_touch_the_popup():
    published: list[str] = []

    def handler(request):
        if request.url.path == "/internal/agent/turn/start":
            return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})
        return httpx.Response(200, json={"status": "disabled", "message": "Wordle is switched off."})

    async def run():
        client, agent = build(handler, published)
        async with client:
            await agent.on_user_turn_completed(
                None, llm.ChatMessage(id="m1", role="user", content=["let's play wordle"])
            )
            assert await agent.play_wordle._func(agent, ctx()) == "Wordle is switched off."

    asyncio.run(run())
    assert published == []


def test_publish_wordle_uses_the_sarjy_wordle_topic():
    calls: list[tuple] = []

    class Participant:
        async def publish_data(self, payload, *, reliable, topic):
            calls.append((json.loads(payload), reliable, topic))

    asyncio.run(publish_wordle(SimpleNamespace(local_participant=Participant()), "open"))
    assert calls == [({"action": "open"}, True, "sarjy.wordle")]


def test_only_whole_utterance_guesses_are_extracted():
    for text, word in (
        ("Snake.", "SNAKE"),
        ("No. My word my word is false.", "FALSE"),
        ("S N A K E.", "SNAKE"),
        ("Uh... crane.", "CRANE"),
        ("Crane. Crane.", "CRANE"),
        ("I think it's c-r-a-n-e", "CRANE"),
    ):
        assert extract_guess(text) == word, text
    for text in (
        "Be quick.",
        "Be quick.",
        "Is there a vowel in it?",
        "R.",
        "Fals.",
        "Where am I at in this game?",
        "Make your voice a lady",
        "",
    ):
        assert extract_guess(text) is None, text


def test_state_claims_are_detected():
    for text in ("N is yellow and A is green.", "You have three guesses left.", "You won!", "Out of attempts."):
        assert claims_game_state(text), text
    for text in ("Sure, give me a five-letter word.", "Tell me a joke."):
        assert not claims_game_state(text), text


def test_open_game_withdraws_play_unless_it_was_called_this_turn():
    _, agent = build(lambda request: httpx.Response(200, json={}), [], active=True)
    agent.current_turn = "t1"
    agent.current_text = "Crane."
    names = {tool.info.name for tool in agent._wordle_tools(agent.tools)}
    assert "play_wordle" not in names and "submit_wordle_guess" in names
    agent.wordle_calls.add("t1")
    assert "play_wordle" in {tool.info.name for tool in agent._wordle_tools(agent.tools)}


def test_bare_guess_is_recorded_and_spoken_without_the_model(monkeypatch):
    seen: list[str] = []

    def handler(request):
        if request.url.path == "/internal/agent/turn/start":
            return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})
        seen.append(request.url.path)
        return httpx.Response(
            200, json={"status": "in_progress", "ui": "open", "spoken": "CRANE: C gray. 5 guesses left."}
        )

    async def run():
        client, agent = build(handler, [], active=True)
        async with client:
            message = llm.ChatMessage(id="m1", role="user", content=["Crane."])
            await agent.on_user_turn_completed(None, message)
            agent.prepared["m1"] = {"turn_id": "turn-1", "context": {}}
            agent.current_turn = "turn-1"
            assert await agent._wordle_turn_speech(message) == "CRANE: C gray. 5 guesses left."
            other = llm.ChatMessage(id="m2", role="user", content=["Be quick."])
            assert await agent._wordle_turn_speech(other) is None

    asyncio.run(run())
    assert seen == ["/internal/agent/tool/wordle-guess"]


def test_closed_game_keeps_play_and_never_records_a_bare_word():
    _, agent = build(lambda request: httpx.Response(200, json={}), [], active=False)
    assert "play_wordle" in {tool.info.name for tool in agent._wordle_tools(agent.tools)}
    message = llm.ChatMessage(id="m1", role="user", content=["false"])
    assert asyncio.run(agent._wordle_turn_speech(message)) is None


def test_close_requests_and_guess_candidates():
    for text in ("Close the popup.", "back to chat please", "Please close the game", "Hide the board."):
        assert is_close_request(text), text
    for text in ("Close the door", "I closed the popup", "Tell me a joke"):
        assert not is_close_request(text), text
    assert has_guess_candidate("I think it might be crane") and has_guess_candidate("s n a k e")
    assert not has_guess_candidate("How are you?") and not has_guess_candidate("Close the popup.")


def test_submit_tool_is_offered_only_for_guess_shaped_text():
    _, agent = build(lambda request: httpx.Response(200, json={}), [], active=True)
    agent.current_turn = "t9"
    agent.current_text = "Tell me a joke."
    assert "submit_wordle_guess" not in {t.info.name for t in agent._wordle_tools(agent.tools)}
    agent.current_text = "maybe the word is crane"
    assert "submit_wordle_guess" in {t.info.name for t in agent._wordle_tools(agent.tools)}


def _guard(active: bool, reply: str, touched: bool = True):
    """Run the prose guard over a model reply with no tool call."""
    from livekit.agents import llm as _llm

    async def stream():
        yield _llm.ChatChunk(id="c", delta=_llm.ChoiceDelta(content=reply))

    async def run():
        _, agent = build(lambda request: httpx.Response(200, json={"spoken": "Real board."}), [], active=active)
        agent.wordle_touched = touched
        agent.current_turn = "turn-1"
        return "".join([c.delta.content or "" async for c in agent._guard_wordle_prose(stream())])

    return asyncio.run(run())


def test_prose_guard_only_applies_while_a_game_is_open():
    # Open game: a claimed state is replaced by the server's real status.
    assert _guard(True, "You got it in three tries, the tiles are green.") == "Real board."
    # Game closed (even though Wordle was used earlier in the call): ordinary replies pass through untouched.
    assert _guard(False, "Grass is green, and it took me three tries.") == "Grass is green, and it took me three tries."
