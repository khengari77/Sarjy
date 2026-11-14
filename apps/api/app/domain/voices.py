"""Pure voice-identifier rules shared by configuration and request validation."""

from __future__ import annotations

import re

VOICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def parse_voice_ids(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated approval list, rejecting malformed identifiers.

    Empty entries and duplicates are ignored so operators can format the list
    for readability without changing its meaning.
    """

    identifiers = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if any(not VOICE_ID_PATTERN.fullmatch(identifier) for identifier in identifiers):
        raise ValueError("VOICE_IDS_INVALID")
    return identifiers
