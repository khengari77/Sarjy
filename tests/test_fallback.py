import asyncio

from generation import GenerationEvent, GenerationFailure
from fallback import fallback_decision, fallback_eligible, next_attempt


def failure(code='timeout', *, status=None, partial=False):
    return GenerationEvent(
        'error', turn_id='turn', attempt_id='attempt',
        partial=partial, error_code=code, status_code=status,
    )


def test_transient_pre_output_failures_are_eligible():
    for code in ('timeout', 'connection_error', 'rate_limit', 'provider_error'):
        assert fallback_eligible(
            failure(code), emitted_output=False, fallback_configured=True
        )
    assert fallback_eligible(
        failure('provider_error', status=503), emitted_output=False,
        fallback_configured=True,
    )


def test_output_partial_cancellation_and_configuration_deny_fallback():
    assert not fallback_eligible(
        failure(), emitted_output=True, fallback_configured=True
    )
    assert not fallback_eligible(
        failure(partial=True), emitted_output=False, fallback_configured=True
    )
    assert not fallback_eligible(
        failure('cancelled'), emitted_output=False, fallback_configured=True
    )
    assert not fallback_eligible(
        failure('authentication', status=401), emitted_output=False,
        fallback_configured=True,
    )
    assert not fallback_eligible(
        failure(), emitted_output=False, fallback_configured=False
    )


def test_decision_reports_a_reason():
    allowed = fallback_decision(
        failure(), emitted_output=False, fallback_configured=True
    )
    denied = fallback_decision(
        failure('authentication'), emitted_output=False, fallback_configured=True
    )
    assert allowed.allowed and allowed.reason == 'TRANSIENT_PROVIDER_FAILURE'
    assert not denied.allowed and denied.reason == 'INELIGIBLE_FAILURE'


def test_next_attempt_changes_only_the_attempt_identity():
    class FakeRequest:
        turn_id = 'turn'
        attempt_id = 'old'

        def model_copy(self, update):
            clone = FakeRequest()
            clone.turn_id = self.turn_id
            clone.attempt_id = update['attempt_id']
            return clone

    request = FakeRequest()
    updated = next_attempt(request, 'new')
    assert updated is not request
    assert updated.turn_id == 'turn'
    assert updated.attempt_id == 'new'


class FakeAdapter:
    def __init__(self, events):
        self.events = events

    async def stream(self, request, tools, tool_choice=None):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


class FakeRequest:
    turn_id = 'turn'
    attempt_id = 'attempt'

    def model_copy(self, update):
        clone = FakeRequest()
        clone.turn_id = self.turn_id
        clone.attempt_id = update['attempt_id']
        return clone


def run_fallback(monkeypatch, primary_events, fallback_events, *, reserve_error=None, fallback=True):
    import main as worker

    reserved = []

    agent = type('FakeAgent', (), {})()
    agent.pipeline = {'llm': {'provider': 'deepseek_direct'}}
    agent.session = type('Session', (), {'llm': 'primary'})()
    agent.fallback_llm = 'fallback' if fallback else None
    agent._require_openai_bounds = lambda request, tools: None
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    agent._stream_with_fallback = worker.SarjyAgent._stream_with_fallback.__get__(agent)

    async def reserve(provider, characters=0):
        reserved.append(provider)
        if reserve_error is not None:
            raise reserve_error

    agent.reserve = reserve

    adapters = {
        'primary': FakeAdapter(primary_events),
        'fallback': FakeAdapter(fallback_events),
    }
    monkeypatch.setattr(worker, 'adapter_for', lambda provider, kind: adapters[provider])
    monkeypatch.setattr(worker, 'livekit_chunk', lambda event: event)

    async def collect():
        return [event async for event in agent._stream_with_fallback(
            FakeRequest(), [], None
        )]

    try:
        events = asyncio.run(collect())
        return events, reserved, None
    except Exception as exc:
        return [], reserved, exc


def test_pre_output_failure_reserves_once_and_streams_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [GenerationFailure(failure('timeout'))],
        [
            GenerationEvent('text', turn_id='turn', attempt_id='attempt', text='fallback'),
            GenerationEvent('completed', turn_id='turn', attempt_id='attempt'),
        ],
    )
    assert error is None
    assert reserved == ['openai']
    assert [event.kind for event in events] == ['text', 'completed']


def test_primary_success_does_not_reserve_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [
            GenerationEvent('text', turn_id='turn', attempt_id='attempt', text='primary'),
            GenerationEvent('completed', turn_id='turn', attempt_id='attempt'),
        ],
        [],
    )
    assert error is None
    assert reserved == []
    assert [event.kind for event in events] == ['text', 'completed']


def test_emitted_output_blocks_fallback(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [
            GenerationEvent('text', turn_id='turn', attempt_id='attempt', text='partial'),
            GenerationFailure(failure('timeout', partial=True)),
        ],
        [GenerationEvent('completed', turn_id='turn', attempt_id='attempt')],
    )
    assert events == []
    assert reserved == []
    assert isinstance(error, GenerationFailure)


def test_fallback_budget_denial_forwards_nothing(monkeypatch):
    events, reserved, error = run_fallback(
        monkeypatch,
        [GenerationFailure(failure('timeout'))],
        [GenerationEvent('completed', turn_id='turn', attempt_id='attempt')],
        reserve_error=ValueError('DAILY_BUDGET_EXHAUSTED'),
    )
    assert events == []
    assert reserved == ['openai']
    assert isinstance(error, ValueError)


def test_attempt_deadline_raises_timeout_generation_failure(monkeypatch):
    import main as worker

    class SlowAdapter:
        async def stream(self, request, tools, tool_choice=None):
            await asyncio.sleep(1)
            yield GenerationEvent('completed', turn_id='turn', attempt_id='attempt')

    agent = type('FakeAgent', (), {})()
    agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
    monkeypatch.setattr(worker, 'GENERATION_ATTEMPT_SECONDS', 0.02)
    monkeypatch.setattr(worker, 'adapter_for', lambda provider, kind: SlowAdapter())

    async def collect():
        return [event async for event in agent._stream_attempt(
            'provider', 'deepseek_direct', FakeRequest(), [], None
        )]

    try:
        asyncio.run(collect())
    except GenerationFailure as exc:
        assert exc.event.error_code == 'timeout'
        assert exc.event.partial is False
    else:
        raise AssertionError('expected a timeout GenerationFailure')
