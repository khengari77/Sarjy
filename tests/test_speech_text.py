import asyncio
from hypothesis import given, strategies as st
from speech_text import spoken_text


def render(parts, language='en'):
    async def source():
        for part in parts:
            yield part
    async def run():
        return ''.join([part async for part in spoken_text(source(), language)])
    return asyncio.run(run())


def test_weather_units_are_expanded_only_for_speech():
    original = 'Temperature -2.5°C; wind 15 km/h.'
    assert render([original]) == 'Temperature -2.5 degrees Celsius; wind 15 kilometres per hour.'
    assert original == 'Temperature -2.5°C; wind 15 km/h.'
    assert render(['Units: km/h and °C']) == 'Units: kilometres per hour and degrees Celsius'


@given(st.lists(st.integers(min_value=0, max_value=64), max_size=40))
def test_pronunciation_is_independent_of_stream_chunk_boundaries(cuts):
    text = 'Wind 12km/h, now 20°C; later -3.5 °C. C and km remain literal.'
    points = sorted({0, len(text), *(min(c, len(text)) for c in cuts)})
    parts = [text[a:b] for a,b in zip(points,points[1:])]
    assert render(parts) == render([text])


def test_unknown_units_and_other_languages_are_preserved():
    text = 'مقدار 20°C و 12km/h; kg, CO2 and C are unchanged.'
    assert render(list(text), 'ar') == text
    assert render(['No units, just C and km.']) == 'No units, just C and km.'
