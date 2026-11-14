import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st
from speech_text import spoken_text


def render(parts, language="en"):
    async def source():
        for part in parts:
            yield part

    async def run():
        return "".join([part async for part in spoken_text(source(), language)])

    return asyncio.run(run())


def test_weather_units_are_expanded_only_for_speech():
    original = "Temperature -2.5°C; wind 15 km/h."
    assert render([original]) == "Temperature -2.5 degrees Celsius; wind 15 kilometres per hour."
    assert original == "Temperature -2.5°C; wind 15 km/h."
    assert render(["Units: km/h and °C"]) == "Units: kilometres per hour and degrees Celsius"


@pytest.mark.parametrize(
    "text",
    [
        "15 km/hour and Xkm/h.",  # unit substring extended by letters
        "5°Celsius and 5°Cx.",  # letter after the unit
        "km, C, kmh and °x stay literal.",  # no unit at all
        "12km/h2 and 20°C9 stay literal.",  # digit after the unit
    ],
)
def test_near_miss_tokens_are_byte_identical(text):
    # Only whole tokens expand: anything merely containing a unit substring must
    # come out exactly as it went in.
    assert render([text]) == text


def test_legitimate_units_expand_exactly_once():
    assert render(["15 km/h 20°C"]) == "15 kilometres per hour 20 degrees Celsius"
    assert render(["15 km/h"]) == "15 kilometres per hour"
    # Replacement output is never re-scanned, so spoken words are not re-expanded.
    assert render(["kilometres per hour and degrees Celsius"]) == "kilometres per hour and degrees Celsius"


@given(st.lists(st.integers(min_value=0, max_value=64), max_size=40))
def test_pronunciation_is_independent_of_stream_chunk_boundaries(cuts):
    text = "Wind 12km/h, now 20°C; later -3.5 °C. km/hour, Xkm/h and 5°Celsius stay literal."
    points = sorted({0, len(text), *(min(c, len(text)) for c in cuts)})
    parts = [text[a:b] for a, b in zip(points, points[1:], strict=False)]
    assert render(parts) == render([text])


def test_unknown_units_and_other_languages_are_preserved():
    text = "مقدار 20°C و 12km/h; kg, CO2 and C are unchanged."
    assert render(list(text), "ar") == text
    assert render(["No units, just C and km."]) == "No units, just C and km."
