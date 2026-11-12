import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.domain.contracts import (
    AdjustSpeakingSpeed,
    Complexity,
    DecisionSource,
    Intent,
    ResponsePreference,
    SearchMemory,
    TIER_CAPS,
)
from app.domain.routing import (
    JEV_MODEL,
    ROUTING_POLICY_VERSION,
    COMPLEXITY_QUESTION,
    INTENT_QUESTION,
    MAX_INSTRUCTION_CHARS,
    MAX_STATE_CHARS,
    TIER_BY_OPTION,
    TIER_QUESTION,
    authorize_automatic_action,
    build_classification_state,
    decision_policy,
    validate_classification_payload,
)
from app.services.jev import (
    DEFAULT_DEADLINE_SECONDS,
    JevCircuitBreaker,
    JevClient,
)


def choice(option_names, selected, top=0.96, confidence=0.99):
    others = [name for name in option_names if name != selected]
    remainder = (1 - top) / len(others)
    return {
        'type': 'choice',
        'choice': selected,
        'confidence': confidence,
        'probabilities': {name: (top if name == selected else remainder) for name in option_names},
    }


def intent_options():
    return [intent.value for intent in Intent]


def tier_option(tier):
    return next(name for name, value in TIER_BY_OPTION.items() if value == tier)


def response(
    *,
    intent=Intent.CONVERSATION,
    tier=2,
    complexity='simple',
    top=0.96,
    confidence=0.99,
    model=JEV_MODEL,
    usage=None,
):
    return {
        'model': model,
        'answers': {
            INTENT_QUESTION: choice(intent_options(), intent.value, top=top, confidence=confidence),
            TIER_QUESTION: choice(list(TIER_BY_OPTION), tier_option(tier), top=0.97, confidence=0.98),
            COMPLEXITY_QUESTION: choice(['simple', 'complex'], complexity, top=0.96, confidence=0.97),
        },
        'usage': usage or {'input_tokens': 120, 'output_tokens': 9},
    }


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeReservation:
    def __init__(self, allow=True, advance_clock=None):
        self.allow = allow
        self.calls = []
        self.advance_clock = advance_clock

    async def __call__(self, request):
        self.calls.append(request)
        if self.advance_clock is not None:
            self.advance_clock()
        return self.allow


def transport(payload_factory, calls, *, status=200, clock=None, advance=0.0):
    def handler(request):
        calls.append(request)
        if clock is not None:
            clock.advance(advance)
        payload = payload_factory() if callable(payload_factory) else payload_factory
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def client_for(payload_factory, *, status=200, allow=True, clock=None, calls=None, advance=0.0, deadline=0.75):
    calls = calls if calls is not None else []
    clock = clock or FakeClock()
    reservations = FakeReservation(allow=allow)
    client = JevClient(
        'test-key',
        reservations,
        transport=transport(payload_factory, calls, status=status, clock=clock, advance=advance),
        clock=clock,
        deadline_seconds=deadline,
    )
    return client, calls, reservations, clock


def classify(client, instruction='Hello there', request_id='request-1', turn_id='turn-1'):
    return asyncio.run(client.classify(
        request_id=request_id,
        turn_id=turn_id,
        instruction=instruction,
    ))


def test_valid_independent_classifications_cover_every_intent_and_tier():
    for intent in Intent:
        for tier in range(1, 6):
            for complexity in ('simple', 'complex'):
                payload = response(intent=intent, tier=tier, complexity=complexity)
                outcome = validate_classification_payload(
                    payload, request_id='r', turn_id='t'
                )
                assert outcome.decision is not None
                assert outcome.decision.intent is intent
                assert outcome.decision.tier == tier
                assert outcome.decision.complexity is Complexity(complexity)
                assert outcome.decision.source is DecisionSource.JEV
                assert outcome.decision.model == JEV_MODEL
                assert outcome.decision.policy_version == ROUTING_POLICY_VERSION
                assert decision_policy(outcome.decision).max_output_tokens == TIER_CAPS[tier - 1]
                if intent is Intent.AMBIGUOUS:
                    assert outcome.kind == 'clarification'
                    assert outcome.reason == 'AMBIGUOUS_INTENT'
                else:
                    assert outcome.kind == 'decision'
                assert outcome.usage.input_tokens == 120


def test_complexity_never_changes_the_selected_tier_or_policy():
    simple = validate_classification_payload(
        response(tier=4, complexity='simple'), request_id='r', turn_id='t'
    )
    complex_ = validate_classification_payload(
        response(tier=4, complexity='complex'), request_id='r', turn_id='t'
    )
    assert simple.decision.tier == complex_.decision.tier == 4
    assert simple.decision.complexity is Complexity.SIMPLE
    assert complex_.decision.complexity is Complexity.COMPLEX
    assert decision_policy(simple.decision, ResponsePreference.AUTOMATIC) == decision_policy(
        complex_.decision, ResponsePreference.AUTOMATIC
    )


def test_client_sends_three_independent_questions_and_the_pinned_model():
    client, calls, reservations, _ = client_for(response())
    outcome = classify(client, 'Set a reminder for tomorrow')
    assert outcome.kind == 'decision'
    assert len(reservations.calls) == 1
    assert reservations.calls[0].provider == 'jev'
    assert reservations.calls[0].model == JEV_MODEL
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body['model'] == JEV_MODEL
    assert set(body['questions']) == {INTENT_QUESTION, TIER_QUESTION, COMPLEXITY_QUESTION}
    assert all(question['type'] == 'choice' for question in body['questions'].values())
    assert body['questions'][INTENT_QUESTION]['criteria'].keys() == {intent.value for intent in Intent}
    assert body['questions'][TIER_QUESTION]['criteria'].keys() == set(TIER_BY_OPTION)
    assert body['questions'][COMPLEXITY_QUESTION]['criteria'].keys() == {'simple', 'complex'}
    assert calls[0].headers['authorization'] == 'Bearer test-key'


def test_zero_retries_on_provider_failure():
    client, calls, _, _ = client_for(response(), status=500)
    outcome = classify(client)
    assert outcome.kind == 'bypass'
    assert outcome.reason == 'PROVIDER_FAILURE'
    assert len(calls) == 1
    assert client.circuit.consecutive_failures == 1


def test_total_deadline_expires_without_retry():
    calls = []

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request)
            await asyncio.sleep(0.2)
            return httpx.Response(200, json=response())

    async def run():
        client = JevClient(
            'test-key',
            FakeReservation(),
            transport=SlowTransport(),
            clock=FakeClock(),
            deadline_seconds=0.02,
        )
        return await client.classify(request_id='r', turn_id='t', instruction='Hello')

    outcome = asyncio.run(run())
    assert DEFAULT_DEADLINE_SECONDS == 0.75
    assert outcome.kind == 'bypass'
    assert outcome.reason == 'TIMEOUT'
    assert len(calls) == 1


def test_client_requires_an_injected_reservation_callback():
    with pytest.raises(TypeError, match='reserve'):
        JevClient('test-key', None)


def test_missing_credentials_bypass_without_reservation_or_network():
    calls = []
    reservations = FakeReservation()
    client = JevClient(
        '',
        reservations,
        transport=transport(response(), calls),
        clock=FakeClock(),
    )
    for _ in range(2):
        outcome = classify(client)
        assert outcome.kind == 'bypass'
        assert outcome.reason == 'MISSING_CREDENTIALS'
    assert calls == []
    assert reservations.calls == []


def test_reservation_denial_never_sends_a_request_and_is_not_operational():
    client, calls, reservations, _ = client_for(response(), allow=False)
    for _ in range(3):
        outcome = classify(client)
        assert outcome.kind == 'bypass'
        assert outcome.reason == 'RESERVATION_DENIED'
    assert calls == []
    assert len(reservations.calls) == 3
    assert client.circuit.consecutive_failures == 0
    assert not client.circuit.is_open()


def test_exhausted_credit_is_a_safe_bypass():
    client, calls, _, _ = client_for(
        {'detail': 'insufficient credit'}, status=402
    )
    outcome = classify(client)
    assert outcome.kind == 'bypass'
    assert outcome.reason == 'CREDIT_EXHAUSTED'
    assert len(calls) == 1


def test_three_operational_failures_open_five_minute_window_and_success_recovers():
    clock = FakeClock()
    state = {'status': 500}

    def handler(_request):
        return httpx.Response(state['status'], json=response())

    async def run():
        client = JevClient(
            'test-key',
            FakeReservation(),
            transport=httpx.MockTransport(handler),
            clock=clock,
        )
        first = [await client.classify(request_id=f'r{i}', turn_id='t', instruction='Hello') for i in range(3)]
        blocked = await client.classify(request_id='r3', turn_id='t', instruction='Hello')
        state['status'] = 200
        clock.advance(300)
        recovered = await client.classify(request_id='r4', turn_id='t', instruction='Hello')
        return client, first, blocked, recovered

    client, first, blocked, recovered = asyncio.run(run())
    assert [outcome.reason for outcome in first] == ['PROVIDER_FAILURE'] * 3
    assert blocked.reason == 'CIRCUIT_OPEN'
    assert recovered.kind == 'decision'
    assert client.circuit.consecutive_failures == 0
    assert client.circuit.open_until is None


def test_valid_low_confidence_response_resets_the_operational_counter():
    clock = FakeClock()
    mode = {'status': 500}

    def handler(_request):
        if mode['status'] != 200:
            return httpx.Response(mode['status'], json={})
        return httpx.Response(200, json=response(intent=Intent.CONVERSATION, top=0.45))

    async def run():
        client = JevClient(
            'test-key', FakeReservation(), transport=httpx.MockTransport(handler), clock=clock
        )
        await client.classify(request_id='a', turn_id='t', instruction='Hello')
        await client.classify(request_id='b', turn_id='t', instruction='Hello')
        mode['status'] = 200
        low_confidence = await client.classify(request_id='c', turn_id='t', instruction='Hello')
        mode['status'] = 500
        await client.classify(request_id='d', turn_id='t', instruction='Hello')
        await client.classify(request_id='e', turn_id='t', instruction='Hello')
        return client, low_confidence

    client, low_confidence = asyncio.run(run())
    assert low_confidence.kind == 'decision'
    assert not low_confidence.decision.permits_automatic_action()
    assert client.circuit.consecutive_failures == 2
    assert not client.circuit.is_open()


@pytest.mark.parametrize(
    'mutate, expected',
    [
        (lambda payload: payload['answers'].pop(INTENT_QUESTION), 'INVALID_RESPONSE'),
        (lambda payload: payload.update(model='jev-1.13.0'), 'MODEL_MISMATCH'),
        (lambda payload: payload['answers'][INTENT_QUESTION].update(type='score'), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION].update(choice='not_an_intent'), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION]['probabilities'].pop('conversation'), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION]['probabilities'].update(unknown=0.0), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION]['probabilities'].update(conversation=0.99), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION].update(choice='tool_request'), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][INTENT_QUESTION]['probabilities'].update(conversation='nan'), 'INVALID_RESPONSE'),
        (lambda payload: payload['answers'][TIER_QUESTION]['probabilities'].update(tier_1_short_acknowledgment=0.5), 'INVALID_RESPONSE'),
    ],
)
def test_missing_unknown_malformed_and_conflicting_outputs_bypass(mutate, expected):
    payload = response()
    mutate(payload)
    client, calls, _, _ = client_for(payload)
    outcome = classify(client)
    assert outcome.kind == 'bypass'
    assert outcome.reason == expected
    assert len(calls) == 1


def test_vendor_confidence_is_informational_not_the_selected_probability():
    strong = validate_classification_payload(
        response(intent=Intent.VOICE_SETTING, top=0.96, confidence=0.1),
        request_id='r', turn_id='t',
    )
    weak = validate_classification_payload(
        response(intent=Intent.VOICE_SETTING, top=0.62, confidence=1.0),
        request_id='r', turn_id='t',
    )
    assert strong.kind == 'decision'
    assert strong.automatic_action_eligible
    assert weak.kind == 'clarification'
    assert weak.reason == 'LOW_CONFIDENCE'
    assert weak.decision.confidence == 1.0


def test_automatic_eligibility_requires_supported_intent_and_valid_typed_action():
    strong = validate_classification_payload(
        response(intent=Intent.VOICE_SETTING), request_id='r', turn_id='t'
    )
    assert authorize_automatic_action(strong, AdjustSpeakingSpeed(delta=-0.1))
    assert not authorize_automatic_action(strong, SearchMemory(query='trip'))
    assert not authorize_automatic_action(strong, None)
    conversation = validate_classification_payload(
        response(intent=Intent.CONVERSATION), request_id='r', turn_id='t'
    )
    assert not authorize_automatic_action(conversation, AdjustSpeakingSpeed(delta=-0.1))


def test_context_is_bounded_but_the_current_instruction_is_preserved():
    instruction = 'x' * MAX_INSTRUCTION_CHARS
    context = [f'entry {index} ' + ('z' * 900) for index in range(6)]
    state = build_classification_state(instruction, context)
    assert state['current_instruction'] == instruction
    assert len(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) <= MAX_STATE_CHARS
    assert state['context_truncated'] is True
    assert state['recent_context'][-1].startswith('entry 5')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_non_finite_probabilities_are_rejected_directly(value):
    payload = response()
    payload['answers'][INTENT_QUESTION]['probabilities']['conversation'] = value
    with pytest.raises(ValueError, match='INVALID_PROBABILITY'):
        validate_classification_payload(payload, request_id='r', turn_id='t')


def test_oversized_instruction_bypasses_without_reservation_or_network():
    client, calls, reservations, _ = client_for(response())
    outcome = classify(client, 'x' * (MAX_INSTRUCTION_CHARS + 1))
    assert outcome.kind == 'bypass'
    assert outcome.reason == 'INSTRUCTION_TOO_LONG'
    assert calls == []
    assert reservations.calls == []


def test_outcome_is_immutable_and_usage_timing_are_retained():
    clock = FakeClock()
    client, _, _, _ = client_for(response(usage={'input_tokens': 33, 'output_tokens': 4}), clock=clock, advance=0.125)
    outcome = classify(client)
    assert outcome.usage.input_tokens == 33
    assert outcome.usage.output_tokens == 4
    assert outcome.elapsed_ms == pytest.approx(125.0)
    with pytest.raises(ValidationError):
        outcome.kind = 'bypass'


def test_circuit_breaker_invariants_are_independent_of_the_client():
    clock = FakeClock()
    breaker = JevCircuitBreaker(clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.is_open()
    breaker.record_failure()
    assert breaker.is_open()
    clock.advance(299)
    assert breaker.is_open()
    clock.advance(1)
    assert not breaker.is_open()
    breaker.record_failure()
    assert not breaker.is_open()
    breaker.record_success()
    assert breaker.consecutive_failures == 0
