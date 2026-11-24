"""A tool call starts the continuation's budget reservation before the tool result exists."""

import asyncio

import httpx
from generation import GenerationEvent
from main import SarjyAgent
from test_generation import request


class ToolAdapter:
    def __init__(self, kind):
        self.kind = kind
        self.prompt_cache_hit_tokens = None
        self.prompt_cache_miss_tokens = None
        self.provider_connect_ms = None
        self.provider_headers_ms = None
        self.on_first_chunk = None

    async def stream(self, request, tools=(), tool_choice=None):
        identity = {"turn_id": request.turn_id, "attempt_id": request.attempt_id}
        if self.kind == "tool":
            yield GenerationEvent("tool", **identity, call_id="call-1", name="get_current_weather", arguments="{}")
        else:
            yield GenerationEvent("text", **identity, text="Hello")
        yield GenerationEvent("completed", **identity)


def _agent(client):
    return SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")


def _run(kind, monkeypatch):
    paths = []

    def backend(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    monkeypatch.setattr("main.adapter_for", lambda provider, provider_kind: ToolAdapter(kind))

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            agent.current_turn = "turn"
            agent.reserving_message = "message"
            agent.turn_stages["turn"] = {}
            events = [event async for event in agent._stream_attempt(object(), "openai_direct", request(), [], None)]
            await asyncio.gather(*agent.prepayments, return_exceptions=True)
            before_continuation = list(paths)
            prepaid = agent.prepaid_generation
            if prepaid is not None and prepaid[0] == "openai":
                await agent.reserve("openai")
            return agent, before_continuation, paths, events, prepaid

    return asyncio.run(run())


def test_a_tool_event_starts_the_continuation_reservation(monkeypatch):
    agent, before_continuation, paths, events, prepaid = _run("tool", monkeypatch)

    assert [event.kind for event in events] == ["tool", "completed"]
    assert before_continuation == ["/internal/development/reserve"]
    assert prepaid is not None
    provider, message_id, task = prepaid
    assert (provider, message_id) == ("openai", "message")
    assert task.done()
    # The continuation took the prepayment instead of issuing another request.
    assert paths == ["/internal/development/reserve"]
    assert agent.prepaid_generation is None


def test_a_text_only_attempt_starts_no_continuation_reservation(monkeypatch):
    agent, before_continuation, paths, events, prepaid = _run("text", monkeypatch)

    assert [event.kind for event in events] == ["text", "completed"]
    assert before_continuation == paths == []
    assert prepaid is None
    assert agent.prepaid_generation is None
