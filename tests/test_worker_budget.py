import asyncio

import httpx
import pytest
from livekit.agents import Agent
from main import SarjyAgent, deepseek_request_within_bounds, openai_request_within_bounds


def agent_for(client, max_output_tokens=128, notify=None, instructions="Authoritative instructions."):
    return SarjyAgent(
        client,
        {"llm": {"provider": "openai_direct"}, "max_output_tokens": max_output_tokens},
        instructions=instructions,
        notify=notify,
    )


def test_tts_reserves_before_text_reaches_provider(monkeypatch):
    events = []

    def backend(request):
        assert request.url.path == "/internal/development/reserve"
        events.append("reserved")
        return httpx.Response(200, json={})

    async def synthesize(agent, text, model_settings):
        async for part in text:
            assert events[-1] == "reserved"
            events.append(part)
            yield "audio"

    monkeypatch.setattr(Agent.default, "tts_node", synthesize)

    async def text():
        yield "مرحبا"

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client)
            assert [frame async for frame in agent.tts_node(text(), None)] == ["audio"]

    asyncio.run(run())
    assert events == ["reserved", "مرحبا"]


@pytest.mark.parametrize("max_output_tokens", [64, 1024])
def test_exhausted_budget_does_not_forward_synthesis_text(monkeypatch, max_output_tokens):
    """Tier 1 and tier 5 both stop before TTS when the speech allowance is spent."""
    forwarded = []
    notifications = []

    async def synthesize(agent, text, model_settings):
        async for part in text:
            forwarded.append(part)
            yield "audio"

    monkeypatch.setattr(Agent.default, "tts_node", synthesize)

    async def text():
        yield "Hello"

    async def notify(code):
        notifications.append(code)

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(429, json={}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            agent = agent_for(client, max_output_tokens=max_output_tokens, notify=notify)
            with pytest.raises(httpx.HTTPStatusError):
                [frame async for frame in agent.tts_node(text(), None)]

    asyncio.run(run())
    assert forwarded == []
    assert notifications == ["DAILY_BUDGET_EXHAUSTED"]


def test_request_bounds_accept_every_published_tier_and_reject_the_rest():
    for cap in (64, 128, 256, 512, 1024):
        assert openai_request_within_bounds(b"{}", [], cap)
    assert not openai_request_within_bounds(b"{}", [], 1025)
    assert not openai_request_within_bounds(b"x" * 64001, [], 128)
    assert not openai_request_within_bounds(b"{}", [object(), object()], 128)


def test_agent_uses_the_backend_instructions_without_rewriting_them():
    async def run():
        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ) as client:
            agent = agent_for(client, instructions="Tier directive from the API.")
            assert agent.instructions == "Tier directive from the API."

    asyncio.run(run())


def test_deepseek_request_bound():
    assert deepseek_request_within_bounds(b"{}")
    assert deepseek_request_within_bounds(b"x" * (16 * 1024))
    assert not deepseek_request_within_bounds(b"x" * (16 * 1024 + 1))


def test_oversized_deepseek_continuation_stops_before_reservation_or_provider():
    from types import SimpleNamespace

    from test_generation import request

    calls = []
    notifications = []

    async def reserve(provider):
        calls.append("reserve")

    async def stream(*args):
        calls.append("provider")
        yield None

    async def notify(code):
        notifications.append(code)

    agent = SimpleNamespace(
        pipeline={"llm": {"provider": "deepseek_direct"}},
        reserve=reserve,
        _stream_attempt=stream,
        notify=notify,
        refused_turns=set(),
        fallback_llm=object(),
    )
    agent._notify = SarjyAgent._notify.__get__(agent)
    agent._require_deepseek_bounds = SarjyAgent._require_deepseek_bounds.__get__(agent)
    data = request().model_dump(mode="json")
    data["tool_history"] = [{"kind": "assistant", "content": "x" * (16 * 1024 + 1)}]
    oversized = type(request()).model_validate(data)

    async def run():
        with pytest.raises(ValueError, match="PROVIDER_REQUEST_TOO_LARGE"):
            _ = [chunk async for chunk in SarjyAgent._stream_with_fallback(agent, oversized, [], None)]

    asyncio.run(run())
    assert calls == []
    assert notifications == ["PROVIDER_REQUEST_TOO_LARGE"]


def test_deepseek_admission_measures_utf8_bytes_not_characters():
    from types import SimpleNamespace

    from app.domain.provider_bounds import ProviderRequestTooLarge
    from test_generation import request

    notifications = []

    async def notify(code):
        notifications.append(code)

    agent = SimpleNamespace(notify=notify, refused_turns=set())
    agent._notify = SarjyAgent._notify.__get__(agent)
    agent._require_deepseek_bounds = SarjyAgent._require_deepseek_bounds.__get__(agent)
    data = request().model_dump(mode="json")
    data["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "ا" * 7000},
    ]
    data["instructions"] = "rules"
    fitting = type(request()).model_validate(data)
    asyncio.run(agent._require_deepseek_bounds(fitting))
    assert notifications == []

    data["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "ا" * 9000},
    ]
    oversized = type(request()).model_validate(data)
    with pytest.raises(ProviderRequestTooLarge):
        asyncio.run(agent._require_deepseek_bounds(oversized))
    assert notifications == ["PROVIDER_REQUEST_TOO_LARGE"]
    assert oversized.turn_id in agent.refused_turns


def test_shared_accounting_matches_worker_envelope():
    import json as json_module

    from app.domain.provider_bounds import provider_request_bytes
    from test_generation import request

    payload = request().model_dump(mode="json")
    expected = len(
        json_module.dumps(
            {key: payload[key] for key in ("messages", "tool_history", "tools")}, ensure_ascii=False
        ).encode()
    )
    assert provider_request_bytes(payload["messages"], payload["tool_history"], payload["tools"]) == expected
