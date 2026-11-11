"""Speech-only English unit pronunciation; never rewrite conversation records."""
from collections.abc import AsyncIterable
from livekit.agents import text_transforms

# Prefer the longer, space-prefixed spelling so compact and already-spaced
# quantities both produce a single word separator before the spoken unit.
_ENGLISH_UNITS = text_transforms.replace({
    " km/h": " kilometres per hour",
    "km/h": " kilometres per hour",
    " °C": " degrees Celsius",
    "°C": " degrees Celsius",
}, case_sensitive=True)


def spoken_text(text: AsyncIterable[str], language: str) -> AsyncIterable[str]:
    return _ENGLISH_UNITS(text) if language == 'en' else text
