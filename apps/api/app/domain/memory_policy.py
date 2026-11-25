"""Pure memory-saving policy: given the user's mode and what a fact is about, save, ask or skip.

No I/O and no model call. The model may only *propose* a fact; this decides what happens next. The keyword net can
only raise sensitivity, never lower it, so a missed keyword can make the policy more permissive than ideal but a model
that labels a fact "normal" can never talk it past a credential.
"""

import re
from enum import StrEnum

from app.domain.contracts import MemoryMode


class Sensitivity(StrEnum):
    NORMAL = "normal"
    THIRD_PARTY = "third_party"
    HEALTH = "health"
    FINANCE = "finance"
    CREDENTIAL = "credential"


class Decision(StrEnum):
    SAVE = "save"
    ASK = "ask"
    SKIP = "skip"


# Least to most sensitive. ``max`` over this order is how two labels combine.
_ORDER = (
    Sensitivity.NORMAL,
    Sensitivity.THIRD_PARTY,
    Sensitivity.HEALTH,
    Sensitivity.FINANCE,
    Sensitivity.CREDENTIAL,
)

_CREDENTIAL = re.compile(
    r"\b(?:password|passcode|passphrase|pin(?: code| number)?|cvv|cvc|api[ -]?key|secret key|private key|"
    r"security code|one[- ]time (?:code|password)|otp|recovery (?:code|phrase)|seed phrase)\b",
    re.IGNORECASE,
)
_FINANCE = re.compile(
    r"\b(?:salary|income|net worth|debt|loan|mortgage|credit card|card number|bank account|account number|iban|"
    r"routing number|tax(?:es)?|bankrupt\w*|invest\w*|savings)\b",
    re.IGNORECASE,
)
_HEALTH = re.compile(
    r"\b(?:diagnos\w+|medication|prescri\w+|therapy|therapist|depress\w+|anxiety|diabet\w+|cancer|pregnan\w+|"
    r"hiv|allerg\w+|surgery|disorder|illness|disease|doctor|medical|blood pressure|metformin|insulin)\b",
    re.IGNORECASE,
)
_THIRD_PARTY = re.compile(
    r"\b(?:my (?:boss|ex|wife|husband|partner|girlfriend|boyfriend|mother|father|mom|dad|sister|brother|friend|"
    r"coworker|colleague|neighbou?r|landlord)(?:'s)?)\b",
    re.IGNORECASE,
)


def classify_sensitivity(text: str) -> Sensitivity:
    """Keyword safety net. Deterministic; the strongest matching class wins."""
    if _CREDENTIAL.search(text):
        return Sensitivity.CREDENTIAL
    if _FINANCE.search(text):
        return Sensitivity.FINANCE
    if _HEALTH.search(text):
        return Sensitivity.HEALTH
    if _THIRD_PARTY.search(text):
        return Sensitivity.THIRD_PARTY
    return Sensitivity.NORMAL


def combine(*labels: Sensitivity) -> Sensitivity:
    """The most sensitive of several labels (model label plus keyword net)."""
    return max(labels, key=_ORDER.index, default=Sensitivity.NORMAL)


def decide(mode: MemoryMode, sensitivity: Sensitivity, *, explicit: bool, negated: bool = False) -> Decision:
    """What to do with a proposed fact.

    * ``off`` never saves, even when the user explicitly asks; the spoken reply says memory is off.
    * A credential is never saved in any mode, however it was asked for.
    * ``ask`` always asks, so the user hears one question per fact. So does a negated fact in any mode, because it
      retracts a stored fact instead of adding one.
    * ``auto`` saves an ordinary fact without asking, and a fact about another person only when the user
      explicitly asked to remember it (otherwise it asks once). Health and finance facts are asked about once even
      when the user explicitly said to remember them: a spoken request is not consent to store medical or money
      details, so the question is the one consent step that remains.
    """
    if mode == "off" or sensitivity is Sensitivity.CREDENTIAL:
        return Decision.SKIP
    if mode == "ask" or negated:
        # A negated fact ("I no longer live in X") retracts something already stored, so it always needs consent.
        return Decision.ASK
    if explicit and sensitivity in (Sensitivity.NORMAL, Sensitivity.THIRD_PARTY):
        return Decision.SAVE
    if sensitivity is Sensitivity.NORMAL:
        return Decision.SAVE
    return Decision.ASK


def plan_for_proposal(mode: MemoryMode, text: str, *, explicit: bool, negated: bool = False) -> Decision:
    """The decision for a proposed fact, from its text. The keyword net sets the sensitivity."""
    return decide(mode, classify_sensitivity(text), explicit=explicit, negated=negated)
