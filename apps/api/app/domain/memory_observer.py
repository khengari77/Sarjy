"""Pure background memory-observer questions, response validation and decision.

The observer asks the Jev model four small choice questions about a finalized
user turn.  This module only validates the payload and turns the answers into a
:class:`Decision`; it performs no provider I/O and writes nothing.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.domain.memory_policy import Decision, Sensitivity, combine, decide
from app.domain.routing import JEV_MODEL
from app.domain.ui_routing import CONFIDENCE_THRESHOLD

_ANY_LANGUAGE = " The criteria apply in any language; the instruction may be in Arabic or English."

FACT_QUESTION: Final[dict[str, Any]] = {
    "type": "choice",
    "instructions": (
        "Does the current instruction state a lasting personal fact about the speaker that would still be true "
        "later and useful to remember? Judge only the instruction itself." + _ANY_LANGUAGE
    ),
    "criteria": {
        "yes": (
            "A stable fact about the speaker: their name, where they live or work, a preference, a relationship, "
            "an allergy, a possession or a plan that remains relevant. A polite request to remember such a fact "
            "also counts."
        ),
        "no": (
            "No lasting personal fact: a task or reminder, a question, a quotation, a hypothetical or conditional, "
            "chit-chat, small talk, the weather, an instruction to the assistant, or a fact about someone else."
        ),
    },
}

TIER_QUESTION: Final[dict[str, Any]] = {
    "type": "choice",
    "instructions": (
        "Which sensitivity tier best fits the personal fact stated in the current instruction? "
        "Choose the most cautious tier that applies." + _ANY_LANGUAGE
    ),
    "criteria": {
        "normal": (
            "An ordinary fact about the speaker with no health, money or credential content: name, location, "
            "work, language, hobbies, food or other harmless preferences."
        ),
        "third_party": (
            "A fact about another person, such as a family member, friend, colleague or neighbour, including "
            "their name, health, money or relationships."
        ),
        "health": (
            "A fact about the speaker's physical or mental health: diagnosis, medication, therapy, allergy, "
            "pregnancy, surgery, disability or symptoms."
        ),
        "finance": (
            "A fact about the speaker's money: salary, income, debt, loan, mortgage, bank or card details, "
            "taxes, investments, savings or bankruptcy."
        ),
        "credential": (
            "A secret or credential: password, passcode, PIN, API key, private or SSH key, seed or recovery "
            "phrase, one-time code, Social Security or national ID number, passport number or safe combination."
        ),
    },
}

NEGATION_QUESTION: Final[dict[str, Any]] = {
    "type": "choice",
    "instructions": (
        "Does the current instruction retract, correct or end something that was true before, or negate a fact "
        "about the speaker?" + _ANY_LANGUAGE
    ),
    "criteria": {
        "yes": (
            "Retracts or corrects an earlier fact ('I no longer live in X', 'actually my name is Y', 'forget "
            "that I like tea') or negates a fact about the speaker ('I am not allergic to nuts')."
        ),
        "no": (
            "States a new fact, asks a question, gives a task or makes an ordinary statement without retracting "
            "or negating anything."
        ),
    },
}

OBSERVER_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "fact": FACT_QUESTION,
    "tier": TIER_QUESTION,
    "negation": NEGATION_QUESTION,
}

_YES_NO: Final[dict[str, bool]] = {"yes": True, "no": False}
_TIERS: Final[dict[str, Sensitivity]] = {sensitivity.value: sensitivity for sensitivity in Sensitivity}


@dataclass(frozen=True)
class ObserverAnswers:
    """Validated answers to the observer questions; immutable."""

    has_fact: bool
    tier: Sensitivity
    negated: bool
    confidence: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be a finite number between 0 and 1")


def build_observer_request(instruction: str) -> dict[str, Any]:
    """The Jev request body for one observer call; a fresh copy every time."""
    questions = json.loads(json.dumps(OBSERVER_QUESTIONS))
    return {
        "model": JEV_MODEL,
        "state": {"current_instruction": instruction, "recent_context": [], "context_truncated": False},
        "questions": questions,
    }


def parse_observer_response(body: object) -> ObserverAnswers | None:
    """Validate a Jev payload; any deviation returns ``None``."""
    if not isinstance(body, Mapping):
        return None
    answers = body.get("answers")
    if not isinstance(answers, Mapping):
        return None
    fact = _parse_choice(answers.get("fact"), _YES_NO)
    tier = _parse_choice(answers.get("tier"), _TIERS)
    negation = _parse_choice(answers.get("negation"), _YES_NO)
    if fact is None or tier is None or negation is None:
        return None
    confidence = min(fact[1], tier[1], negation[1])
    return ObserverAnswers(
        has_fact=_YES_NO[fact[0]],
        tier=_TIERS[tier[0]],
        negated=_YES_NO[negation[0]],
        confidence=confidence,
    )


def _parse_choice(raw: object, options: Mapping[str, object]) -> tuple[str, float] | None:
    if not isinstance(raw, Mapping):
        return None
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in options:
        return None
    probability = raw.get("probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        return None
    value = float(probability)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return choice, value


def observer_plan(mode: str, floor: Sensitivity, answers: ObserverAnswers | None, *, explicit: bool) -> Decision:
    """What the background observer should do with a finalized user turn.

    The keyword floor can only be raised by the model's tier.  Nothing can lower
    a credential, and a retraction or a low-confidence answer only ever asks.
    """
    if mode != "auto":
        return Decision.SKIP
    if floor is Sensitivity.CREDENTIAL or (answers is not None and answers.tier is Sensitivity.CREDENTIAL):
        return Decision.SKIP
    if answers is None or not answers.has_fact:
        return Decision.SKIP
    combined = combine(floor, answers.tier)
    if answers.negated or answers.confidence < CONFIDENCE_THRESHOLD:
        return Decision.ASK
    return decide("auto", combined, explicit=explicit, negated=False)
