import asyncio

from fallback import fallback_decision, next_attempt
from generation import GenerationEvent, GenerationFailure


def failure(code="timeout", *, status=None, partial=False):
    return GenerationEvent(
        "error",
        turn_id="turn",
        attempt_id="attempt",
        partial=partial,
        error_code=code,
        status_code=status,
    )


def test_transient_pre_output_failures_are_eligible():
    for code in ("timeout", "connection_error", "rate_limit", "provider_error"):
        assert fallback_decision(failure(code), emitted_output=False, fallback_configured=True).allowed
    assert fallback_decision(
        failure("provider_error", status=503),
        emitted_output=False,
        fallback_configured=True,
    ).allowed


def test_permanent_http_statuses_never_fall_back():
    for status in (400, 401, 402, 403, 404, 405, 409, 410, 422):
        assert not fallback_decision(
            failure("provider_error", status=status),
            emitted_output=False,
            fallback_configured=True,
        ).allowed
    for status in (408, 429, 500, 502, 503, 504):
        assert fallback_decision(
            failure("provider_error", status=status),
            emitted_output=False,
            fallback_configured=True,
        ).allowed


def test_output_partial_cancellation_and_configuration_deny_fallback():
    assert not fallback_decision(failure(), emitted_output=True, fallback_configured=True).allowed
    assert not fallback_decision(failure(partial=True), emitted_output=False, fallback_configured=True).allowed
    assert not fallback_decision(failure("cancelled"), emitted_output=False, fallback_configured=True).allowed
    assert not fallback_decision(
        failure("authentication", status=401),
        emitted_output=False,
        fallback_configured=True,
    ).allowed
    assert not fallback_decision(failure(), emitted_output=False, fallback_configured=False).allowed


def test_decision_reports_a_reason():
    allowed = fallback_decision(failure(), emitted_output=False, fallback_configured=True)
    denied = fallback_decision(failure("authentication"), emitted_output=False, fallback_configured=True)
    assert allowed.allowed and allowed.reason == "TRANSIENT_PROVIDER_FAILURE"
    assert not denied.allowed and denied.reason == "INELIGIBLE_FAILURE"


def test_next_attempt_changes_only_the_attempt_identity():
    class FakeRequest:
        turn_id = "turn"
        attempt_id = "old"

        def model_copy(self, update):
            clone = FakeRequest()
            clone.turn_id = self.turn_id
            clone.attempt_id = update["attempt_id"]
            return clone

    request = FakeRequest()
    updated = next_attempt(request, "new")
    assert updated is not request
    assert updated.turn_id == "turn"
    assert updated.attempt_id == "new"


class FakeAdapter:
    def __init__(self, events):
        self.events = events
        self.prompt_cache_hit_tokens = None
        self.prompt_cache_miss_tokens = None
        self.provider_connect_ms = None
        self.provider_headers_ms = None

    async def stream(self, request, tools, tool_choice=None):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


class FakeRequest:
    turn_id = "turn"
    attempt_id = "attempt"

    def model_copy(self, update):
        clone = FakeRequest()
        clone.turn_id = self.turn_id
        clone.attempt_id = update["attempt_id"]
        return clone


def run_fallback(
    monkeypatch, primary_events, fallback_events, *, reserve_error=None, denied_provider="deepseek", fallback=True
):
    import main as worker

    reserved = []

    agent = type("FakeAgent", (), {})()
    agent.pipeline = {"llm": {"provider": "deepseek_direct"}}
    agent.session = type("Session", (), {"llm": "primary"})()
    agent.fallback_llm = "fallback" if fallback else None
    agent._require_openai_bounds = lambda request, tools: None
    agent._require_deepseek_bounds = lambda request: None
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    agent._stream_with_fallback = worker.SarjyAgent._stream_with_fallback.__get__(agent)

    async def reserve(provider, characters=0, report=None):
        reserved.append(provider)
        if reserve_error is not None and provider == denied_provider:
            raise reserve_error

    agent.reserve = reserve

    adapters = {
        "primary": FakeAdapter(primary_events),
        "fallback": FakeAdapter(fallback_events),
    }
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: adapters[provider])
    monkeypatch.setattr(worker, "livekit_chunk", lambda event: event)

    async def collect():
        return [event async for event in agent._stream_with_fallback(FakeRequest(), [], None)]

    try:
        events = asyncio.run(collect())
        return events, reserved, None
    except Exception as exc:
        return [], reserved, exc


def test_pre_output_failure_reserves_once_and_streams_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [GenerationFailure(failure("timeout"))],
        [
            GenerationEvent("text", turn_id="turn", attempt_id="attempt", text="fallback"),
            GenerationEvent("completed", turn_id="turn", attempt_id="attempt"),
        ],
    )
    assert error is None
    assert reserved == ["deepseek", "openai"]
    assert [event.kind for event in events] == ["text", "completed"]


def test_primary_success_does_not_reserve_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [
            GenerationEvent("text", turn_id="turn", attempt_id="attempt", text="primary"),
            GenerationEvent("completed", turn_id="turn", attempt_id="attempt"),
        ],
        [],
    )
    assert error is None
    assert reserved == ["deepseek"]
    assert [event.kind for event in events] == ["text", "completed"]


def test_emitted_output_blocks_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [
            GenerationEvent("text", turn_id="turn", attempt_id="attempt", text="partial"),
            GenerationFailure(failure("timeout", partial=True)),
        ],
        [GenerationEvent("completed", turn_id="turn", attempt_id="attempt")],
    )
    assert events == []
    assert reserved == ["deepseek"]
    assert isinstance(error, GenerationFailure)


def test_primary_budget_denial_forwards_nothing(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [GenerationFailure(failure("timeout"))],
        [GenerationEvent("completed", turn_id="turn", attempt_id="attempt")],
        reserve_error=ValueError("DAILY_BUDGET_EXHAUSTED"),
    )
    assert events == []
    assert reserved == ["deepseek"]
    assert isinstance(error, ValueError)


def test_attempt_deadline_raises_timeout_generation_failure(monkeypatch):
    import main as worker

    class SlowAdapter:
        async def stream(self, request, tools, tool_choice=None):
            await asyncio.sleep(1)
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    agent = type("FakeAgent", (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, "GENERATION_ATTEMPT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: SlowAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt("provider", "deepseek_direct", FakeRequest(), [], None)]

    try:
        asyncio.run(collect())
    except GenerationFailure as exc:
        assert exc.event.error_code == "timeout"
        assert exc.event.partial is False
    else:
        raise AssertionError("expected a timeout GenerationFailure")


def test_first_event_deadline_raises_timeout_before_any_output(monkeypatch):
    import main as worker

    class StalledAdapter:
        async def stream(self, request, tools, tool_choice=None):
            await asyncio.sleep(1)
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    agent = type("FakeAgent", (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, "FIRST_EVENT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "GENERATION_ATTEMPT_SECONDS", 5.0)
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: StalledAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt("provider", "deepseek_direct", FakeRequest(), [], None)]

    try:
        asyncio.run(collect())
    except GenerationFailure as exc:
        assert exc.event.error_code == "timeout"
        assert exc.event.partial is False
    else:
        raise AssertionError("expected a first-event timeout GenerationFailure")


def test_first_event_earns_the_whole_attempt_deadline(monkeypatch):
    import main as worker

    class GappedAdapter:
        prompt_cache_hit_tokens = None
        prompt_cache_miss_tokens = None

        async def stream(self, request, tools, tool_choice=None):
            yield GenerationEvent("text", turn_id="turn", attempt_id="attempt", text="first")
            await asyncio.sleep(0.05)
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    agent = type("FakeAgent", (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, "FIRST_EVENT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "GENERATION_ATTEMPT_SECONDS", 5.0)
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: GappedAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt("provider", "deepseek_direct", FakeRequest(), [], None)]

    assert [event.kind for event in asyncio.run(collect())] == ["text", "completed"]


def test_a_streaming_tool_call_satisfies_the_first_event_deadline(monkeypatch):
    """The adapter releases tool calls only at the end; its first raw chunk must count."""
    import main as worker

    class ToolAdapter:
        prompt_cache_hit_tokens = None
        prompt_cache_miss_tokens = None
        on_first_chunk = None

        async def stream(self, request, tools, tool_choice=None):
            self.on_first_chunk()  # the provider started streaming a tool call
            await asyncio.sleep(0.05)  # longer than the first-event deadline
            yield GenerationEvent("tool", turn_id="turn", attempt_id="attempt")
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    agent = type("FakeAgent", (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, "FIRST_EVENT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "GENERATION_ATTEMPT_SECONDS", 5.0)
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: ToolAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt("provider", "deepseek_direct", FakeRequest(), [], None)]

    assert [event.kind for event in asyncio.run(collect())] == ["tool", "completed"]


def test_stalled_primary_falls_back_after_first_event_deadline(monkeypatch):
    import main as worker

    class StalledAdapter:
        async def stream(self, request, tools, tool_choice=None):
            await asyncio.sleep(1)
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    class AnsweringAdapter:
        prompt_cache_hit_tokens = None
        prompt_cache_miss_tokens = None

        async def stream(self, request, tools, tool_choice=None):
            yield GenerationEvent("text", turn_id="turn", attempt_id="attempt", text="fallback")
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    reserved = []
    agent = type("FakeAgent", (), {})()
    agent.pipeline = {"llm": {"provider": "deepseek_direct"}}
    agent.session = type("Session", (), {"llm": "primary"})()
    agent.fallback_llm = "fallback"
    agent._require_openai_bounds = lambda request, tools: None
    agent._require_deepseek_bounds = lambda request: None
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    agent._stream_with_fallback = worker.SarjyAgent._stream_with_fallback.__get__(agent)

    async def reserve(provider, characters=0, report=None):
        reserved.append(provider)

    agent.reserve = reserve
    adapters = {"primary": StalledAdapter(), "fallback": AnsweringAdapter()}
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: adapters[provider])
    monkeypatch.setattr(worker, "livekit_chunk", lambda event: event)
    monkeypatch.setattr(worker, "FIRST_EVENT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "GENERATION_ATTEMPT_SECONDS", 5.0)

    async def collect():
        return [event async for event in agent._stream_with_fallback(FakeRequest(), [], None)]

    events = asyncio.run(collect())
    assert reserved == ["deepseek", "openai"]
    assert [event.kind for event in events] == ["text", "completed"]
    assert events[0].text == "fallback"


def test_fallback_budget_denial_never_invokes_fallback(monkeypatch):
    calls = []
    original = FakeAdapter.stream

    async def observed(self, request, tools, tool_choice=None):
        calls.append(self)
        async for event in original(self, request, tools, tool_choice):
            yield event

    monkeypatch.setattr(FakeAdapter, "stream", observed)
    events, reserved, error = run_fallback(
        monkeypatch,
        [GenerationFailure(failure("timeout"))],
        [GenerationEvent("completed", turn_id="turn", attempt_id="attempt")],
        reserve_error=ValueError("DAILY_BUDGET_EXHAUSTED"),
        denied_provider="openai",
    )
    assert events == []
    assert reserved == ["deepseek", "openai"]
    assert len(calls) == 1
    assert isinstance(error, ValueError)


def test_a_deadline_cancellation_mapped_by_the_adapter_still_becomes_a_timeout(monkeypatch):
    """The real adapter re-raises cancellation as GenerationCancelled; Python 3.12 would not convert it."""
    import main as worker
    from generation import GenerationCancelled

    class MappingAdapter:
        prompt_cache_hit_tokens = None
        prompt_cache_miss_tokens = None
        on_first_chunk = None

        async def stream(self, request, tools, tool_choice=None):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError as exc:  # mirrors ProviderAdapter.stream
                raise GenerationCancelled(GenerationEvent("cancelled", turn_id="turn", attempt_id="attempt")) from exc
            yield GenerationEvent("completed", turn_id="turn", attempt_id="attempt")

    agent = type("FakeAgent", (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, "FIRST_EVENT_SECONDS", 0.02)
    monkeypatch.setattr(worker, "adapter_for", lambda provider, kind: MappingAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt("provider", "deepseek_direct", FakeRequest(), [], None)]

    try:
        asyncio.run(collect())
    except GenerationFailure as exc:
        assert exc.event.error_code == "timeout" and exc.event.partial is False
    else:
        raise AssertionError("expected a timeout GenerationFailure")
