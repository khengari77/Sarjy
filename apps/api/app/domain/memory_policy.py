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

# Unmistakable credential terms only. Kept deliberately small and well defined: a wider pattern trips on ordinary
# facts ("I collect pin badges"). Ambiguous words, paraphrases, other languages and negation are judged by the
# background observer (Jev), never by growing this list.
# Arabic proclitics (و، ب، ل، ف، ك) are word characters, so a leading ``\b`` hides "وكلمة المرور". The term may
# appear anywhere; a value or assignment shape is still required so ordinary prose ("I forgot the password") does not
# match. A bare Arabic noun ("credit card") belongs in FINANCE, not here.
_ARABIC_CREDENTIAL = (
    r"(?:[وفبلك]\s*)?(?:كلمة\s+(?:ال)?(?:مرور|سر)|رمز\s+(?:التحقق|الدخول|الأمان|سري|السر)|"
    r"الرمز\s+السري|للرمز\s+السري|الرقم\s+السري|للرقم\s+السري|رقم\s+(?:الهوية|البطاقة)|"
    r"عبارة\s+الاسترداد|مفتاح\s+(?:سري|الapi|ssh))"
)
# The assignment markers are words too: "سهو" must not read as "هو". Arabic letters (not digits/punctuation) bound them.
_ARABIC_LETTER = r"\u0621-\u064A\u066E-\u06D3\u06FA-\u06FF"
_ARABIC_MARKER = (
    rf"(?:(?<![{_ARABIC_LETTER}])هو(?![{_ARABIC_LETTER}])|"
    rf"(?<![{_ARABIC_LETTER}])هي(?![{_ARABIC_LETTER}])|:|=)"
)
# The value may follow directly, after up to a few ordinary words, or after an assignment marker. A term with no
# value or assignment ("I forgot the password") is ordinary prose and stays out.
_ARABIC_CREDENTIAL_VALUE = (
    rf"(?:[^\n]{{0,20}}?{_ARABIC_MARKER}\s*\S{{1,20}}|"
    r"\s+(?:[^\s]+\s+){0,3}[0-9٠-٩A-Za-z][\w!@#$%^&*._-]{0,19})"
)

_CREDENTIAL = re.compile(
    r"\b(?:password|passcode|passphrase|pin (?:code|number)|cvv|cvc|api[ -]?(?:key|secret)|private key|ssh[ -]?key|"
    r"seed phrase|recovery (?:code|phrase)|one[- ]time (?:code|password)|backup code|otp|social security|ssn|"
    r"national id|passport number|safe combination|security code|(?:2fa|two[- ]factor) code)s?\b"
    # A PIN given as digits: "my pin is 1234". Fixed shape, so "pin badges" or "pin it to the wall" never match.
    r"|\bpin\s*(?:is|:|=)\s*\d"
    # Token and key literals that are unmistakable even without a label.
    r"|(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,}|xox[abprs]-|eyJ[A-Za-z0-9_-]{10,}\.)"
    # "my openai token is ..." / "secret: ..." (a short ordinary value like "the token budget" does not match).
    r"|\b(?:api[ _-]?key|token|secret)\s*(?:is|:|=)\s*\S{6,}"
    # Arabic credential terms with an optional proclitic and a value/assignment.
    rf"|{_ARABIC_CREDENTIAL}{_ARABIC_CREDENTIAL_VALUE}",
    re.IGNORECASE,
)
_FINANCE = re.compile(
    r"\b(?:salary|income|net worth|debt|loan|mortgage|credit card|card number|bank account|account number|iban|"
    r"routing number|tax(?:es)?|bankrupt\w*|invest\w*|savings|بطاقة\s+(?:ال)?ائتمان)\b",
    re.IGNORECASE,
)
_HEALTH = re.compile(
    r"\b(?:diagnos\w+|medication|prescri\w+|therapy|therapist|depress\w+|anxiety|diabet\w+|cancer|pregnan\w+|"
    r"hiv|allerg\w+|surgery|disorder|illness|disease|doctor|medical|blood pressure|metformin|insulin)\b",
    re.IGNORECASE,
)
# Arabic-Indic and Persian digits become ASCII before any count, and punctuation (comma, en/em dash, Arabic comma,
# NBSP, slash, underscore, ...) is a separator rather than a break. A run therefore spans "4,012–8888–8888–1881".
_DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_TIME = re.compile(
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?)?\b"
    r"|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    rf"|\b{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+{_MONTH}\.?,?\s+\d{{4}}\b",
    re.IGNORECASE,
)


def _digit_runs(text: str) -> list[tuple[str, bool]]:
    """Digit runs after date/time exclusion; separators do not break a run, letters do. ``plus`` tracks a leading +."""
    normalised = _DATE_TIME.sub(" ", text.translate(_DIGIT_TRANSLATION))
    runs: list[tuple[str, bool]] = []
    current: list[str] = []
    plus = False
    pending_plus = False
    for character in normalised:
        if character.isdigit():
            if not current:
                plus = pending_plus
            current.append(character)
            pending_plus = False
        elif character.isalpha():
            if current:
                runs.append(("".join(current), plus))
                current = []
            pending_plus = False
        elif character == "+":
            pending_plus = True
    if current:
        runs.append(("".join(current), plus))
    return runs


def looks_like_phone(text: str) -> bool:
    """A card/phone/ID run: >= 9 digits, or a leading + with a country-shaped run of at least 7 digits."""
    return any(len(run) >= 9 or (plus and len(run) >= 7) for run, plus in _digit_runs(text))


def has_long_number(text: str) -> bool:
    """Nine or more digits in a row (a card, phone or ID number): never saved without asking, whatever the words say."""
    return looks_like_phone(text)


_THIRD_PARTY = re.compile(
    r"\b(?:my (?:boss|ex|wife|husband|partner|girlfriend|boyfriend|mother|father|mom|dad|sister|brother|friend|"
    r"coworker|colleague|neighbou?r|landlord)(?:'s)?)\b",
    re.IGNORECASE,
)


def classify_sensitivity(text: str) -> Sensitivity:
    """Keyword safety net. Deterministic; the strongest matching class wins."""
    if has_long_number(text):
        return Sensitivity.CREDENTIAL
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
