"""Pure recognition of user-authored memory candidates.

Only explicit remembering or a small set of ordinary first-person statements
produce a candidate.  The result is data, never a write: the service shell owns
authorization, consent, embedding reservations and durable claims.
"""

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
    r"\b(?:now|instead|actually|no\s+longer|not\s+anymore|changed\s+my\s+mind|from\s+now\s+on)\b",
    re.IGNORECASE,
)
_PRONOUN_VALUES = frozenset({"it", "this", "that", "them", "him", "her", "those", "these", "something", "anything"})
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
    (re.compile(r"^i\s+am\s+(?:allergic\s+to|vegetarian|vegan)\b(?P<value>.*)$", re.IGNORECASE), "diet"),
    (re.compile(r"^i\s+(?:am|'m)\s+from\s+(?P<value>.+)$", re.IGNORECASE), "origin"),
    (re.compile(r"^call\s+me\s+(?P<value>.+)$", re.IGNORECASE), "name"),
    (re.compile(r"^my\s+(?P<key>[a-z][a-z _-]{1,40}?)\s+(?:is|are)\s+(?P<value>.+)$", re.IGNORECASE), "attribute"),
)


@dataclass(frozen=True)
class MemoryCapture:
    """A candidate fact and the slot used for contradiction detection."""

    text: str
    subject: str | None
    value: str
    explicit: bool
    replaces: bool = False


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().strip(".,;:!? ")


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
            return (f"preference:{topic}" if topic else f"preference:{value.casefold()}"), value
        if subject == "diet":
            return f"diet:{value.casefold()}", value
        return subject, value
    return None


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
        if _ordinary(content) is None:
            return None

    replaces = bool(_REPLACEMENT.search(content))
    slot = _ordinary(content)
    if slot is None:
        return MemoryCapture(text=content, subject=None, value=content.casefold(), explicit=explicit, replaces=replaces)
    subject, value = slot
    return MemoryCapture(text=content, subject=subject, value=value.casefold(), explicit=explicit, replaces=replaces)
