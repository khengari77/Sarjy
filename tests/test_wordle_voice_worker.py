"""Worker-side evidence for #110: sarjy.say and command suppression."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from livekit import rtc
from livekit.agents import llm
from main import RevealState, SarjyAgent, apply_control, perform_say, resolve_say_request
from wordle_commands import command_kind, is_wordle_command

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "packages/contracts/wordle_command_fixtures.json"
HUMAN = SimpleNamespace(kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD)
AGENT = SimpleNamespace(kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)


def fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]


def test_worker_classifier_matches_the_shared_fixture_corpus():
    for case in fixtures():
        active = case.get("active", False)
        assert is_wordle_command(case["text"], wordle_active=active) is case["worker"], case["text"]
        if "workerPending" in case:
            pending = is_wordle_command(case["text"], reveal_pending=True, wordle_active=active)
            assert pending is case["workerPending"], case["text"]


def test_worker_classifier_returns_the_client_command_kind():
    assert command_kind("Guess CRANE", wordle_active=True) == "guess"
    assert command_kind("Guess CRANE") is None
    assert command_kind("play wordle") == "play"
    assert command_kind("how many guesses left") == "remaining"
    assert command_kind("quit wordle") == "quit"
    assert command_kind("play wordle") == "play"
    assert command_kind("what is the weather") is None
    assert command_kind("yes") is None
    assert command_kind("yes", reveal_pending=True) == "confirm"
    assert command_kind("no", reveal_pending=True) == "cancel"


class StrictSession:
    """Only ``say`` exists; any other session call fails the test."""

    def __init__(self):
        self.calls: list[dict] = []

    async def say(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})

    def __getattr__(self, name):
        raise AssertionError(f"unexpected session access: {name}")


def say_data(payload: str = '{"text":"The answer was PLANT."}', caller: str = "human"):
    return SimpleNamespace(caller_identity=caller, payload=payload)


def test_say_rpc_speaks_the_exact_result_without_history():
    session = StrictSession()
    participants = {"human": HUMAN, "agent": AGENT}
    response = asyncio.run(perform_say(session, say_data(), participants))
    assert json.loads(response) == {"ok": True}
    assert session.calls == [{"text": "The answer was PLANT.", "allow_interruptions": True, "add_to_chat_ctx": False}]


def test_say_rpc_rejects_agent_callers_and_invalid_payloads():
    session = StrictSession()
    participants = {"human": HUMAN, "agent": AGENT}
    assert resolve_say_request(say_data(caller="agent"), participants) == (None, "SARJY_SAY_FORBIDDEN")
    assert asyncio.run(perform_say(session, say_data(caller="agent"), participants)) == json.dumps(
        {"ok": False, "code": "SARJY_SAY_FORBIDDEN"}
    )
    for payload in ("not json", "{}", '{"text":""}', '{"text":"' + "x" * 301 + '"}', '{"text":"a\\u0000b"}'):
        assert asyncio.run(perform_say(session, say_data(payload), participants)) == json.dumps(
            {"ok": False, "code": "SARJY_SAY_PAYLOAD_INVALID"}
        )
    assert session.calls == []


def test_say_rpc_reports_a_session_that_is_not_ready():
    class Closing(StrictSession):
        async def say(self, text, **kwargs):
            raise RuntimeError("AgentSession isn't running")

    response = asyncio.run(perform_say(Closing(), say_data(), {"human": HUMAN}))
    assert json.loads(response) == {"ok": False, "code": "SARJY_SAY_UNAVAILABLE"}


def test_wordle_active_flag_is_human_only_and_gates_guess_intents():
    state = RevealState()
    packet = lambda participant, value: SimpleNamespace(  # noqa: E731
        topic="sarjy.control",
        participant=participant,
        data=json.dumps({"type": "wordle.active", "value": value}).encode(),
    )
    apply_control(packet(AGENT, True), state)
    assert state.active is False
    apply_control(packet(HUMAN, True), state)
    assert state.active is True
    assert is_wordle_command("guess crane", wordle_active=state.active)
    apply_control(packet(HUMAN, False), state)
    assert state.active is False


def test_ordinary_speech_is_not_suppressed_when_no_game_is_open():
    for text in ("Make your voice a lady", "Make the voice a woman", "Spell my name", "Guess I'll have tea"):
        assert is_wordle_command(text) is False, text


def test_control_messages_only_flip_the_human_reveal_flag(monkeypatch):
    # These cases check the flag itself; the grace window after a clear has its own test.
    monkeypatch.setattr("main.PENDING_GRACE_SECONDS", 0.0)
    reveal = RevealState()
    packet = SimpleNamespace(
        topic="sarjy.control",
        participant=HUMAN,
        data=json.dumps({"type": "wordle.reveal_pending", "value": True}).encode(),
    )
    apply_control(packet, reveal)
    assert reveal.pending is True
    apply_control(SimpleNamespace(topic="sarjy.control", participant=HUMAN, data=b"{}"), reveal)
    assert reveal.pending is True  # unknown payloads are ignored
    apply_control(
        SimpleNamespace(
            topic="sarjy.control",
            participant=AGENT,
            data=json.dumps({"type": "wordle.reveal_pending", "value": False}).encode(),
        ),
        reveal,
    )
    assert reveal.pending is True  # agents cannot clear the user's flag
    apply_control(
        SimpleNamespace(topic="other", participant=HUMAN, data=b'{"type":"wordle.reveal_pending","value":false}'),
        reveal,
    )
    assert reveal.pending is True
    apply_control(
        SimpleNamespace(
            topic="sarjy.control",
            participant=HUMAN,
            data=json.dumps({"type": "wordle.reveal_pending", "value": False}).encode(),
        ),
        reveal,
    )
    assert reveal.pending is False


def agent_for(backend, is_command=None):
    return SarjyAgent(
        backend,
        {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
        instructions="Authoritative instructions.",
        is_command=is_command,
    )


def test_recognized_command_never_reaches_conversational_generation(monkeypatch):
    posted: list[str] = []

    def backend(request):
        posted.append(request.url.path)
        return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})

    async def fail_stream(*_args, **_kwargs):
        raise AssertionError("conversational generation must not run for a Wordle command")
        yield  # pragma: no cover

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client, is_command=lambda text: is_wordle_command(text, wordle_active=True))
            command = llm.ChatMessage(id="m1", role="user", content=["Guess CRANE"])
            await agent.on_user_turn_completed(None, command)
            assert posted == []
            assert agent.suppressed == {"m1"}
            monkeypatch.setattr(agent, "_stream_with_fallback", fail_stream)
            chunks = [chunk async for chunk in agent.llm_node(llm.ChatContext(items=[command]), [], None)]
            assert chunks == []
            assert agent.suppressed == set()
            assert posted == []

            normal = llm.ChatMessage(id="m2", role="user", content=["What is the weather in Paris?"])
            await agent.on_user_turn_completed(None, normal)
            assert posted == ["/internal/agent/turn/start"]
            assert normal.id not in agent.suppressed

    asyncio.run(run())


def test_voice_request_is_not_suppressed_when_no_game_is_open():
    posted: list[str] = []

    def backend(request):
        posted.append(request.url.path)
        return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client, is_command=is_wordle_command)
            message = llm.ChatMessage(id="v1", role="user", content=["Make your voice a lady"])
            await agent.on_user_turn_completed(None, message)
            assert posted == ["/internal/agent/turn/start"]
            assert agent.suppressed == set()

    asyncio.run(run())


def test_reveal_confirmation_is_suppressed_only_while_pending(monkeypatch):
    posted: list[str] = []

    def backend(request):
        posted.append(request.url.path)
        return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})

    async def fail_stream(*_args, **_kwargs):
        raise AssertionError("confirmation must not reach conversational generation")
        yield  # pragma: no cover

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            state = {"pending": False}
            agent = agent_for(client, is_command=lambda text: is_wordle_command(text, reveal_pending=state["pending"]))
            monkeypatch.setattr(agent, "_stream_with_fallback", fail_stream)

            yes = llm.ChatMessage(id="y1", role="user", content=["yes"])
            await agent.on_user_turn_completed(None, yes)
            assert posted == ["/internal/agent/turn/start"]  # ordinary "yes" stays conversational

            posted.clear()
            state["pending"] = True
            await agent.on_user_turn_completed(None, llm.ChatMessage(id="y2", role="user", content=["yes"]))
            assert posted == []
            assert agent.suppressed == {"y2"}

    asyncio.run(run())


def test_reset_phrases_are_suppressed_without_a_model_call_and_confirmation_follows_the_pending_flag(monkeypatch):
    posted: list[str] = []

    def backend(request):
        posted.append(request.url.path)
        return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})

    async def fail_stream(*_args, **_kwargs):
        raise AssertionError("a reset command must not reach conversational generation")
        yield  # pragma: no cover

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            state = {"pending": False}
            agent = agent_for(
                client,
                is_command=lambda text: is_wordle_command(text, reveal_pending=state["pending"], wordle_active=True),
            )
            monkeypatch.setattr(agent, "_stream_with_fallback", fail_stream)
            for index, text in enumerate(("reset the game", "Restart Wordle", "start over", "start the game over")):
                await agent.on_user_turn_completed(None, llm.ChatMessage(id=f"r{index}", role="user", content=[text]))
                assert agent.suppressed == {f"r{index}"}
                agent.suppressed.clear()
            assert posted == []
            # Pending reset: yes / no are the client's answer; anything else is processed normally.
            state["pending"] = True
            for index, text in enumerate(("yes", "no", "cancel cancel")):
                msg = llm.ChatMessage(id=f"c{index}", role="user", content=[text])
                await agent.on_user_turn_completed(None, msg)
            assert agent.suppressed == {"c0", "c1"}
            assert posted == ["/internal/agent/turn/start"]  # only the third, ordinary, turn

    asyncio.run(run())


def test_pending_action_control_accepts_reveal_and_reset_from_humans_only(monkeypatch):
    # These cases check the flag itself; the grace window after a clear has its own test.
    monkeypatch.setattr("main.PENDING_GRACE_SECONDS", 0.0)
    state = RevealState()

    def packet(participant, value):
        data = json.dumps({"type": "wordle.pending", "value": value}).encode()
        return SimpleNamespace(topic="sarjy.control", participant=participant, data=data)

    apply_control(packet(AGENT, "reset"), state)
    assert state.pending is False
    apply_control(packet(HUMAN, "reset"), state)
    assert state.pending is True
    apply_control(packet(HUMAN, None), state)
    assert state.pending is False
    apply_control(packet(HUMAN, "reveal"), state)
    assert state.pending is True
    apply_control(packet(HUMAN, "delete_everything"), state)
    assert state.pending is False


def test_a_pending_guess_hands_yes_and_no_to_the_client(monkeypatch):
    # These cases check the flag itself; the grace window after a clear has its own test.
    monkeypatch.setattr("main.PENDING_GRACE_SECONDS", 0.0)
    state = RevealState()
    state.active = True
    pending = lambda value: SimpleNamespace(  # noqa: E731
        topic="sarjy.control", participant=HUMAN, data=json.dumps({"type": "wordle.pending", "value": value}).encode()
    )
    apply_control(pending("guess"), state)
    assert state.pending is True
    assert is_wordle_command("yes", reveal_pending=state.pending, wordle_active=True)
    apply_control(pending(None), state)
    assert state.pending is False


def test_yes_stays_with_the_client_briefly_after_it_clears_the_pending_flag(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("main.time.monotonic", lambda: clock["now"])
    state = RevealState()
    state.active = True
    pending = lambda value: SimpleNamespace(  # noqa: E731
        topic="sarjy.control", participant=HUMAN, data=json.dumps({"type": "wordle.pending", "value": value}).encode()
    )
    apply_control(pending("guess"), state)
    # The client handles "yes" and clears its flag before the worker classifies the same turn.
    apply_control(pending(None), state)
    clock["now"] += 1.0
    assert state.pending is True
    assert is_wordle_command("yes", reveal_pending=state.pending, wordle_active=True)
    clock["now"] += 5.0
    assert state.pending is False
