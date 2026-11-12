"""Pure Jev routing contracts, question wording and decision validation.

This module deliberately contains no provider I/O and no memory/database access.
The service layer supplies bounded state and an HTTP response; everything here is
deterministic, testable decision logic.

The question wording follows TypeSafe's integration guidance: state is a small
JSON object with named fields, each question asks for one judgment, option names
describe the intended situation, and code (not the model) decides what an answer
permits.  Complexity is its own judgment and never raises reasoning effort or
response length.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal, cast

from app.domain.contracts import (
    PROBABILITY_MAX_TOLERANCE,
    PROBABILITY_TOLERANCE,
    AdjustSpeakingSpeed,
    Complexity,
    Contract,
    Decision,
    DecisionSource,
    Intent,
    OpenMemoryExplorer,
    ProposeContextExclusion,
    ResponsePolicy,
    ResponsePreference,
    RestoreContextOperation,
    SearchMemory,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    ShowConversationHistory,
    resolve_response_policy,
)
from pydantic import Field, ValidationError, model_validator

JEV_MODEL: Final = "jev-1.14.0"
ROUTING_POLICY_VERSION: Final = "day2-jev-v1"

INTENT_QUESTION: Final = "intent"
TIER_QUESTION: Final = "response_tier"
COMPLEXITY_QUESTION: Final = "complexity"

# Conservative application bounds.  Jev 1.14 accepts 64k tokens per request, but
# routing should receive one instruction plus a small excerpt of permitted
# context, never an entire memory store.
MAX_INSTRUCTION_CHARS: Final = 4000
MAX_STATE_CHARS: Final = 6000
MAX_CONTEXT_ENTRIES: Final = 12
MAX_CONTEXT_ENTRY_CHARS: Final = 800

INTENT_CRITERIA: Final[dict[str, str]] = {
    "conversation": (
        "A normal conversational request that needs a spoken or written answer, not a change to the application."
    ),
    "ui_control": (
        "A request to change or navigate the interface, such as opening the memory "
        "explorer or toggling captions. Showing the conversation list is "
        "conversation_navigation instead."
    ),
    "voice_setting": (
        "A request to change speaking speed, select an approved voice, or change the saved response-length preference."
    ),
    "memory_search": (
        "A request to recall, search or summarize saved memories and earlier context. "
        "It does not remove or change a memory."
    ),
    "memory_modification": (
        "A request to forget, exclude, restore or otherwise change saved memory or "
        "context, including proposing an exclusion while keeping exceptions."
    ),
    "conversation_navigation": ("A request to move to a different conversation or to the conversation list."),
    "tool_request": (
        "A request for a specific external tool result, such as current weather or a "
        "live lookup, rather than a memory or application control."
    ),
    "ambiguous": (
        "The request is too unclear, quoted, negated or compound to route safely, or "
        "no other category is a confident fit."
    ),
}

TIER_OPTIONS: Final[dict[str, str]] = {
    "tier_1_short_acknowledgment": (
        "A one-line acknowledgment or a single short sentence of about fifteen words or fewer."
    ),
    "tier_2_normal_conversation": (
        "A normal conversational answer of one or two sentences, about forty words or fewer."
    ),
    "tier_3_moderate_explanation": ("A moderate explanation or a short paragraph, about ninety words or fewer."),
    "tier_4_detailed_explanation": (
        "A detailed answer of a few short paragraphs, about one hundred eighty words or fewer."
    ),
    "tier_5_explicitly_detailed": (
        "An explicitly detailed, focused answer of several paragraphs, about three hundred fifty words or fewer."
    ),
}
TIER_BY_OPTION: Final[dict[str, int]] = {option: index for index, option in enumerate(TIER_OPTIONS, start=1)}
TIER_OPTION_BY_VALUE: Final[dict[int, str]] = {index: option for index, option in enumerate(TIER_OPTIONS, start=1)}

COMPLEXITY_CRITERIA: Final[dict[str, str]] = {
    "simple": (
        "The request is straightforward: a direct answer or a single known action "
        "needs no comparison, synthesis or multi-step reasoning."
    ),
    "complex": (
        "The request needs comparison, synthesis or several reasoning steps. "
        "Complexity is independent of how long the reply should be."
    ),
}

CLASSIFICATION_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    INTENT_QUESTION: {
        "type": "choice",
        "instructions": (
            "Which single category best describes what the user wants in the current "
            "instruction? Judge the request itself, not how it should be answered."
        ),
        "criteria": INTENT_CRITERIA,
    },
    TIER_QUESTION: {
        "type": "choice",
        "instructions": (
            "What response length does the current instruction ask for? Judge only "
            "the requested length; do not consider how difficult the topic is."
        ),
        "criteria": TIER_OPTIONS,
    },
    COMPLEXITY_QUESTION: {
        "type": "choice",
        "instructions": (
            "How much reasoning does handling the current instruction require? Judge "
            "complexity independently of the requested response length."
        ),
        "criteria": COMPLEXITY_CRITERIA,
    },
}

ACTION_INTENTS: Final[dict[Intent, frozenset[type]]] = {
    Intent.VOICE_SETTING: frozenset(
        {
            AdjustSpeakingSpeed,
            SetSpeakingSpeed,
            SelectApprovedVoice,
            SetCaptionsVisible,
        }
    ),
    Intent.UI_CONTROL: frozenset(
        {
            OpenMemoryExplorer,
            SetCaptionsVisible,
        }
    ),
    Intent.MEMORY_SEARCH: frozenset({SearchMemory}),
    Intent.MEMORY_MODIFICATION: frozenset(
        {
            ProposeContextExclusion,
            RestoreContextOperation,
        }
    ),
    Intent.CONVERSATION_NAVIGATION: frozenset({ShowConversationHistory}),
}


class ClassificationError(ValueError):
    """A vendor response that cannot safely become a Decision."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class InstructionTooLong(ValueError):
    """The current instruction itself does not fit; never truncate it silently."""

    def __init__(self) -> None:
        super().__init__("INSTRUCTION_TOO_LONG")


def classification_questions() -> dict[str, dict[str, Any]]:
    """Return a fresh copy so callers cannot mutate module-level wording."""
    return cast(dict[str, dict[str, Any]], json.loads(json.dumps(CLASSIFICATION_QUESTIONS, ensure_ascii=False)))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_classification_state(
    instruction: str,
    context: Sequence[str] = (),
) -> dict[str, Any]:
    """Build bounded structured state for one routing request.

    ``context`` is supplied by the caller and should already be permission-filtered;
    this function never retrieves memories.  The current instruction is preserved
    in full or ``InstructionTooLong`` is raised.  Context is added newest-first
    while it fits, with older entries omitted rather than pretending context is
    complete.
    """
    if not isinstance(instruction, str):
        raise ValueError("INSTRUCTION_MUST_BE_TEXT")
    current = instruction.strip()
    if not current:
        raise ValueError("INSTRUCTION_EMPTY")
    if len(current) > MAX_INSTRUCTION_CHARS:
        raise InstructionTooLong()

    candidates: list[str] = []
    for raw in context:
        if not isinstance(raw, str):
            raise ValueError("CONTEXT_MUST_BE_TEXT")
        text = " ".join(raw.split())
        if not text:
            continue
        if len(text) > MAX_CONTEXT_ENTRY_CHARS:
            text = text[: MAX_CONTEXT_ENTRY_CHARS - 1] + "…"
        candidates.append(text)
    candidates.reverse()  # newest first, matching caller's oldest-to-newest input

    selected: list[str] = []  # chronological, oldest first
    for text in candidates:
        if len(selected) >= MAX_CONTEXT_ENTRIES:
            break
        trial = [text, *selected]
        state = _state(current, trial, omitted=True)
        if len(_canonical_json(state)) <= MAX_STATE_CHARS:
            selected = trial
        else:
            break

    omitted = len(candidates) > len(selected)
    state = _state(current, selected, omitted=omitted)
    if len(_canonical_json(state)) > MAX_STATE_CHARS:
        # Can only happen when the instruction plus fixed JSON overhead exceeds the
        # bound; the instruction is never shortened.
        raise InstructionTooLong()
    return state


def _state(instruction: str, context: list[str], *, omitted: bool) -> dict[str, Any]:
    return {
        "current_instruction": instruction,
        "recent_context": context,
        "context_truncated": omitted,
    }


def build_systemone_request(state: Mapping[str, Any]) -> dict[str, Any]:
    """The exact pinned request body; used by the client and benchmark hashing."""
    return {
        "model": JEV_MODEL,
        "state": dict(state),
        "questions": classification_questions(),
    }


class UsageMetadata(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ReservationRequest(Contract):
    """The minimum a spending owner needs to approve one bounded request."""

    request_id: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=100)
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(default=0, ge=0)
    estimated_cost_micro_usd: int = Field(ge=0)
    purpose: Literal["classification", "extraction"] = "classification"


class RoutingOutcome(Contract):
    """Immutable result of one classification attempt.

    ``kind=decision`` is a validated distribution.  ``kind=clarification`` is a
    validated distribution that cannot authorize a control action and should be
    clarified.  ``kind=bypass`` means no trustworthy decision exists.
    """

    kind: Literal["decision", "clarification", "bypass"]
    request_id: str = Field(min_length=1, max_length=100)
    turn_id: str = Field(min_length=1, max_length=100)
    decision: Decision | None = None
    reason: str | None = Field(default=None, max_length=100)
    usage: UsageMetadata | None = None
    elapsed_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def kind_is_consistent(self) -> RoutingOutcome:
        if self.elapsed_ms is not None and not math.isfinite(self.elapsed_ms):
            raise ValueError("ELAPSED_NOT_FINITE")
        if self.kind in {"decision", "clarification"} and self.decision is None:
            raise ValueError("DECISION_REQUIRED")
        if self.kind == "bypass" and self.decision is not None:
            raise ValueError("BYPASS_HAS_DECISION")
        if self.kind == "bypass" and not self.reason:
            raise ValueError("BYPASS_REASON_REQUIRED")
        if self.kind == "clarification" and not self.reason:
            raise ValueError("CLARIFICATION_REASON_REQUIRED")
        return self

    @property
    def automatic_action_eligible(self) -> bool:
        """Whether the decision alone could authorize a supported control action.

        Action arguments must also validate; use ``authorize_automatic_action``.
        """
        return (
            self.kind == "decision"
            and self.decision is not None
            and self.decision.intent in ACTION_INTENTS
            and self.decision.permits_automatic_action()
        )


def authorize_automatic_action(outcome: RoutingOutcome, action: Any) -> bool:
    """The complete gate for an automatic control action.

    Requires a validated decision, the published probability/lead thresholds,
    a supported intent, and a typed action whose kind is permitted for it.
    """
    if not outcome.automatic_action_eligible or outcome.decision is None:
        return False
    if action is None or not hasattr(action, "kind"):
        return False
    allowed = ACTION_INTENTS.get(outcome.decision.intent, frozenset())
    return type(action) in allowed


def decision_policy(
    decision: Decision,
    preference: ResponsePreference = ResponsePreference.AUTOMATIC,
) -> ResponsePolicy:
    """Resolve the existing response policy from the independently chosen tier."""
    return resolve_response_policy(preference, classified_tier=decision.tier)


def _choice_answer(
    answers: Mapping[str, Any],
    name: str,
    expected_options: set[str],
) -> tuple[str, dict[str, float], float]:
    answer = answers.get(name)
    if not isinstance(answer, Mapping):
        raise ClassificationError("MISSING_ANSWER")
    if answer.get("type") != "choice":
        raise ClassificationError("WRONG_ANSWER_TYPE")

    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in expected_options:
        raise ClassificationError("UNKNOWN_OPTION")

    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ClassificationError("INVALID_CONFIDENCE")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ClassificationError("INVALID_CONFIDENCE")

    raw_probabilities = answer.get("probabilities")
    if not isinstance(raw_probabilities, Mapping):
        raise ClassificationError("MISSING_PROBABILITIES")
    if set(raw_probabilities) != expected_options:
        raise ClassificationError("PROBABILITY_OPTIONS_MISMATCH")

    probabilities: dict[str, float] = {}
    for option, value in raw_probabilities.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ClassificationError("INVALID_PROBABILITY")
        probability = float(value)
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ClassificationError("INVALID_PROBABILITY")
        probabilities[option] = probability

    total = math.fsum(probabilities.values())
    if abs(total - 1) > PROBABILITY_TOLERANCE:
        raise ClassificationError("PROBABILITY_SUM")

    top = max(probabilities.values())
    if probabilities[choice] < top - PROBABILITY_MAX_TOLERANCE:
        raise ClassificationError("SELECTED_OPTION_NOT_MAXIMUM")
    return choice, probabilities, confidence


def _usage(payload: Mapping[str, Any]) -> UsageMetadata | None:
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return None
    values = []
    for key in ("input_tokens", "output_tokens"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        values.append(value)
    return UsageMetadata(input_tokens=values[0], output_tokens=values[1])


def validate_classification_payload(
    payload: Any,
    *,
    request_id: str,
    turn_id: str,
    policy_version: str = ROUTING_POLICY_VERSION,
) -> RoutingOutcome:
    """Validate a System One response and return a safe immutable outcome.

    All expected questions and options must be present; unknown options, malformed
    distributions, a non-maximum declared choice and a substituted model are
    rejected.  Vendor confidence is retained only as informational metadata.
    """
    if not isinstance(payload, Mapping):
        raise ClassificationError("RESPONSE_NOT_OBJECT")
    if payload.get("model") != JEV_MODEL:
        raise ClassificationError("MODEL_MISMATCH")

    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise ClassificationError("MISSING_ANSWERS")
    expected_questions = {INTENT_QUESTION, TIER_QUESTION, COMPLEXITY_QUESTION}
    if set(answers) != expected_questions:
        raise ClassificationError("ANSWER_SET_MISMATCH")

    intent_choice, intent_probabilities, intent_confidence = _choice_answer(
        answers, INTENT_QUESTION, {intent.value for intent in Intent}
    )
    tier_choice, _, _ = _choice_answer(answers, TIER_QUESTION, set(TIER_BY_OPTION))
    complexity_choice, _, _ = _choice_answer(answers, COMPLEXITY_QUESTION, set(COMPLEXITY_CRITERIA))

    try:
        intent = Intent(intent_choice)
        tier = TIER_BY_OPTION[tier_choice]
        complexity = Complexity(complexity_choice)
        decision = Decision(
            turn_id=turn_id,
            intent=intent,
            probabilities={Intent(name): value for name, value in intent_probabilities.items()},
            confidence=intent_confidence,
            tier=tier,
            complexity=complexity,
            source=DecisionSource.JEV,
            model=JEV_MODEL,
            policy_version=policy_version,
        )
    except (KeyError, ValueError, ValidationError) as exc:
        raise ClassificationError("INVALID_DECISION") from exc

    kind: Literal["decision", "clarification", "bypass"]
    if decision.intent is Intent.AMBIGUOUS:
        kind = "clarification"
        reason = "AMBIGUOUS_INTENT"
    elif decision.intent in ACTION_INTENTS and not decision.permits_automatic_action():
        kind = "clarification"
        reason = "LOW_CONFIDENCE"
    else:
        kind = "decision"
        reason = None

    return RoutingOutcome(
        kind=kind,
        request_id=request_id,
        turn_id=turn_id,
        decision=decision,
        reason=reason,
        usage=_usage(payload),
    )


__all__ = [
    "ACTION_INTENTS",
    "CLASSIFICATION_QUESTIONS",
    "COMPLEXITY_CRITERIA",
    "COMPLEXITY_QUESTION",
    "ClassificationError",
    "INTENT_CRITERIA",
    "INTENT_QUESTION",
    "InstructionTooLong",
    "JEV_MODEL",
    "MAX_CONTEXT_ENTRIES",
    "MAX_CONTEXT_ENTRY_CHARS",
    "MAX_INSTRUCTION_CHARS",
    "MAX_STATE_CHARS",
    "ReservationRequest",
    "ROUTING_POLICY_VERSION",
    "RoutingOutcome",
    "TIER_BY_OPTION",
    "TIER_OPTION_BY_VALUE",
    "TIER_OPTIONS",
    "TIER_QUESTION",
    "UsageMetadata",
    "authorize_automatic_action",
    "build_classification_state",
    "build_systemone_request",
    "classification_questions",
    "decision_policy",
    "validate_classification_payload",
]
