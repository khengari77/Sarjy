import asyncio
import json

import httpx
import pytest
from app.domain.provider_bounds import (
    CONTINUATION_TOOL_ROUND_BYTES,
    MAX_CONTINUATION_TOOL_ROUNDS,
    TOOL_SCHEMA_RESERVE_BYTES,
    admission_reserve_bytes,
    provider_request_bytes,
)
from livekit.agents import Agent, StopResponse, llm
from livekit.agents.llm.utils import build_legacy_openai_schema
from main import RequestRefused, SarjyAgent


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


def test_weather_schema_fits_its_reserve():
    async def run():
        async with httpx.AsyncClient(base_url="http://test") as client:
            agent = agent_for(client)
            schema = build_legacy_openai_schema(agent.get_current_weather)["function"]
            assert provider_request_bytes([], tools=[schema]) <= TOOL_SCHEMA_RESERVE_BYTES

    asyncio.run(run())


def test_continuation_reserve_covers_two_tool_rounds_and_the_output_cap():
    reserve = admission_reserve_bytes(1024)
    assert (
        reserve == TOOL_SCHEMA_RESERVE_BYTES + 1024 * 4 + CONTINUATION_TOOL_ROUND_BYTES * MAX_CONTINUATION_TOOL_ROUNDS
    )
    assert reserve < 16 * 1024


def test_agent_uses_the_backend_instructions_without_rewriting_them():
    async def run():
        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ) as client:
            agent = agent_for(client, instructions="Tier directive from the API.")
            assert agent.instructions == "Tier directive from the API."

    asyncio.run(run())


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

    agent = SimpleNamespace(notify=notify)
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


class PlayoutHandle:
    def __init__(self, text, *, failed=False, interrupted=False):
        self.chat_items = [llm.ChatMessage(role="assistant", content=[text])]
        self.interrupted = interrupted
        self._failed = failed

    async def wait_for_playout(self):
        return None

    def exception(self):
        return RuntimeError("continuation refused") if self._failed else None


def test_compile_maps_413_to_notification_and_refusal():
    notifications = []

    def backend(request):
        assert request.url.path == "/internal/agent/turn/start"
        return httpx.Response(413, json={"detail": {"code": "CONTEXT_BUDGET_EXCEEDED"}})

    async def notify(code):
        notifications.append(code)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client, notify=notify)
            agent.current_turn = "stale-turn"
            message = llm.ChatMessage(role="user", content=["hello"])
            with pytest.raises(RequestRefused):
                await agent.compile(message)
            assert agent.current_turn is None

    asyncio.run(run())
    assert notifications == ["CONTEXT_BUDGET_EXCEEDED"]


def test_refused_turn_stops_before_generation():
    async def refuse(_message):
        raise RequestRefused("CONTEXT_BUDGET_EXCEEDED")

    from types import SimpleNamespace

    agent = SimpleNamespace(compile=refuse)

    async def run():
        with pytest.raises(StopResponse):
            await SarjyAgent.on_user_turn_completed(agent, None, object())

    asyncio.run(run())


def test_spoken_text_survives_a_later_continuation_refusal():
    finalizes = []

    def backend(request):
        finalizes.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "reply"})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client)
            await agent._persist_playout("turn", PlayoutHandle("Let me check the weather.", failed=True))

    asyncio.run(run())
    assert finalizes == [{"turn_id": "turn", "text": "Let me check the weather.", "status": "failed"}]


def test_empty_refused_reply_is_not_persisted():
    finalizes = []

    def backend(request):
        finalizes.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "reply"})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = agent_for(client)
            await agent._persist_playout("turn", PlayoutHandle("", failed=True))

    asyncio.run(run())
    assert finalizes == []


@pytest.mark.parametrize("content", ["y" * 4000, "ا" * 2000, '"\\\n' * 700])
def test_two_tool_rounds_fit_the_admission_reserve(content):
    from types import SimpleNamespace

    from app.domain.contracts import AssistantContinuation, ModelToolItem, ToolSpec
    from test_generation import request

    async def schema_for_agent():
        async with httpx.AsyncClient(base_url="http://test") as client:
            return build_legacy_openai_schema(agent_for(client).get_current_weather)["function"]

    schema = asyncio.run(schema_for_agent())
    continuation = [AssistantContinuation(content=content, interrupted=False).model_dump(mode="json")]
    for city in ("Tripoli", "Benghazi"):
        continuation.append(
            ModelToolItem(
                kind="call", call_id=city, name="get_current_weather", content=json.dumps({"city": city})
            ).model_dump(mode="json")
        )
        continuation.append(
            ModelToolItem(
                kind="result",
                call_id=city,
                name="get_current_weather",
                content=json.dumps({"city": city, "current": {"temperature_2m": 23}}),
            ).model_dump(mode="json")
        )
    data = request().model_dump(mode="json")
    data["instructions"] = "rules"
    data["messages"] = [{"role": "system", "content": "rules"}, {"role": "user", "content": "x" * 9000}]
    data["tool_history"] = continuation
    data["tools"] = [
        ToolSpec(
            name=schema["name"], description=schema.get("description", ""), parameters=schema["parameters"]
        ).model_dump(mode="json")
    ]
    assembled = type(request()).model_validate(data)
    payload = assembled.model_dump(mode="json")
    size = provider_request_bytes(payload["messages"], payload["tool_history"], payload["tools"])
    assert size <= 16 * 1024

    async def notify(_code):
        return None

    agent = SimpleNamespace(notify=notify)
    agent._notify = SarjyAgent._notify.__get__(agent)
    agent._require_deepseek_bounds = SarjyAgent._require_deepseek_bounds.__get__(agent)
    asyncio.run(agent._require_deepseek_bounds(assembled))
