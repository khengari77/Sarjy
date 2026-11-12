import asyncio

import pytest

from app.domain.contracts import (
    ACTION_TYPES,
    AdjustSpeakingSpeed,
    ProposeContextExclusion,
    RestoreContextOperation,
    SelectApprovedVoice,
    SetSpeakingSpeed,
)
from app.domain.routing import MAX_INSTRUCTION_CHARS
from app.services.action_extraction import (
    ExtractionOutcome,
    GenerationResult,
    build_extraction_prompt,
    extract_action,
    unsafe_utterance_reason,
)
from app.domain.routing import UsageMetadata


def run_extract(
    text,
    raw,
    *,
    allow=True,
    approved_voice_ids=(),
    allowed_operation_ids=(),
    context=(),
    allowed_kinds=None,
    generate_error=None,
):
    reservations = []
    prompts = []

    async def reserve(request):
        reservations.append(request)
        return allow

    async def generate(prompt):
        prompts.append(prompt)
        if generate_error is not None:
            raise generate_error
        return raw

    async def run():
        return await extract_action(
            text,
            request_id='extract-1',
            reserve=reserve,
            generate=generate,
            approved_voice_ids=approved_voice_ids,
            allowed_operation_ids=allowed_operation_ids,
            context=context,
            allowed_kinds=allowed_kinds,
        )

    return asyncio.run(run()), reservations, prompts


def test_exact_fast_path_precedes_reservation_and_generation():
    outcome, reservations, prompts = run_extract('  SPEAK FASTER.  ', {'kind': 'NoAction'})
    assert outcome.kind == 'action'
    assert outcome.source == 'exact'
    assert isinstance(outcome.action, AdjustSpeakingSpeed)
    assert outcome.action.delta == 0.1
    assert reservations == []
    assert prompts == []


def test_valid_generated_action_is_returned_with_usage_and_a_bounded_prompt():
    outcome, reservations, prompts = run_extract(
        'Set my speaking speed to 1.2',
        GenerationResult(
            payload={'kind': 'SetSpeakingSpeed', 'speed': 1.2},
            usage=UsageMetadata(input_tokens=44, output_tokens=7),
        ),
    )
    assert outcome.kind == 'action'
    assert outcome.source == 'generated'
    assert isinstance(outcome.action, SetSpeakingSpeed)
    assert outcome.action.speed == 1.2
    assert outcome.usage.input_tokens == 44
    assert reservations[0].provider == 'deepseek'
    assert reservations[0].purpose == 'extraction'
    assert len(prompts) == 1
    assert 'current_instruction' in prompts[0]
    assert 'Set my speaking speed to 1.2' in prompts[0]
    assert 'NoAction' in prompts[0]


@pytest.mark.parametrize(
    'raw, reason',
    [
        ('not json', 'INVALID_JSON'),
        ([], 'INVALID_JSON'),
        ({'kind': 'SearchMemory', 'query': 'trip', 'extra': True}, 'INVALID_ACTION'),
        ({'kind': 'SetSpeakingSpeed'}, 'INVALID_ACTION'),
        ({'kind': 'UnknownAction'}, 'INVALID_ACTION'),
        ({'kind': 'NoAction'}, 'NO_ACTION'),
    ],
)
def test_invalid_generated_payloads_clarify_without_an_action(raw, reason):
    outcome, reservations, prompts = run_extract('Search my memory for the trip', raw)
    assert outcome.kind == 'clarification'
    assert outcome.reason == reason
    assert outcome.action is None
    assert len(reservations) == 1
    assert len(prompts) == 1


def test_voice_references_must_come_from_the_caller_approved_choices():
    rejected, _, _ = run_extract(
        'Use the Aurora voice',
        {'kind': 'SelectApprovedVoice', 'voice_id': 'aurora'},
        approved_voice_ids=('orion',),
    )
    assert rejected.kind == 'clarification'
    assert rejected.reason == 'UNSUPPORTED_VOICE'

    accepted, _, _ = run_extract(
        'Use the Aurora voice',
        {'kind': 'SelectApprovedVoice', 'voice_id': 'aurora'},
        approved_voice_ids=('aurora', 'orion'),
    )
    assert accepted.kind == 'action'
    assert isinstance(accepted.action, SelectApprovedVoice)


def test_operation_ids_must_be_caller_supplied_and_never_invented():
    rejected, _, _ = run_extract(
        'Undo the last exclusion',
        {'kind': 'RestoreContextOperation', 'operation_id': 'operation-9'},
        allowed_operation_ids=('operation-1',),
    )
    assert rejected.kind == 'clarification'
    assert rejected.reason == 'UNSUPPORTED_OPERATION'

    accepted, _, _ = run_extract(
        'Undo the last exclusion',
        {'kind': 'RestoreContextOperation', 'operation_id': 'operation-1'},
        allowed_operation_ids=('operation-1',),
    )
    assert accepted.kind == 'action'
    assert isinstance(accepted.action, RestoreContextOperation)


@pytest.mark.parametrize(
    'text, reason',
    [
        ('"Hide captions"', 'QUOTED_UTTERANCE'),
        ("Don't open the memory explorer.", 'NEGATED_UTTERANCE'),
        ('Open the memory explorer and show captions.', 'COMPOUND_UTTERANCE'),
        ('Open the memory explorer. Show captions.', 'COMPOUND_UTTERANCE'),
        ('Never set my speed to 1.2.', 'NEGATED_UTTERANCE'),
    ],
)
def test_quoted_negated_and_compound_requests_never_reach_generation(text, reason):
    outcome, reservations, prompts = run_extract(
        text,
        {'kind': 'SetCaptionsVisible', 'visible': True},
    )
    assert outcome.kind == 'clarification'
    assert outcome.reason == reason
    assert outcome.action is None
    assert reservations == []
    assert prompts == []


def test_memory_exclusion_is_only_a_proposal():
    outcome, _, _ = run_extract(
        'Forget the hotel booking I mentioned',
        {'kind': 'ProposeContextExclusion', 'query': 'hotel booking', 'keep': []},
    )
    assert outcome.kind == 'action'
    assert isinstance(outcome.action, ProposeContextExclusion)
    names = {model.__name__ for model in ACTION_TYPES}
    assert not any('Confirm' in name for name in names)


def test_reservation_denial_and_generation_failure_never_execute():
    denied, reservations, prompts = run_extract(
        'Set my speaking speed to 1.2',
        {'kind': 'SetSpeakingSpeed', 'speed': 1.2},
        allow=False,
    )
    assert denied.kind == 'clarification'
    assert denied.reason == 'RESERVATION_DENIED'
    assert len(reservations) == 1
    assert prompts == []

    failed, _, prompts = run_extract(
        'Set my speaking speed to 1.2',
        {'kind': 'SetSpeakingSpeed', 'speed': 1.2},
        generate_error=RuntimeError('provider down'),
    )
    assert failed.kind == 'clarification'
    assert failed.reason == 'GENERATION_FAILED'
    assert len(prompts) == 1


def test_oversized_instruction_and_empty_instruction_clarify_before_reservation():
    oversized, reservations, prompts = run_extract(
        'x' * (MAX_INSTRUCTION_CHARS + 1), {'kind': 'NoAction'}
    )
    assert oversized.kind == 'clarification'
    assert oversized.reason == 'INSTRUCTION_TOO_LONG'
    assert reservations == []
    assert prompts == []

    empty, reservations, prompts = run_extract('   ', {'kind': 'NoAction'})
    assert empty.kind == 'clarification'
    assert empty.reason == 'EMPTY_INSTRUCTION'
    assert reservations == []
    assert prompts == []


def test_allowed_kinds_are_enforced_after_parsing():
    outcome, _, _ = run_extract(
        'Open my history',
        {'kind': 'ShowConversationHistory'},
        allowed_kinds={'SearchMemory'},
    )
    assert outcome.kind == 'clarification'
    assert outcome.reason == 'UNSUPPORTED_ACTION'

    accepted, _, _ = run_extract(
        'Search my memory for the trip',
        {'kind': 'SearchMemory', 'query': 'trip'},
        allowed_kinds={'SearchMemory'},
    )
    assert accepted.kind == 'action'


def test_prompt_contains_only_caller_approved_references_and_bounded_context():
    prompt = build_extraction_prompt(
        'Use my preferred voice',
        approved_voice_ids=('orion', 'aurora', 'aurora'),
        allowed_operation_ids=('operation-1',),
        context=['old ' * 500, 'new ' * 500],
    )
    assert '"aurora"' in prompt
    assert '"orion"' in prompt
    assert '"operation-1"' in prompt
    assert len(prompt) <= 4000


def test_unsafe_detector_does_not_flag_contractions_or_decimals():
    assert unsafe_utterance_reason("What's the weather in Tripoli?") is None
    assert unsafe_utterance_reason('Set my speaking speed to 1.2.') is None
    assert unsafe_utterance_reason("Don't open it.") == 'NEGATED_UTTERANCE'


def test_missing_dependencies_raise_instead_of_creating_an_unreserved_path():
    async def generate(_prompt):
        return {'kind': 'NoAction'}

    async def reserve(_request):
        return True

    async def missing_reserve():
        return await extract_action(
            'Set my speaking speed to 1.2',
            request_id='r',
            reserve=None,
            generate=generate,
        )

    async def missing_generate():
        return await extract_action(
            'Set my speaking speed to 1.2',
            request_id='r',
            reserve=reserve,
            generate=None,
        )

    with pytest.raises(TypeError):
        asyncio.run(missing_reserve())
    with pytest.raises(TypeError):
        asyncio.run(missing_generate())
