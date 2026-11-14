"""Exhaustive and property tests for the pure decision layer."""

from __future__ import annotations

import itertools

import pytest
from app.domain.interaction import (
    ACTION_FAMILY_QUESTION,
    INTERACTION_CONTRACT_VERSION,
    MIN_ANSWER_CONFIDENCE,
    OPERATION_QUESTION,
    PENDING_RELATION_QUESTION,
    REQUEST_TYPE_QUESTION,
    SCOPE_QUESTION,
    TERMINAL_STATES,
    ActionFamily,
    EffectNotAuthorized,
    InteractionAnswers,
    InteractionState,
    OperationChoice,
    PendingInteraction,
    PendingRelation,
    ReadyAction,
    RequestType,
    ScopeClarity,
    Transition,
    begin_execution,
    complete_execution,
    interaction_questions,
    transition,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

KNOWN_OPERATIONS = frozenset(
    {
        "answer",
        "retrieve_weather",
        "set_speaking_speed",
        "select_approved_voice",
        "set_captions_visible",
        "set_response_preference",
        "navigate_panel",
        "search_memory",
        "propose_context_exclusion",
        "restore_context_operation",
        "repeat_last_response",
    }
)
CONFIRMATION_REQUIRED = frozenset({"restore_context_operation"})


def answers(
    *,
    request_type: RequestType = RequestType.ACTION,
    family: ActionFamily = ActionFamily.VOICE_SETTINGS,
    operation: OperationChoice = OperationChoice.REGISTERED,
    operation_id: str | None = "set_speaking_speed",
    relation: PendingRelation = PendingRelation.NEW_REQUEST,
    scope: ScopeClarity = ScopeClarity.CLEAR,
    confidence: float = 0.99,
    version: str = INTERACTION_CONTRACT_VERSION,
) -> InteractionAnswers:
    return InteractionAnswers(
        contract_version=version,
        request_type=request_type,
        action_family=family,
        operation=operation,
        operation_id=operation_id,
        pending_relation=relation,
        scope=scope,
        confidence=confidence,
    )


def go(
    state: InteractionState,
    candidate: InteractionAnswers,
    *,
    pending: PendingInteraction | None = None,
    arguments: dict[str, object] | None = None,
) -> Transition:
    return transition(
        state,
        candidate,
        known_operations=KNOWN_OPERATIONS,
        confirmation_required=CONFIRMATION_REQUIRED,
        pending=pending,
        arguments=arguments,
    )


def test_question_batch_is_versioned_and_has_exactly_the_five_questions():
    questions = interaction_questions()
    assert INTERACTION_CONTRACT_VERSION == "interaction-questions-v1"
    assert set(questions) == {
        REQUEST_TYPE_QUESTION,
        ACTION_FAMILY_QUESTION,
        OPERATION_QUESTION,
        PENDING_RELATION_QUESTION,
        SCOPE_QUESTION,
    }
    assert set(questions[REQUEST_TYPE_QUESTION]["criteria"]) == {item.value for item in RequestType}
    assert set(questions[ACTION_FAMILY_QUESTION]["criteria"]) == {item.value for item in ActionFamily}
    assert set(questions[OPERATION_QUESTION]["criteria"]) == {item.value for item in OperationChoice}
    assert set(questions[PENDING_RELATION_QUESTION]["criteria"]) == {item.value for item in PendingRelation}
    assert set(questions[SCOPE_QUESTION]["criteria"]) == {item.value for item in ScopeClarity}
    assert "not_applicable" in questions[OPERATION_QUESTION]["criteria"]
    assert "unclear" in questions[OPERATION_QUESTION]["criteria"]
    assert "missing_information" in questions[SCOPE_QUESTION]["criteria"]
    assert "ambiguous" in questions[SCOPE_QUESTION]["criteria"]


def test_returned_questions_cannot_mutate_the_contract_copy():
    first = interaction_questions()
    first[REQUEST_TYPE_QUESTION]["criteria"]["answer"] = "changed"
    assert interaction_questions()[REQUEST_TYPE_QUESTION]["criteria"]["answer"] != "changed"


def test_answers_require_an_id_exactly_when_registered():
    assert answers().operation_id == "set_speaking_speed"
    with pytest.raises(ValidationError, match="REGISTERED_OPERATION_REQUIRES_ID"):
        answers(operation_id=None)
    with pytest.raises(ValidationError, match="UNREGISTERED_OPERATION_MUST_NOT_HAVE_ID"):
        answers(operation=OperationChoice.NOT_APPLICABLE, operation_id="set_speaking_speed")


def test_clear_action_without_confirmation_reaches_ready_and_effect_only_from_ready():
    result = go(InteractionState.INTERPRETING, answers(), arguments={"speed": 1.1})
    assert result.state is InteractionState.READY
    assert result.ready is not None
    assert result.ready.requires_confirmation is False
    effect = begin_execution(result.state, result.ready)
    assert effect.operation_id == "set_speaking_speed"
    assert effect.revision == 1
    assert dict(effect.arguments) == {"speed": 1.1}

    for state in InteractionState:
        if state is InteractionState.READY:
            continue
        with pytest.raises(EffectNotAuthorized, match="READY_STATE_REQUIRED"):
            begin_execution(state, result.ready)


def test_confirmation_round_trip_is_bound_to_the_delivered_revision():
    low = go(
        InteractionState.INTERPRETING,
        answers(
            family=ActionFamily.MEMORY_MODIFICATION,
            operation_id="restore_context_operation",
        ),
        arguments={"operation_id": "operation-last-exclusion"},
    )
    assert low.state is InteractionState.AWAITING_CONFIRMATION
    assert low.pending is not None

    undelivered = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(
            family=ActionFamily.MEMORY_MODIFICATION,
            operation_id="restore_context_operation",
            relation=PendingRelation.CONFIRM,
        ),
        pending=low.pending,
        arguments={"operation_id": "operation-last-exclusion"},
    )
    assert undelivered.state is InteractionState.AWAITING_CONFIRMATION
    assert undelivered.ready is None

    delivered = low.pending.model_copy(update={"confirmation_delivered": True})
    confirmed = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(
            family=ActionFamily.MEMORY_MODIFICATION,
            operation_id="restore_context_operation",
            relation=PendingRelation.CONFIRM,
        ),
        pending=delivered,
        arguments={"operation_id": "operation-last-exclusion"},
    )
    assert confirmed.state is InteractionState.READY
    assert confirmed.ready is not None
    assert confirmed.ready.confirmation_delivered is True
    effect = begin_execution(confirmed.state, confirmed.ready)
    assert effect.operation_id == "restore_context_operation"


def test_confirming_a_different_operation_or_changed_arguments_cannot_authorize():
    pending = PendingInteraction(
        operation_id="restore_context_operation",
        revision=2,
        arguments={"operation_id": "operation-last-exclusion"},
        confirmation_delivered=True,
    )
    mismatch = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(
            family=ActionFamily.MEMORY_MODIFICATION,
            operation_id="propose_context_exclusion",
            relation=PendingRelation.CONFIRM,
        ),
        pending=pending,
    )
    assert mismatch.state is InteractionState.AWAITING_DETAILS
    assert mismatch.ready is None

    changed = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(
            family=ActionFamily.MEMORY_MODIFICATION,
            operation_id="restore_context_operation",
            relation=PendingRelation.CONFIRM,
        ),
        pending=pending,
        arguments={"operation_id": "another-operation"},
    )
    assert changed.state is InteractionState.AWAITING_DETAILS
    assert changed.ready is None


def test_reject_and_cancel_are_terminal_and_never_ready():
    pending = PendingInteraction(
        operation_id="restore_context_operation",
        revision=1,
        arguments={"operation_id": "operation-last-exclusion"},
        confirmation_delivered=True,
    )
    rejected = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(family=ActionFamily.MEMORY_MODIFICATION, relation=PendingRelation.REJECT),
        pending=pending,
    )
    assert rejected.state is InteractionState.CANCELLED
    assert rejected.ready is None

    cancelled = go(InteractionState.INTERPRETING, answers(relation=PendingRelation.CANCEL))
    assert cancelled.state is InteractionState.CANCELLED
    assert cancelled.ready is None

    for state in TERMINAL_STATES:
        terminal = go(state, answers())
        assert terminal.state is state
        assert terminal.ready is None


def test_missing_details_round_trip_supplies_arguments_and_never_guesses():
    missing = go(
        InteractionState.INTERPRETING,
        answers(
            family=ActionFamily.MEMORY_SEARCH,
            operation_id="search_memory",
            scope=ScopeClarity.MISSING_INFORMATION,
        ),
    )
    assert missing.state is InteractionState.AWAITING_DETAILS
    assert missing.pending is not None
    assert missing.clarification is not None
    assert missing.clarification.reason == "MISSING_DETAILS"

    supplied = go(
        InteractionState.AWAITING_DETAILS,
        answers(
            family=ActionFamily.MEMORY_SEARCH,
            operation=OperationChoice.NOT_APPLICABLE,
            operation_id=None,
            relation=PendingRelation.SUPPLY_DETAILS,
            scope=ScopeClarity.CLEAR,
        ),
        pending=missing.pending,
        arguments={"query": "hotel in Rome"},
    )
    assert supplied.state is InteractionState.READY
    assert supplied.ready is not None
    assert dict(supplied.ready.arguments) == {"query": "hotel in Rome"}


def test_scope_missing_information_never_reaches_ready_even_with_arguments():
    result = go(
        InteractionState.INTERPRETING,
        answers(
            family=ActionFamily.VOICE_SETTINGS,
            operation_id="set_speaking_speed",
            scope=ScopeClarity.MISSING_INFORMATION,
        ),
        arguments={"speed": 1.1},
    )
    assert result.state is InteractionState.AWAITING_DETAILS
    assert result.ready is None


def test_low_confidence_and_contradictions_never_reach_ready():
    low = go(InteractionState.INTERPRETING, answers(confidence=MIN_ANSWER_CONFIDENCE - 0.01))
    assert low.state is InteractionState.AWAITING_DETAILS
    assert low.ready is None

    answer_with_action = go(InteractionState.INTERPRETING, answers(request_type=RequestType.ANSWER))
    assert answer_with_action.ready is None

    action_without_family = go(InteractionState.INTERPRETING, answers(family=ActionFamily.NOT_APPLICABLE))
    assert action_without_family.state is InteractionState.AWAITING_DETAILS
    assert action_without_family.ready is None

    unknown = go(InteractionState.INTERPRETING, answers(operation_id="not_a_registered_operation"))
    assert unknown.state is InteractionState.AWAITING_DETAILS
    assert unknown.ready is None
    assert unknown.clarification is not None
    assert unknown.clarification.reason == "UNKNOWN_OPERATION"

    unknown_pending = PendingInteraction(
        operation_id="not_a_registered_operation",
        revision=1,
        arguments={},
        confirmation_delivered=True,
    )
    confirm_unknown = go(
        InteractionState.AWAITING_CONFIRMATION,
        answers(
            operation=OperationChoice.NOT_APPLICABLE,
            operation_id=None,
            relation=PendingRelation.CONFIRM,
        ),
        pending=unknown_pending,
    )
    assert confirm_unknown.state is InteractionState.AWAITING_DETAILS
    assert confirm_unknown.ready is None


def test_plain_answer_completes_without_an_operation():
    result = go(
        InteractionState.INTERPRETING,
        answers(
            request_type=RequestType.ANSWER,
            family=ActionFamily.NOT_APPLICABLE,
            operation=OperationChoice.NOT_APPLICABLE,
            operation_id=None,
            scope=ScopeClarity.NOT_APPLICABLE,
        ),
    )
    assert result.state is InteractionState.SUCCEEDED
    assert result.answer is True
    assert result.ready is None


def test_execution_completion_paths_are_truthful():
    for outcome, expected in (
        ("succeeded", InteractionState.SUCCEEDED),
        ("failed", InteractionState.FAILED),
        ("unknown", InteractionState.UNKNOWN),
    ):
        result = complete_execution(InteractionState.EXECUTING, outcome)
        assert result.state is expected
        assert result.ready is None
    with pytest.raises(EffectNotAuthorized, match="EXECUTING_STATE_REQUIRED"):
        complete_execution(InteractionState.READY, "succeeded")


def test_confirmation_ready_action_cannot_be_built_before_delivery():
    with pytest.raises(ValidationError, match="CONFIRMATION_NOT_DELIVERED"):
        ReadyAction(
            operation_id="restore_context_operation",
            arguments={"operation_id": "operation-last-exclusion"},
            revision=1,
            confirmation_delivered=False,
            requires_confirmation=True,
        )
    delivered = ReadyAction(
        operation_id="restore_context_operation",
        arguments={"operation_id": "operation-last-exclusion"},
        revision=1,
        confirmation_delivered=True,
        requires_confirmation=True,
    )
    effect = begin_execution(InteractionState.READY, delivered)
    assert effect.operation_id == "restore_context_operation"


def test_every_state_and_answer_combination_produces_a_legal_transition():
    operation_ids = sorted(KNOWN_OPERATIONS)
    for (
        state,
        request_type,
        family,
        operation_choice,
        relation,
        scope,
    ) in itertools.product(
        InteractionState,
        RequestType,
        ActionFamily,
        OperationChoice,
        PendingRelation,
        ScopeClarity,
    ):
        operation_id = operation_ids[0] if operation_choice is OperationChoice.REGISTERED else None
        candidate = answers(
            request_type=request_type,
            family=family,
            operation=operation_choice,
            operation_id=operation_id,
            relation=relation,
            scope=scope,
            confidence=0.99,
        )
        for pending in (
            None,
            PendingInteraction(
                operation_id="restore_context_operation",
                revision=1,
                arguments={"operation_id": "operation-last-exclusion"},
            ),
            PendingInteraction(
                operation_id="restore_context_operation",
                revision=1,
                arguments={"operation_id": "operation-last-exclusion"},
                confirmation_delivered=True,
            ),
        ):
            result = go(state, candidate, pending=pending, arguments={"speed": 1.1})
            assert result.source is state
            if result.ready is not None:
                assert result.state is InteractionState.READY
            if result.pending is not None:
                assert result.state in {InteractionState.AWAITING_DETAILS, InteractionState.AWAITING_CONFIRMATION}


@settings(max_examples=300, deadline=None)
@given(
    state=st.sampled_from(list(InteractionState)),
    request_type=st.sampled_from(list(RequestType)),
    family=st.sampled_from(list(ActionFamily)),
    operation_choice=st.sampled_from(list(OperationChoice)),
    relation=st.sampled_from(list(PendingRelation)),
    scope=st.sampled_from(list(ScopeClarity)),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    pending_kind=st.sampled_from(("none", "undelivered", "delivered", "other")),
    arguments=st.one_of(
        st.none(),
        st.dictionaries(st.sampled_from(["speed", "query", "operation_id"]), st.text(max_size=20)),
    ),
)
def test_property_no_non_ready_state_can_begin_an_effect(
    state: InteractionState,
    request_type: RequestType,
    family: ActionFamily,
    operation_choice: OperationChoice,
    relation: PendingRelation,
    scope: ScopeClarity,
    confidence: float,
    pending_kind: str,
    arguments: dict[str, str] | None,
) -> None:
    operation_id = "set_speaking_speed" if operation_choice is OperationChoice.REGISTERED else None
    candidate = answers(
        request_type=request_type,
        family=family,
        operation=operation_choice,
        operation_id=operation_id,
        relation=relation,
        scope=scope,
        confidence=confidence,
    )
    pending = None
    if pending_kind != "none":
        pending = PendingInteraction(
            operation_id="restore_context_operation" if pending_kind == "other" else "set_speaking_speed",
            revision=1,
            arguments={"speed": 1.1} if pending_kind != "other" else {"operation_id": "operation-last-exclusion"},
            confirmation_delivered=pending_kind == "delivered",
        )
    result = go(state, candidate, pending=pending, arguments=arguments)

    # A transition never creates an effect, and a ready action only exists for
    # the READY result state.  The effect constructor itself refuses every other
    # source state, so no non-READY path can authorize a handler call.
    if result.ready is not None:
        assert result.state is InteractionState.READY
        if state is not InteractionState.READY:
            with pytest.raises(EffectNotAuthorized):
                begin_execution(state, result.ready)

    if confidence < MIN_ANSWER_CONFIDENCE:
        assert result.ready is None
        with pytest.raises(EffectNotAuthorized):
            begin_execution(
                result.state,
                ReadyAction(
                    operation_id="set_speaking_speed",
                    arguments={},
                    revision=1,
                    confirmation_delivered=False,
                    requires_confirmation=False,
                ),
            )
