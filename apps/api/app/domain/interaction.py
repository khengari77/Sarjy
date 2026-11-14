"""Pure interaction decisions for issue #12.

The five-question batch is sent together.  This module validates its answers and
folds them, with the current interaction state, into a next step.  It contains no
provider I/O, no handlers, no command parser and no execution.

The caller supplies the operation ids and confirmation requirements it already
owns (the #45 command registry is the eventual source).  This module does not
define argument schemas or duplicate that registry.

Safety properties are structural rather than advisory:

* only ``READY`` can produce a ``ReadyAction``;
* ``begin_execution`` accepts only a current ``READY`` state and a ready action
  whose confirmation requirement is satisfied;
* low confidence, contradictory answers, unknown operations and missing details
  produce clarification or detail requests, never a ready action.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from pydantic import Field, field_validator, model_validator

from app.domain.contracts import CONFIDENCE_THRESHOLD, Contract

INTERACTION_CONTRACT_VERSION: Final = "interaction-questions-v1"
MIN_ANSWER_CONFIDENCE: Final = CONFIDENCE_THRESHOLD

REQUEST_TYPE_QUESTION: Final = "request_type"
ACTION_FAMILY_QUESTION: Final = "action_family"
OPERATION_QUESTION: Final = "operation"
PENDING_RELATION_QUESTION: Final = "pending_relation"
SCOPE_QUESTION: Final = "scope_clarity"


class RequestType(StrEnum):
    ANSWER = "answer"
    ACTION = "action"
    BOTH = "both"
    UNCLEAR = "unclear"


class ActionFamily(StrEnum):
    """The action-family choices in the issue #12 five-question batch."""

    VOICE_SETTINGS = "voice_settings"
    NAVIGATION = "navigation"
    MEMORY_SEARCH = "memory_search"
    MEMORY_MODIFICATION = "memory_modification"
    EXTERNAL_LOOKUP = "external_lookup"
    REPEAT_RESPONSE = "repeat_response"
    NOT_APPLICABLE = "not_applicable"
    UNCLEAR = "unclear"


class OperationChoice(StrEnum):
    REGISTERED = "registered"
    NOT_APPLICABLE = "not_applicable"
    UNCLEAR = "unclear"


class PendingRelation(StrEnum):
    NEW_REQUEST = "new_request"
    SUPPLY_DETAILS = "supply_details"
    CONFIRM = "confirm"
    REJECT = "reject"
    MODIFY = "modify"
    CANCEL = "cancel"
    UNRELATED = "unrelated"
    UNCLEAR = "unclear"


class ScopeClarity(StrEnum):
    CLEAR = "clear"
    MISSING_INFORMATION = "missing_information"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"


class InteractionState(StrEnum):
    INTERPRETING = "interpreting"
    AWAITING_DETAILS = "awaiting_details"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY = "ready"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


TERMINAL_STATES: Final[frozenset[InteractionState]] = frozenset(
    {
        InteractionState.SUCCEEDED,
        InteractionState.FAILED,
        InteractionState.UNKNOWN,
        InteractionState.CANCELLED,
    }
)

ALLOWED_TRANSITIONS: Final[Mapping[InteractionState, frozenset[InteractionState]]] = MappingProxyType(
    {
        InteractionState.INTERPRETING: frozenset(
            {
                InteractionState.AWAITING_DETAILS,
                InteractionState.AWAITING_CONFIRMATION,
                InteractionState.READY,
                InteractionState.SUCCEEDED,
                InteractionState.CANCELLED,
            }
        ),
        InteractionState.AWAITING_DETAILS: frozenset(
            {
                InteractionState.AWAITING_DETAILS,
                InteractionState.AWAITING_CONFIRMATION,
                InteractionState.READY,
                InteractionState.CANCELLED,
            }
        ),
        InteractionState.AWAITING_CONFIRMATION: frozenset(
            {
                InteractionState.AWAITING_DETAILS,
                InteractionState.AWAITING_CONFIRMATION,
                InteractionState.READY,
                InteractionState.CANCELLED,
            }
        ),
        InteractionState.READY: frozenset(
            {
                InteractionState.AWAITING_DETAILS,
                InteractionState.AWAITING_CONFIRMATION,
                InteractionState.READY,
                InteractionState.CANCELLED,
            }
        ),
        InteractionState.EXECUTING: frozenset(
            {
                InteractionState.EXECUTING,
                InteractionState.SUCCEEDED,
                InteractionState.FAILED,
                InteractionState.UNKNOWN,
            }
        ),
        InteractionState.SUCCEEDED: frozenset({InteractionState.SUCCEEDED}),
        InteractionState.FAILED: frozenset({InteractionState.FAILED}),
        InteractionState.UNKNOWN: frozenset({InteractionState.UNKNOWN}),
        InteractionState.CANCELLED: frozenset({InteractionState.CANCELLED}),
    }
)

QUESTION_OPTIONS: Final[dict[str, dict[str, str]]] = {
    REQUEST_TYPE_QUESTION: {
        "answer": "The user wants information, explanation or conversation only.",
        "action": "The user wants one supported operation performed.",
        "both": "The user wants one supported operation and an explanation of its result.",
        "unclear": "The request cannot be classified confidently as answer or action.",
    },
    ACTION_FAMILY_QUESTION: {
        "voice_settings": "Change speaking speed, approved voice, captions or response preference.",
        "navigation": "Move to a supported panel.",
        "memory_search": "Recall or search saved memory without changing it.",
        "memory_modification": "Propose, confirm, restore or otherwise change saved memory/context.",
        "external_lookup": "Read a specific external result such as weather.",
        "repeat_response": "Replay the last available assistant response.",
        "not_applicable": "No action family applies; this is an answer or confirmation only.",
        "unclear": "No action family fits confidently.",
    },
    OPERATION_QUESTION: {
        "registered": "One operation from the caller-owned registry applies; name its id.",
        "not_applicable": "No registered operation applies.",
        "unclear": "The operation cannot be identified confidently.",
    },
    PENDING_RELATION_QUESTION: {
        "new_request": "A fresh request unrelated to any pending interaction.",
        "supply_details": "Supplies missing arguments for the pending operation.",
        "confirm": "Confirms the pending proposal exactly as delivered.",
        "reject": "Rejects the pending proposal.",
        "modify": "Changes an argument of the pending proposal.",
        "cancel": "Cancels the pending interaction.",
        "unrelated": "A separate request that should not consume the pending interaction.",
        "unclear": "The relation to the pending interaction is unclear.",
    },
    SCOPE_QUESTION: {
        "clear": "The target and scope are sufficiently specified.",
        "missing_information": "Required details are absent and must be requested.",
        "ambiguous": "The target or scope can be read more than one way.",
        "not_applicable": "No action scope applies because this is an answer or confirmation.",
    },
}

INTERACTION_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    REQUEST_TYPE_QUESTION: {
        "type": "choice",
        "instructions": "What does the user request in this turn?",
        "criteria": QUESTION_OPTIONS[REQUEST_TYPE_QUESTION],
    },
    ACTION_FAMILY_QUESTION: {
        "type": "choice",
        "instructions": "Which action family applies? Use not_applicable rather than unclear when no action is requested.",
        "criteria": QUESTION_OPTIONS[ACTION_FAMILY_QUESTION],
    },
    OPERATION_QUESTION: {
        "type": "choice",
        "instructions": (
            "Which supported operation applies? Choose registered only with an exact registered id; "
            "use not_applicable when no operation applies and unclear when it cannot be identified."
        ),
        "criteria": QUESTION_OPTIONS[OPERATION_QUESTION],
    },
    PENDING_RELATION_QUESTION: {
        "type": "choice",
        "instructions": "How does this turn relate to the pending interaction, if any?",
        "criteria": QUESTION_OPTIONS[PENDING_RELATION_QUESTION],
    },
    SCOPE_QUESTION: {
        "type": "choice",
        "instructions": "Is the requested action scope sufficiently clear? Distinguish missing information from ambiguity.",
        "criteria": QUESTION_OPTIONS[SCOPE_QUESTION],
    },
}


def interaction_questions() -> dict[str, dict[str, Any]]:
    """Return a fresh copy so callers cannot mutate the versioned wording."""
    return cast(dict[str, dict[str, Any]], json.loads(json.dumps(INTERACTION_QUESTIONS, ensure_ascii=False)))


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class InteractionAnswers(Contract):
    """One validated answer to the five-question batch.

    ``confidence`` is the model's own confidence in the batch, not permission to
    act.  Contradictory combinations are accepted by this model so the pure
    transition can reject them with an explicit clarification reason.
    """

    contract_version: str = Field(min_length=1, max_length=100)
    request_type: RequestType
    action_family: ActionFamily
    operation: OperationChoice
    operation_id: str | None = Field(default=None, min_length=1, max_length=100)
    pending_relation: PendingRelation
    scope: ScopeClarity
    confidence: float = Field(ge=0, le=1)

    @field_validator("confidence")
    @classmethod
    def confidence_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("CONFIDENCE_NOT_FINITE")
        return value

    @model_validator(mode="after")
    def operation_id_matches_choice(self) -> InteractionAnswers:
        if self.operation is OperationChoice.REGISTERED and self.operation_id is None:
            raise ValueError("REGISTERED_OPERATION_REQUIRES_ID")
        if self.operation is not OperationChoice.REGISTERED and self.operation_id is not None:
            raise ValueError("UNREGISTERED_OPERATION_MUST_NOT_HAVE_ID")
        return self


class PendingInteraction(Contract):
    """The exact operation/revision a confirmation or detail reply is bound to."""

    operation_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    arguments: Mapping[str, Any] = Field(default_factory=dict)
    confirmation_delivered: bool = False

    @field_validator("arguments", mode="after")
    @classmethod
    def arguments_are_immutable(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_mapping(value)


class ReadyAction(Contract):
    """A validated operation that may be executed exactly once from READY."""

    operation_id: str = Field(min_length=1, max_length=100)
    arguments: Mapping[str, Any] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    confirmation_delivered: bool
    requires_confirmation: bool
    include_explanation: bool = False

    @field_validator("arguments", mode="after")
    @classmethod
    def arguments_are_immutable(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_mapping(value)

    @model_validator(mode="after")
    def confirmation_is_satisfied(self) -> ReadyAction:
        if self.requires_confirmation and not self.confirmation_delivered:
            raise ValueError("CONFIRMATION_NOT_DELIVERED")
        return self


class Clarification(Contract):
    reason: str = Field(min_length=1, max_length=100)
    questions: tuple[str, ...] = ()
    operation_id: str | None = Field(default=None, min_length=1, max_length=100)


class Transition(Contract):
    """A pure next step.  It never carries an executable effect."""

    source: InteractionState
    state: InteractionState
    reason: str = Field(min_length=1, max_length=100)
    clarification: Clarification | None = None
    ready: ReadyAction | None = None
    pending: PendingInteraction | None = None
    answer: bool = False

    @model_validator(mode="after")
    def step_matches_state(self) -> Transition:
        if self.state not in ALLOWED_TRANSITIONS[self.source]:
            raise ValueError("ILLEGAL_TRANSITION")
        if self.ready is not None and self.state is not InteractionState.READY:
            raise ValueError("READY_ACTION_REQUIRES_READY_STATE")
        if self.pending is not None and self.state not in {
            InteractionState.AWAITING_DETAILS,
            InteractionState.AWAITING_CONFIRMATION,
        }:
            raise ValueError("PENDING_REQUIRES_WAITING_STATE")
        if self.answer and self.state is not InteractionState.SUCCEEDED:
            raise ValueError("ANSWER_REQUIRES_SUCCEEDED_STATE")
        if self.answer and (self.ready is not None or self.pending is not None):
            raise ValueError("ANSWER_HAS_NO_OPERATION")
        return self


class EffectRequest(Contract):
    """The only artifact that may cross into an effectful handler."""

    operation_id: str = Field(min_length=1, max_length=100)
    arguments: Mapping[str, Any] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    include_explanation: bool = False

    @field_validator("arguments", mode="after")
    @classmethod
    def arguments_are_immutable(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _freeze_mapping(value)


class EffectNotAuthorized(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def begin_execution(
    current: InteractionState,
    ready: ReadyAction,
    *,
    idempotency_key: str | None = None,
) -> EffectRequest:
    """Authorize one effect, only from READY with a satisfied confirmation.

    This is the sole constructor of ``EffectRequest``.  Every non-READY source
    state is rejected before the operation is inspected, which is the property
    the state machine relies on.
    """
    if current is not InteractionState.READY:
        raise EffectNotAuthorized("READY_STATE_REQUIRED")
    if ready.requires_confirmation and not ready.confirmation_delivered:
        raise EffectNotAuthorized("CONFIRMATION_NOT_DELIVERED")
    key = idempotency_key or f"{ready.operation_id}:{ready.revision}"
    return EffectRequest(
        operation_id=ready.operation_id,
        arguments=ready.arguments,
        revision=ready.revision,
        idempotency_key=key,
        include_explanation=ready.include_explanation,
    )


def transition(
    current: InteractionState,
    answers: InteractionAnswers,
    *,
    known_operations: Collection[str] = (),
    confirmation_required: Collection[str] = (),
    pending: PendingInteraction | None = None,
    arguments: Mapping[str, Any] | None = None,
) -> Transition:
    """Fold validated batch answers and the current state into one next step.

    ``known_operations`` and ``confirmation_required`` are supplied by the caller
    that owns the command registry.  This module deliberately does not define
    operation schemas, handlers or parsers.
    """
    if answers.contract_version != INTERACTION_CONTRACT_VERSION:
        return _clarify(current, "CONTRACT_VERSION_MISMATCH", (INTERACTION_CONTRACT_VERSION,))
    if current in TERMINAL_STATES:
        return Transition(source=current, state=current, reason="TERMINAL_STATE")
    if current is InteractionState.EXECUTING:
        return Transition(source=current, state=current, reason="EXECUTION_IN_PROGRESS")
    if answers.confidence < MIN_ANSWER_CONFIDENCE:
        return _clarify(current, "LOW_CONFIDENCE")

    relation = answers.pending_relation
    if relation is PendingRelation.CANCEL:
        return Transition(source=current, state=InteractionState.CANCELLED, reason="USER_CANCELLED")
    if relation is PendingRelation.REJECT:
        if pending is None:
            return _clarify(current, "NO_PENDING_INTERACTION")
        return Transition(source=current, state=InteractionState.CANCELLED, reason="USER_REJECTED")
    if relation is PendingRelation.UNCLEAR:
        return _clarify(current, "PENDING_RELATION_UNCLEAR")
    if relation is PendingRelation.CONFIRM:
        return _confirm(current, answers, known_operations, confirmation_required, pending, arguments)

    allow_pending = relation in {PendingRelation.SUPPLY_DETAILS, PendingRelation.MODIFY}
    if allow_pending and pending is None:
        return _clarify(current, "NO_PENDING_INTERACTION")
    if current is InteractionState.AWAITING_CONFIRMATION and relation in {
        PendingRelation.NEW_REQUEST,
        PendingRelation.UNRELATED,
    }:
        # A genuinely new request supersedes the pending proposal; it must still
        # pass every validation below and cannot inherit confirmation.
        pending = None
    return _interpret_action(
        current,
        answers,
        known_operations,
        confirmation_required,
        pending,
        arguments,
        allow_pending=allow_pending,
        relation=relation,
    )


def _interpret_action(
    current: InteractionState,
    answers: InteractionAnswers,
    known_operations: Collection[str],
    confirmation_required: Collection[str],
    pending: PendingInteraction | None,
    arguments: Mapping[str, Any] | None,
    *,
    allow_pending: bool,
    relation: PendingRelation,
) -> Transition:
    gate = _request_gate(current, answers)
    if gate is not None:
        return gate

    operation = _resolve_operation(current, answers, pending, allow_pending)
    if isinstance(operation, Transition):
        return operation
    if operation not in known_operations:
        return _clarify(current, "UNKNOWN_OPERATION", (operation,), operation)

    merged = _merged_arguments(pending, operation, arguments)
    if answers.scope is ScopeClarity.MISSING_INFORMATION:
        return _missing_details(current, operation, pending, merged)
    if operation in confirmation_required:
        return Transition(
            source=current,
            state=InteractionState.AWAITING_CONFIRMATION,
            reason="CONFIRMATION_REQUIRED",
            pending=PendingInteraction(
                operation_id=operation,
                revision=_next_revision(pending, operation),
                arguments=merged,
                confirmation_delivered=False,
            ),
        )
    return Transition(
        source=current,
        state=InteractionState.READY,
        reason="READY",
        ready=ReadyAction(
            operation_id=operation,
            arguments=merged,
            revision=_next_revision(pending, operation),
            confirmation_delivered=False,
            requires_confirmation=False,
            include_explanation=answers.request_type is RequestType.BOTH or relation is PendingRelation.MODIFY,
        ),
    )


def _request_gate(current: InteractionState, answers: InteractionAnswers) -> Transition | None:
    if answers.request_type is RequestType.UNCLEAR:
        return _clarify(current, "REQUEST_TYPE_UNCLEAR")
    if answers.request_type is RequestType.ANSWER:
        return _answer_transition(current, answers)
    if answers.action_family is ActionFamily.UNCLEAR:
        return _clarify(current, "ACTION_FAMILY_UNCLEAR")
    if answers.action_family is ActionFamily.NOT_APPLICABLE:
        return _clarify(current, "ACTION_FAMILY_REQUIRED")
    if answers.scope is ScopeClarity.AMBIGUOUS:
        return _clarify(current, "SCOPE_AMBIGUOUS")
    if answers.scope is ScopeClarity.NOT_APPLICABLE:
        return _clarify(current, "SCOPE_NOT_APPLICABLE")
    return None


def _answer_transition(current: InteractionState, answers: InteractionAnswers) -> Transition:
    if current is not InteractionState.INTERPRETING:
        return _clarify(current, "ANSWER_WHILE_PENDING")
    if answers.operation is OperationChoice.REGISTERED or answers.action_family not in {
        ActionFamily.NOT_APPLICABLE,
        ActionFamily.UNCLEAR,
    }:
        return _clarify(current, "REQUEST_TYPE_CONTRADICTION")
    return Transition(source=current, state=InteractionState.SUCCEEDED, reason="ANSWER_ONLY", answer=True)


def _resolve_operation(
    current: InteractionState,
    answers: InteractionAnswers,
    pending: PendingInteraction | None,
    allow_pending: bool,
) -> str | Transition:
    if answers.operation is OperationChoice.REGISTERED:
        assert answers.operation_id is not None
        return answers.operation_id
    if allow_pending and pending is not None:
        return pending.operation_id
    if answers.operation is OperationChoice.UNCLEAR:
        return _clarify(current, "OPERATION_UNCLEAR")
    return _clarify(current, "OPERATION_REQUIRED")


def _merged_arguments(
    pending: PendingInteraction | None,
    operation_id: str,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = (
        dict(pending.arguments) if pending is not None and pending.operation_id == operation_id else {}
    )
    if arguments:
        merged.update(arguments)
    return merged


def _missing_details(
    current: InteractionState,
    operation_id: str,
    pending: PendingInteraction | None,
    merged: Mapping[str, Any],
) -> Transition:
    return Transition(
        source=current,
        state=InteractionState.AWAITING_DETAILS,
        reason="MISSING_DETAILS",
        clarification=Clarification(reason="MISSING_DETAILS", operation_id=operation_id),
        pending=PendingInteraction(
            operation_id=operation_id,
            revision=_next_revision(pending, operation_id),
            arguments=merged,
            confirmation_delivered=False,
        ),
    )


def _confirm(
    current: InteractionState,
    answers: InteractionAnswers,
    known_operations: Collection[str],
    confirmation_required: Collection[str],
    pending: PendingInteraction | None,
    arguments: Mapping[str, Any] | None,
) -> Transition:
    if pending is None:
        return _clarify(current, "NO_PENDING_INTERACTION")
    if answers.request_type not in {RequestType.ACTION, RequestType.BOTH}:
        return _clarify(current, "CONFIRM_REQUIRES_ACTION_REQUEST")
    if answers.operation is OperationChoice.REGISTERED and answers.operation_id != pending.operation_id:
        return _clarify(current, "PENDING_OPERATION_MISMATCH", operation_id=pending.operation_id)
    if answers.scope is ScopeClarity.AMBIGUOUS:
        return _clarify(current, "SCOPE_AMBIGUOUS", operation_id=pending.operation_id)
    if pending.operation_id not in known_operations:
        return _clarify(current, "UNKNOWN_OPERATION", operation_id=pending.operation_id)
    if not pending.confirmation_delivered:
        return Transition(
            source=current,
            state=InteractionState.AWAITING_CONFIRMATION,
            reason="CONFIRMATION_NOT_DELIVERED",
            pending=pending,
        )
    if arguments:
        merged = {**dict(pending.arguments), **dict(arguments)}
        if merged != dict(pending.arguments):
            return _clarify(current, "PENDING_ARGUMENTS_CHANGED", operation_id=pending.operation_id)
    return Transition(
        source=current,
        state=InteractionState.READY,
        reason="CONFIRMED",
        ready=ReadyAction(
            operation_id=pending.operation_id,
            arguments=pending.arguments,
            revision=pending.revision,
            confirmation_delivered=True,
            requires_confirmation=pending.operation_id in confirmation_required,
        ),
    )


def _next_revision(pending: PendingInteraction | None, operation_id: str) -> int:
    if pending is None or pending.operation_id != operation_id:
        return 1
    return pending.revision + 1


def _clarify(
    current: InteractionState,
    reason: str,
    questions: tuple[str, ...] = (),
    operation_id: str | None = None,
) -> Transition:
    return Transition(
        source=current,
        state=InteractionState.AWAITING_DETAILS,
        reason=reason,
        clarification=Clarification(reason=reason, questions=questions, operation_id=operation_id),
    )


def complete_execution(current: InteractionState, outcome: Literal["succeeded", "failed", "unknown"]) -> Transition:
    """Move an executing interaction to a truthful terminal state."""
    if current is not InteractionState.EXECUTING:
        raise EffectNotAuthorized("EXECUTING_STATE_REQUIRED")
    target = {
        "succeeded": InteractionState.SUCCEEDED,
        "failed": InteractionState.FAILED,
        "unknown": InteractionState.UNKNOWN,
    }[outcome]
    return Transition(source=current, state=target, reason=outcome.upper())


__all__ = [
    "ACTION_FAMILY_QUESTION",
    "ALLOWED_TRANSITIONS",
    "ActionFamily",
    "Clarification",
    "EffectNotAuthorized",
    "EffectRequest",
    "INTERACTION_CONTRACT_VERSION",
    "INTERACTION_QUESTIONS",
    "InteractionAnswers",
    "InteractionState",
    "MIN_ANSWER_CONFIDENCE",
    "OPERATION_QUESTION",
    "OperationChoice",
    "PENDING_RELATION_QUESTION",
    "PendingInteraction",
    "PendingRelation",
    "QUESTION_OPTIONS",
    "REQUEST_TYPE_QUESTION",
    "ReadyAction",
    "RequestType",
    "SCOPE_QUESTION",
    "ScopeClarity",
    "TERMINAL_STATES",
    "Transition",
    "begin_execution",
    "complete_execution",
    "interaction_questions",
    "transition",
]
