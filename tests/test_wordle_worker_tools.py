"""Worker Wordle function tools: backend call, popup sync and failure honesty."""

import asyncio
import json
from types import SimpleNamespace

import httpx
from livekit.agents import llm
from main import SarjyAgent
from wordle_tools import WORDLE_FAILED, contains_wordle_guess, publish_wordle


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
            await agent.submit_wordle_guess._func(agent, ctx("call-2"), word="crane")

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


def test_guess_shaped_utterances_are_detected():
    for text in ("No. My word my word is false.", "F a l s e. False.", "Snark.", "my guess is c-r-a-n-e", "crane"):
        assert contains_wordle_guess(text), text
    for text in ("What is the weather?", "how many guesses left", "where are we", "Make your voice a lady", ""):
        assert not contains_wordle_guess(text), text


def test_open_game_forces_the_guess_tool_once_and_withdraws_play():
    _, agent = build(lambda request: httpx.Response(200, json={}), [], active=True)
    names = {tool.info.name for tool in agent._wordle_tools(agent.tools)}
    assert "play_wordle" not in names and "submit_wordle_guess" in names
    message = llm.ChatMessage(id="m1", role="user", content=["No. My word is false."])
    assert agent._forced_wordle_choice(message, names) == {
        "type": "function",
        "function": {"name": "submit_wordle_guess"},
    }
    # The continuation inference after the tool result is never forced.
    assert agent._forced_wordle_choice(message, names) is None
    chat = llm.ChatMessage(id="m2", role="user", content=["what is the weather"])
    assert agent._forced_wordle_choice(chat, names) is None


def test_closed_game_forces_nothing_and_keeps_play_available():
    _, agent = build(lambda request: httpx.Response(200, json={}), [], active=False)
    names = {tool.info.name for tool in agent._wordle_tools(agent.tools)}
    assert "play_wordle" in names
    message = llm.ChatMessage(id="m1", role="user", content=["false"])
    assert agent._forced_wordle_choice(message, names) is None
