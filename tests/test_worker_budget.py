import asyncio

import httpx
import pytest
from livekit.agents import Agent

from main import SarjyAgent, openai_request_within_bounds


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
