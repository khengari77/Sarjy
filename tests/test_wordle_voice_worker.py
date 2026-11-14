"""Worker-side evidence for #110: sarjy.say and command suppression."""

import asyncio
import json
from types import SimpleNamespace

import httpx
from livekit import rtc
from livekit.agents import llm
from main import RevealState, SarjyAgent, apply_control, perform_say, resolve_say_request
from wordle_commands import FIXTURES_PATH, command_kind, is_wordle_command

HUMAN = SimpleNamespace(kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD)
AGENT = SimpleNamespace(kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)


def fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]


def test_worker_classifier_matches_the_shared_fixture_corpus():
    for case in fixtures():
        assert is_wordle_command(case["text"]) is case["worker"], case["text"]
        if "workerPending" in case:
            assert is_wordle_command(case["text"], reveal_pending=True) is case["workerPending"], case["text"]


def test_worker_classifier_returns_the_client_command_kind():
    assert command_kind("Guess CRANE") == "guess"
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


def test_control_messages_only_flip_the_human_reveal_flag():
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
            agent = agent_for(client, is_command=is_wordle_command)
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
