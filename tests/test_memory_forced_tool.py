"""Model-decided memory operations: Jev's memory_op forces the matching tool once; regexes are a strict fast path."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.domain.memory_capture import parse_strict_capture
from app.domain.ui_routing import UiAnswer, UiClassification
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, configured, turn


def classified(memory_op="none", p=0.97):
    none = UiAnswer(choice="none", probability=0.97)
    return UiClassification(
        route=UiAnswer(choice="conversation", probability=0.6),
        family=none,
        theme=none,
        captions=none,
        speed=none,
        voice=none,
        memory_op=UiAnswer(choice=memory_op, probability=p),
    )


class FakeClassifier:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def classify(self, *, request_id, instruction):
        self.calls.append(instruction)
        return self.result


@pytest.mark.parametrize(
    "text",
    [
        "Remember to buy milk",
        "Don't forget to call mom tomorrow",
        "Remember when we talked about Rome",
        "Remember me",
        "Remember my name. My name is Abdur Rahman.",
        "Save this fact. My name is Abdurrahman.",
        "I live in Tripoli",
        "I'm from Tripoli",
        "Remember that?",
    ],
)
def test_strict_fast_path_never_interprets_free_form_requests(text):
    assert parse_strict_capture(text) is None


def test_strict_fast_path_keeps_remember_that_clause():
    capture = parse_strict_capture("Remember that I'm vegetarian")
    assert capture is not None and capture.explicit
    assert "vegetarian" in capture.text
    assert parse_strict_capture("Please don't forget that my name is Sam") is not None


def test_turn_start_with_classifier_makes_no_free_form_proposal_but_keeps_exact_path(tmp_path):
    file, settings = configured(tmp_path)
    fake = FakeClassifier(classified())
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder(), ui_classifier=fake)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        for index, text in enumerate(
            ("Remember to buy milk", "Remember when we talked about Rome", "Remember me", "I'm from Tripoli")
        ):
            assert turn(client, headers, text, f"free-{index}").json()["memory_proposal"] is None
        started = turn(client, headers, "Remember that I like green tea", "exact").json()
        assert started["memory_proposal"] is not None
        assert started["memory_proposal"]["text"] == "I like green tea"
    assert fake.calls == []


def test_turn_start_without_classifier_is_unchanged(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        assert turn(client, headers, "Remember to buy milk", "r").json()["memory_proposal"] is not None


def command(client, headers, turn_id):
    return client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})


def test_command_shares_one_classification_and_returns_memory_op(tmp_path):
    file, settings = configured(tmp_path)
    fake = FakeClassifier(classified("remember"))
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder(), ui_classifier=fake)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        started = turn(client, headers, "Could you keep in mind that I'm vegetarian?", "a").json()
        body = command(client, headers, started["turn_id"]).json()
    assert body["command"] is None
    assert body["memory_op"] == {"choice": "remember", "probability": 0.97}
    assert fake.calls == ["Could you keep in mind that I'm vegetarian?"]


def test_command_without_classifier_or_after_exact_command_has_no_memory_op(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        started = turn(client, headers, "Scrap what I said about tea", "a").json()
        assert command(client, headers, started["turn_id"]).json()["memory_op"] is None
    fake = FakeClassifier(classified("remember"))
    file, settings = configured(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder(), ui_classifier=fake)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        started = turn(client, headers, "switch to dark mode", "b").json()
        body = command(client, headers, started["turn_id"]).json()
    assert body["command"] is not None and body["memory_op"] is None
    assert fake.calls == []


# Worker ----------------------------------------------------------------------------------------------------------


def _agent_and_chat(monkeypatch, memory_op, text="Remember my name. My name is Abdur Rahman."):
    from main import SarjyAgent
    from test_memory_capture import _question_context, _session_with

    monkeypatch.setattr(SarjyAgent, "session", property(lambda self: _session_with(None)))
    turn_id, context = _question_context()

    def backend(request):
        if request.url.path.endswith("/command"):
            return httpx.Response(200, json={"command": None, "memory_op": memory_op})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend))
    agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
    agent.prepared["m"] = {"turn_id": turn_id, "context": context}
    agent.current_turn = turn_id
    agent.memory_message = "m"
    return agent, client


def _drive(agent, tools_names):
    from livekit.agents import llm

    seen = []

    async def stream(request, tools, tool_choice, report=None):
        seen.append(tool_choice)
        yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content=""))

    agent._stream_with_fallback = stream
    chat = llm.ChatContext()
    chat.add_message(role="user", content="x", id="m")

    async def run():
        for _ in range(2):  # first inference, then the continuation after the tool result
            _ = [chunk async for chunk in agent.llm_node(chat, tools_names, None)]

    asyncio.run(run())
    return seen


def _tools(agent):
    return [agent.propose_memory, agent.propose_forget, agent.recall]


@pytest.mark.parametrize(
    ("op", "tool"),
    [("remember", "propose_memory"), ("forget", "propose_forget"), ("recall", "recall")],
)
def test_tool_is_forced_on_first_inference_only(monkeypatch, op, tool):
    agent, client = _agent_and_chat(monkeypatch, {"choice": op, "probability": 0.93})
    seen = _drive(agent, _tools(agent))
    # An unforced reply may have been started, and dropped, before the answer arrived.
    assert seen[-2:] == [{"type": "function", "function": {"name": tool}}, None] and seen[:-2] in ([], [None])
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "memory_op",
    [
        {"choice": "restore", "probability": 0.99},
        {"choice": "none", "probability": 0.99},
        {"choice": "remember", "probability": 0.84},
        {"choice": "remember", "probability": True},
        {"choice": "bogus", "probability": 0.99},
        None,
    ],
)
def test_nothing_is_forced_for_restore_none_low_confidence_or_missing(monkeypatch, memory_op):
    agent, client = _agent_and_chat(monkeypatch, memory_op)
    assert _drive(agent, _tools(agent)) == [None, None]
    asyncio.run(client.aclose())


def test_forced_choice_is_skipped_when_tool_unavailable_or_decision_turn(monkeypatch):
    agent, client = _agent_and_chat(monkeypatch, {"choice": "recall", "probability": 0.95})
    assert _drive(agent, [agent.propose_memory]) == [None, None]
    agent2, client2 = _agent_and_chat(monkeypatch, {"choice": "remember", "probability": 0.95})
    agent2.decision_turn = agent2.current_turn
    assert _drive(agent2, _tools(agent2)) == [None, None]
    asyncio.run(client.aclose())
    asyncio.run(client2.aclose())


def test_forced_inference_emits_no_prose_before_the_tool_call(monkeypatch):
    """The forced call is the first and only output of the first inference: the worker adds no text of its own."""
    from livekit.agents import llm

    agent, client = _agent_and_chat(monkeypatch, {"choice": "remember", "probability": 0.95})
    emitted = []

    async def stream(request, tools, tool_choice, report=None):
        if tool_choice is None:
            # The reply started before the answer was known; it must not be heard.
            yield llm.ChatChunk(id="early", delta=llm.ChoiceDelta(content="I will remember that."))
            return
        assert tool_choice == {"type": "function", "function": {"name": "propose_memory"}}
        yield llm.ChatChunk(
            id="r",
            delta=llm.ChoiceDelta(
                tool_calls=[
                    llm.FunctionToolCall(
                        arguments=json.dumps({"fact": "my name is Abdur Rahman"}), name="propose_memory", call_id="c1"
                    )
                ]
            ),
        )

    agent._stream_with_fallback = stream
    chat = llm.ChatContext()
    chat.add_message(role="user", content="x", id="m")

    async def run():
        async for chunk in agent.llm_node(chat, _tools(agent), None):
            emitted.append(chunk)

    asyncio.run(run())
    assert len(emitted) == 1 and not emitted[0].delta.content and emitted[0].delta.tool_calls
    asyncio.run(client.aclose())


def test_command_failure_means_no_forcing(monkeypatch):
    agent, client = _agent_and_chat(monkeypatch, {"choice": "remember", "probability": 0.95})
    agent.commands["m"] = None
    agent.memory_ops.clear()
    assert _drive(agent, _tools(agent)) == [None, None]
    asyncio.run(client.aclose())
    assert SimpleNamespace  # keep import used
