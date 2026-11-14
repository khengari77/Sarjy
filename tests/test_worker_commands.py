"""The worker bypasses conversational generation for deterministic commands."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from app.domain.context import prompt_sha256
from livekit.agents import llm
from main import SarjyAgent
from test_generation import request


def _snapshot():
    payload = request()
    messages = [item.model_dump() for item in payload.messages]
    return {
        "turn_id": "turn",
        "context": {
            "model_request": payload.model_dump(mode="json"),
            "revision": 7,
            "messages": messages,
            "canonical_prompt_sha256": prompt_sha256(messages),
        },
    }


class ExplodingProvider:
    @asynccontextmanager
    async def chat(self, **_kwargs):
        raise AssertionError("conversational generation must not run for a command")
        yield


class ChattyProvider:
    def __init__(self):
        self.calls = 0

    @asynccontextmanager
    async def chat(self, **_kwargs):
        self.calls += 1

        async def chunks():
            yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="conversational output"))

        yield chunks()


def _agent(command, provider, published, paths, monkeypatch):
    monkeypatch.setattr(
        SarjyAgent, "session", property(lambda self: SimpleNamespace(current_speech=None, llm=provider))
    )

    def backend(http_request):
        paths.append(http_request.url.path)
        if http_request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if http_request.url.path.endswith("/internal/agent/command"):
            return httpx.Response(200, json={"command": command})
        if http_request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected worker request: {http_request.url.path}")

    async def publish(result):
        published.append(result)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(
                client,
                {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
                "Rules",
                publish=publish,
            )
            context = llm.ChatContext()
            context.add_message(role="user", content="Speak slower.")
            return [chunk async for chunk in agent.llm_node(context, [], None)]

    return asyncio.run(run())


def _command(kind, action, spoken):
    return {
        "request_id": "command:abc",
        "kind": kind,
        "action": action,
        "settings": None,
        "navigation": None,
        "changed": True,
        "spoken": spoken,
        "detail": None,
    }


@pytest.mark.parametrize(
    ("command", "spoken"),
    [
        (_command("executed", "SetCaptionsVisible", "Captions hidden."), "Captions hidden."),
        (_command("unchanged", "AdjustSpeakingSpeed", "I can't speak any slower."), "I can't speak any slower."),
        (
            _command("unsupported", None, "I can't search your memories by voice yet."),
            "I can't search your memories by voice yet.",
        ),
        (
            _command("failed", "SetCaptionsVisible", "I couldn't apply that. Nothing was changed."),
            "I couldn't apply that. Nothing was changed.",
        ),
    ],
)
def test_exact_and_unsupported_commands_skip_generation_and_publish_once(command, spoken, monkeypatch):
    published, paths = [], []
    chunks = _agent(command, ExplodingProvider(), published, paths, monkeypatch)
    assert [chunk.delta.content for chunk in chunks] == [spoken]
    assert published == [command]
    assert paths == ["/internal/agent/turn/start", "/internal/agent/command"]


def test_ordinary_conversation_still_generates(monkeypatch):
    paths = []
    provider = ChattyProvider()
    chunks = _agent(None, provider, [], paths, monkeypatch)
    assert [chunk.delta.content for chunk in chunks] == ["conversational output"]
    assert provider.calls == 1
    assert paths[0].endswith("/turn/start") and paths[1].endswith("/internal/agent/command")


def test_a_failed_command_lookup_degrades_to_conversation_without_claiming_success(monkeypatch):
    provider = ChattyProvider()
    monkeypatch.setattr(
        SarjyAgent, "session", property(lambda self: SimpleNamespace(current_speech=None, llm=provider))
    )
    paths = []

    def backend(http_request):
        paths.append(http_request.url.path)
        if http_request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if http_request.url.path.endswith("/internal/agent/command"):
            return httpx.Response(503, json={"detail": {"code": "COMMAND_UNAVAILABLE"}})
        if http_request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={})
        raise AssertionError(http_request.url.path)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(
                client,
                {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
                "Rules",
            )
            context = llm.ChatContext()
            context.add_message(role="user", content="Hide captions.")
            chunks = [chunk async for chunk in agent.llm_node(context, [], None)]
            assert [chunk.delta.content for chunk in chunks] == ["conversational output"]

    asyncio.run(run())
    assert "/internal/agent/command" in paths
