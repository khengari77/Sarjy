"""Pure recognition of user-authored memory candidates.

Only explicit remembering or a small set of ordinary first-person statements
produce a candidate.  The result is data, never a write: the service shell owns
authorization, consent, embedding reservations and durable claims.
"""

import hashlib
import re
from dataclasses import dataclass

MAX_CAPTURE_CHARS = 4000
_WHITESPACE = re.compile(r"\s+")
_EXPLICIT = (
    re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s+(?P<content>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:please\s+)?(?:don't|do not)\s+forget(?:\s+that)?\s+(?P<content>.+?)\s*$", re.IGNORECASE),
)
_HYPOTHETICAL = re.compile(r"\b(?:if|would|could|suppose|supposing|imagine|maybe|perhaps)\b", re.IGNORECASE)
_REPLACEMENT = re.compile(
    r"(?:\b(?:now|instead|actually)\s*[.!]?$)"
    r"|\b(?:no\s+longer|not\s+anymore|changed\s+my\s+mind|from\s+now\s+on)\b",
    re.IGNORECASE,
)
_PRONOUN_VALUES = frozenset({"it", "this", "that", "them", "him", "her", "those", "these", "something", "anything"})
_REFERENTIAL_WORDS = frozenset(
    {
        "it",
        "this",
        "that",
        "these",
        "those",
        "them",
        "him",
        "her",
        "one",
        "ones",
        "thing",
        "things",
        "something",
        "anything",
        "everything",
        "nothing",
        "stuff",
    }
)
_REFERENTIAL_FILLERS = frozenset(
    {
        "a",
        "an",
        "the",
        "my",
        "our",
        "your",
        "his",
        "its",
        "their",
        "some",
        "any",
        "please",
        "again",
        "too",
        "also",
        # Leading save imperatives carry no fact of their own; without these
        # "remember it" / "save that" would not be recognised as referential.
        "remember",
        "save",
        "keep",
        "note",
        "store",
    }
)
_SLOT_STOPWORDS = frozenset(
    {"a", "an", "the", "my", "our", "your", "his", "her", "its", "their", "now", "instead", "actually", "please"}
)
_ATTRIBUTE_STOPWORDS = frozenset({"point", "question", "issue", "problem", "thing", "idea", "reason", "story"})
_PREFERENCE_TOPICS = {
    "beverage": frozenset({"tea", "coffee", "water", "juice", "milk", "wine", "beer", "soda", "drink", "drinks"}),
    "music": frozenset({"jazz", "rock", "pop", "classical", "music", "songs", "song", "rap", "metal"}),
    "food": frozenset({"pizza", "pasta", "food", "rice", "bread", "spicy", "sweet", "meat", "fish"}),
    "travel": frozenset({"travel", "trips", "trip", "flying", "trains", "beach", "mountains"}),
}
_NON_REMEMBERABLE = re.compile(
    r"^\s*(?:don't|do not|never)\s+remember\b|\bnever\s+mind\b|\bforget\s+(?:it|that|this)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r'^\s*["“\'].*["”\']\s*$', re.DOTALL)
_ORDINARY = (
    (re.compile(r"^i\s+(?:really\s+)?(?:prefer|like|love|enjoy)\s+(?P<value>.+)$", re.IGNORECASE), "preference"),
    (re.compile(r"^i\s+(?:live|stay)\s+in\s+(?P<value>.+)$", re.IGNORECASE), "location"),
    (re.compile(r"^i\s+(?:work|am\s+working)\s+(?:at|for|in)\s+(?P<value>.+)$", re.IGNORECASE), "work"),
    (re.compile(r"^i\s+am\s+allergic\s+to\s+(?P<value>.+)$", re.IGNORECASE), "diet"),
    (re.compile(r"^i\s+am\s+(?P<value>vegetarian|vegan)$", re.IGNORECASE), "diet"),
    (re.compile(r"^i\s+(?:am|'m)\s+from\s+(?P<value>.+)$", re.IGNORECASE), "origin"),
    (re.compile(r"^call\s+me\s+(?P<value>.+)$", re.IGNORECASE), "name"),
    (re.compile(r"^my\s+(?P<key>[a-z][a-z _-]{1,40}?)\s+(?:is|are)\s+(?P<value>.+)$", re.IGNORECASE), "attribute"),
)


_NEGATED = (
    (
        re.compile(r"^i\s+(?:no\s+longer|don'?t|do\s+not)\s+(?:live|stay)\s+in\s+(?P<value>.+)$", re.IGNORECASE),
        "location",
    ),
    (
        re.compile(r"^i\s+(?:am\s+)?(?:no\s+longer|not)\s+allergic\s+to\s+(?P<value>.+)$", re.IGNORECASE),
        "diet",
    ),
    (
        re.compile(
            r"^i\s+(?:no\s+longer|don'?t|do\s+not)\s+(?:work|work\s+at|work\s+for|work\s+in)\s+(?P<value>.+)$",
            re.IGNORECASE,
        ),
        "work",
    ),
    (
        re.compile(
            r"^i\s+(?:no\s+longer|don'?t|do\s+not)\s+(?:prefer|like|love|enjoy)\s+(?P<value>.+?)(?:\s+anymore)?$",
            re.IGNORECASE,
        ),
        "preference",
    ),
)
_NEGATION_PREFIX = re.compile(r"^i\s+(?:no\s+longer|don'?t|do\s+not|am\s+not|never)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MemoryCapture:
    """A candidate fact and the slot used for contradiction detection."""

    text: str
    subject: str | None
    value: str
    explicit: bool
    replaces: bool = False
    negated: bool = False


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().strip(".,;:!? ")


def _head_word(value: str) -> str:
    words = [word.strip(".,;:!?\"'") for word in value.casefold().split()]
    significant = [word for word in words if word and word not in _SLOT_STOPWORDS]
    return significant[-1] if significant else value.casefold()


def _ordinary(text: str) -> tuple[str, str] | None:
    for pattern, subject in _ORDINARY:
        match = pattern.match(text)
        if match is None:
            continue
        value = _normalize(match.group("value"))
        if not value or value.casefold() in _PRONOUN_VALUES:
            return None
        if subject == "attribute":
            key = _normalize(match.group("key")).casefold()
            if key in _ATTRIBUTE_STOPWORDS:
                return None
            return f"attribute:{key}", value
        if subject == "preference":
            words = set(value.casefold().replace(",", " ").split())
            topic = next(
                (name for name, vocabulary in _PREFERENCE_TOPICS.items() if words & vocabulary),
                "",
            )
            return (f"preference:{topic}" if topic else f"preference:{_head_word(value)}"), value
        if subject == "diet":
            return f"diet:{value.casefold()}", value
        return subject, value
    return None


def _negated(text: str) -> tuple[str, str] | None:
    for pattern, subject in _NEGATED:
        match = pattern.match(text)
        if match is None:
            continue
        value = _normalize(match.group("value"))
        if not value or value.casefold() in _PRONOUN_VALUES:
            return None
        if subject == "preference":
            words = set(value.casefold().replace(",", " ").split())
            topic = next((name for name, vocabulary in _PREFERENCE_TOPICS.items() if words & vocabulary), "")
            return (f"preference:{topic}" if topic else f"preference:{_head_word(value)}"), value
        if subject == "diet":
            return f"diet:{value.casefold()}", value
        return subject, value
    return None


def _explicit_subject(content: str) -> str:
    """A stable, non-null subject for a remembered fact with no recognized slot."""
    return "explicit:" + hashlib.sha256(content.casefold().encode()).hexdigest()[:32]


def resolve_subject(content: str) -> str:
    """Resolve the subject a new memory row must carry.

    Only rows written before the subject column (or arbitrary content that the
    recognizer ignores) may be NULL. New captures always resolve to a stable
    slot so they can never silently fall back into the legacy NULL scan.
    """
    capture = parse_capture(content)
    if capture is not None and capture.subject:
        return capture.subject
    return _explicit_subject(_normalize(content) or content)


def is_referential(content: str) -> bool:
    """True when the content carries no fact of its own, only a back-reference.

    ``remember it`` / ``remember that thing`` can acknowledge a pending
    proposal but must never be stored as a fact on its own.
    """
    words = [word.strip(".,;:!?\"'()").casefold() for word in content.split()]
    significant = [word for word in words if word and word not in _REFERENTIAL_FILLERS]
    return not significant or all(word in _REFERENTIAL_WORDS for word in significant)


def _capture_for(content: str, *, explicit: bool) -> MemoryCapture | None:
    negated_slot = _negated(content)
    slot = _ordinary(content) or negated_slot
    replaces = bool(_REPLACEMENT.search(content)) or negated_slot is not None
    if slot is None:
        if _NEGATION_PREFIX.match(content):
            return None
        return MemoryCapture(
            text=content,
            subject=_explicit_subject(content),
            value=content.casefold(),
            explicit=explicit,
            replaces=replaces,
            negated=False,
        )
    subject, value = slot
    return MemoryCapture(
        text=content,
        subject=subject,
        value=value.casefold(),
        explicit=explicit,
        replaces=replaces,
        negated=negated_slot is not None,
    )


def parse_capture(text: str) -> MemoryCapture | None:
    """Return a candidate for an explicit or ordinary first-person statement."""
    if text.strip().endswith("?"):
        return None
    candidate = _normalize(text)
    if not candidate or len(candidate) > MAX_CAPTURE_CHARS:
        return None
    if _NON_REMEMBERABLE.search(candidate) or _HYPOTHETICAL.search(candidate):
        return None

    explicit = False
    content = candidate
    for pattern in _EXPLICIT:
        match = pattern.match(candidate)
        if match is not None:
            content = _normalize(match.group("content"))
            explicit = True
            break
    if not content or len(content) > MAX_CAPTURE_CHARS:
        return None
    if not explicit:
        if _QUOTED.match(content):
            return None
        if _ordinary(content) is None and _negated(content) is None:
            return None

    return _capture_for(content, explicit=explicit)
