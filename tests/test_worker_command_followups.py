"""Review follow-ups: command retry, resync, consent precedence and tab parity."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from livekit.agents import llm
from main import SarjyAgent
from test_worker_commands import ChattyProvider, ExplodingProvider, _snapshot


def _command(spoken="Speaking slower.", action="AdjustSpeakingSpeed"):
    return {
        "request_id": "command:abc",
        "kind": "executed",
        "action": action,
        "settings": None,
        "navigation": None,
        "changed": True,
        "spoken": spoken,
        "detail": None,
    }


def _runner(agent, backend, text):
    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent.backend = client
            context = llm.ChatContext()
            context.add_message(role="user", content=text)
            return [chunk async for chunk in agent.llm_node(context, [], None)]

    return asyncio.run(run())


def _bind_session(monkeypatch, provider):
    monkeypatch.setattr(
        SarjyAgent, "session", property(lambda self: SimpleNamespace(current_speech=None, llm=provider))
    )


def test_a_transient_command_lookup_is_retried_once(monkeypatch):
    _bind_session(monkeypatch, ExplodingProvider())
    published, paths = [], []
    attempts = 0

    def backend(request):
        nonlocal attempts
        paths.append(request.url.path)
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if request.url.path.endswith("/internal/agent/command"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, json={"detail": {"code": "COMMAND_UNAVAILABLE"}})
            return httpx.Response(200, json={"command": _command()})
        raise AssertionError(request.url.path)

    async def publish(result):
        published.append(result)

    agent = SarjyAgent(
        None,
        {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
        "Rules",
        publish=publish,
    )
    chunks = _runner(agent, backend, "Speak slower.")

    assert attempts == 2
    assert [chunk.delta.content for chunk in chunks] == ["Speaking slower."]
    assert published == [_command()]
    assert paths.count("/internal/agent/command") == 2


def test_a_persistent_command_lookup_failure_falls_through_to_conversation(monkeypatch):
    provider = ChattyProvider()
    _bind_session(monkeypatch, provider)
    paths, attempts = [], 0

    def backend(request):
        nonlocal attempts
        paths.append(request.url.path)
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if request.url.path.endswith("/internal/agent/command"):
            attempts += 1
            return httpx.Response(500, json={"detail": {"code": "COMMAND_UNAVAILABLE"}})
        if request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    agent = SarjyAgent(None, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")
    chunks = _runner(agent, backend, "Hide captions.")

    assert attempts == 2
    assert [chunk.delta.content for chunk in chunks] == ["conversational output"]
    assert provider.calls == 1


def test_lost_publication_sets_resync_and_the_next_turn_publishes_it_once(monkeypatch):
    _bind_session(monkeypatch, ExplodingProvider())
    published = []

    def backend(request):
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if request.url.path.endswith("/internal/agent/command"):
            return httpx.Response(200, json={"command": _command("Captions hidden.", "SetCaptionsVisible")})
        raise AssertionError(request.url.path)

    async def failing_publish(result):
        published.append(result)
        raise RuntimeError("data channel unavailable")

    async def working_publish(result):
        published.append(result)

    agent = SarjyAgent(
        None,
        {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
        "Rules",
        publish=failing_publish,
    )

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent.backend = client
            context = llm.ChatContext()
            context.add_message(role="user", content="Hide captions.")
            chunks = [chunk async for chunk in agent.llm_node(context, [], None)]
            assert [chunk.delta.content for chunk in chunks] == ["Captions hidden."]
            # The command result was attempted twice, then a resync was scheduled.
            assert len(published) == 2
            assert agent.command_resync_required is True

            agent.publish = working_publish
            await agent.compile(SimpleNamespace(id="next-turn", text_content="Hello"))
            await agent.compile(SimpleNamespace(id="next-turn", text_content="Hello"))

    asyncio.run(run())
    assert published[-1] == {"resync": True}
    assert published.count({"resync": True}) == 1
    assert agent.command_resync_required is False


def test_a_pending_consent_decision_owns_the_utterance(monkeypatch):
    captured = {}

    class Provider:
        calls = 0

        @asynccontextmanager
        async def chat(self, *, chat_ctx, **_kwargs):
            Provider.calls += 1
            captured["text"] = "\n".join(item.text_content or "" for item in chat_ctx.items)

            async def chunks():
                yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="Ok, I won't save it."))

            yield chunks()

    _bind_session(monkeypatch, Provider())
    paths = []

    def backend(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if request.url.path.endswith("/memory/confirm"):
            return httpx.Response(200, json={"status": "discarded"})
        if request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    agent = SarjyAgent(None, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")
    agent.memory_proposal = {
        "token": "already-delivered-question-token",
        "confirmation_id": "old",
        "confirmation_revision": 1,
    }
    agent.memory_prompted_key = agent._proposal_key(agent.memory_proposal)

    chunks = _runner(agent, backend, "Forget it.")

    assert Provider.calls == 1
    assert [chunk.delta.content for chunk in chunks] == ["Ok, I won't save it."]
    assert not any(path.endswith("/internal/agent/command") for path in paths)
    assert "user declined" in captured["text"]
    assert "can't change or delete memories" not in captured["text"]
    assert agent.command_blocked_turn is not None


def test_a_pending_consent_decision_never_calls_the_command_endpoint(monkeypatch):
    """Regression guard: the command path is skipped, not merely ignored."""
    _bind_session(monkeypatch, ChattyProvider())
    paths = []

    def backend(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if request.url.path.endswith("/memory/confirm"):
            return httpx.Response(200, json={"status": "discarded"})
        if request.url.path.endswith("/reserve"):
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    agent = SarjyAgent(None, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")
    agent.memory_proposal = {"token": "t", "confirmation_id": "old", "confirmation_revision": 1}
    agent.memory_prompted_key = agent._proposal_key(agent.memory_proposal)

    _runner(agent, backend, "Forget it.")
    assert not any(path.endswith("/internal/agent/command") for path in paths)
