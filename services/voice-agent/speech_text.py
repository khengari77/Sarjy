"""Speech-only English unit pronunciation; never rewrite conversation records."""

import re
from collections.abc import AsyncGenerator, AsyncIterable

# Anchor each unit as a whole token: it may follow a number or space but not a
# letter, and must not be followed by a letter or digit. So "12km/h" and "20 °C"
# expand, while near-misses such as "km/hour", "Xkm/h" and "5°Celsius" stay
# literal. A separating space is only added when the unit does not already
# follow one, and replacement text is never re-scanned.
_UNITS = re.compile(r"([ \t]*)(?<![^\W\d_])(km/h|°C)(?![^\W_])")
_SPOKEN = {"km/h": "kilometres per hour", "°C": "degrees Celsius"}


def _speak(text: str) -> str:
    return _UNITS.sub(lambda match: (match.group(1) or (" " if match.start() else "")) + _SPOKEN[match.group(2)], text)


async def _expanded(text: AsyncIterable[str]) -> AsyncGenerator[str, None]:
    # Emit only complete whitespace-delimited tokens, so a unit split across
    # stream chunks is always matched in full and chunk boundaries never change
    # which characters surround it.
    buffer = ""
    async for chunk in text:
        buffer += chunk
        cut = len(buffer)
        while cut and not buffer[cut - 1].isspace():
            cut -= 1
        if cut:
            yield _speak(buffer[:cut])
            buffer = buffer[cut:]
    if buffer:
        yield _speak(buffer)


def spoken_text(text: AsyncIterable[str], language: str) -> AsyncIterable[str]:
    return _expanded(text) if language == "en" else text
