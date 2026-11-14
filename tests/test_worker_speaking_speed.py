"""The stored speaking speed must reach the ElevenLabs configuration.

The provider boundary is mocked: no synthesis request is made. Each case drives
the worker's real ``compile`` path with a turn snapshot and inspects the
``voice_settings`` the plugin would serialise for its next request.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import elevenlabs
from main import SarjyAgent
from provider_factory import (
    SPEAKING_SPEED_DEFAULT,
    SPEAKING_SPEED_MAX,
    SPEAKING_SPEED_MIN,
    apply_speaking_speed,
)


class RecordingTTS:
    """Stands in for the ElevenLabs plugin and records update_options calls."""

    def __init__(self):
        self._opts = SimpleNamespace(voice_settings=NOT_GIVEN)
        self.updates = []

    def update_options(self, *, voice_settings):
        self.updates.append(voice_settings)
        self._opts.voice_settings = voice_settings


def snapshot(speed, *, include_settings=True):
    request = {} if not include_settings else {"settings": {"speaking_speed": speed}}
    return {"model_request": request}


def compiled_tts(speed, *, include_settings=True):
    """Run one real compile() and return the TTS instance it configured."""

    def backend(request):
        assert request.url.path == "/internal/agent/turn/start"
        return httpx.Response(
            200,
            json={"turn_id": "turn-1", "context": snapshot(speed, include_settings=include_settings)},
        )

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}}, instructions="test")
            tts = RecordingTTS()
            # Agent.session resolves through its activity; this mirrors a live session.
            agent._activity = SimpleNamespace(session=SimpleNamespace(tts=tts))
            await agent.compile(SimpleNamespace(id="message-1", text_content="slower please"))
            return tts

    return asyncio.run(run())


@pytest.mark.parametrize("speed", [0.8, 0.9, 1.1, 1.2])
def test_turn_snapshot_speed_reaches_the_provider_configuration(speed):
    tts = compiled_tts(speed)
    assert len(tts.updates) == 1
    assert tts.updates[0].speed == speed
    # The default voice character is preserved when only pace is overridden.
    assert tts.updates[0].stability == 0.5
    assert tts.updates[0].similarity_boost == 0.75


def test_default_speed_leaves_the_provider_configuration_untouched():
    tts = compiled_tts(SPEAKING_SPEED_DEFAULT)
    assert tts.updates == []


def test_missing_or_legacy_settings_keep_the_provider_default():
    assert compiled_tts(SPEAKING_SPEED_DEFAULT, include_settings=False).updates == []


@pytest.mark.parametrize(
    ("stored", "applied"),
    [(0.5, SPEAKING_SPEED_MIN), (1.5, SPEAKING_SPEED_MAX), (-1, SPEAKING_SPEED_MIN), ("fast", SPEAKING_SPEED_DEFAULT)],
)
def test_speed_is_clamped_to_the_installed_plugin_interval(stored, applied):
    tts = RecordingTTS()
    assert apply_speaking_speed(tts, stored) == applied
    if applied == SPEAKING_SPEED_DEFAULT:
        assert tts.updates == []
    else:
        assert tts.updates[0].speed == applied


def test_repeated_value_does_not_invalidate_the_live_connection():
    tts = RecordingTTS()
    assert apply_speaking_speed(tts, 0.9) == 0.9
    assert apply_speaking_speed(tts, 0.9) == 0.9
    assert len(tts.updates) == 1


def test_existing_voice_settings_are_preserved_when_speed_changes():
    tts = RecordingTTS()
    tts._opts.voice_settings = elevenlabs.VoiceSettings(stability=0.3, similarity_boost=0.6, speed=0.9)
    apply_speaking_speed(tts, 1.1)
    assert tts.updates[0].stability == 0.3
    assert tts.updates[0].similarity_boost == 0.6
    assert tts.updates[0].speed == 1.1


def test_installed_plugin_accepts_and_retains_the_speed():
    """Evidence that the installed (unmocked) plugin supports this option."""
    tts = elevenlabs.TTS(
        model="eleven_flash_v2_5", voice_id="voice", language="en", api_key="test-key", enable_logging=False
    )
    assert tts._opts.voice_settings is NOT_GIVEN
    apply_speaking_speed(tts, 0.8)
    assert tts._opts.voice_settings.speed == 0.8
    revision = tts._opts_revision
    apply_speaking_speed(tts, 0.8)
    assert tts._opts_revision == revision
