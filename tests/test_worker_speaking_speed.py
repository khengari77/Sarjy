"""The stored speaking speed must reach the Deepgram Aura-2 configuration.

The provider boundary is mocked: no synthesis request is made. Each case drives
the worker's real ``compile`` path with a turn snapshot and inspects the
``speed`` the plugin would use for its next request.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from livekit.plugins import deepgram
from main import SarjyAgent
from provider_factory import (
    SPEAKING_SPEED_DEFAULT,
    SPEAKING_SPEED_MAX,
    SPEAKING_SPEED_MIN,
    DeepgramSpeedController,
)


class RecordingTTS:
    """Stands in for the Deepgram plugin and records update_options calls."""

    def __init__(self):
        self.updates = []

    def update_options(self, **kwargs):
        self.updates.append(kwargs)


def snapshot(speed, *, include_settings=True):
    request = {} if not include_settings else {"settings": {"speaking_speed": speed}}
    return {"model_request": request}


def compiled(speed, *, include_settings=True, compiles=1):
    """Run the real compile() path and return the TTS it configured."""
    turn_starts = []

    def backend(request):
        assert request.url.path == "/internal/agent/turn/start"
        turn_starts.append(request)
        return httpx.Response(
            200,
            json={"turn_id": "turn-1", "context": snapshot(speed, include_settings=include_settings)},
        )

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            pipeline = {
                "llm": {"provider": "openai_direct"},
                "tts": {"provider": "deepgram_direct", "voice_id": "aura-2-thalia-en"},
            }
            agent = SarjyAgent(client, pipeline, instructions="test")
            tts = RecordingTTS()
            # Agent.session resolves through its activity; this mirrors a live session.
            agent._activity = SimpleNamespace(session=SimpleNamespace(tts=tts))
            message = SimpleNamespace(id="message-1", text_content="slower please")
            for _ in range(compiles):
                await agent.compile(message)
            return tts, len(turn_starts)

    return asyncio.run(run())


@pytest.mark.parametrize("speed", [0.8, 0.9, 1.1, 1.2])
def test_turn_snapshot_speed_reaches_the_provider_configuration(speed):
    tts, _ = compiled(speed)
    assert len(tts.updates) == 1
    assert tts.updates[0] == {"speed": speed}


def test_default_speed_leaves_the_provider_configuration_untouched():
    tts, _ = compiled(SPEAKING_SPEED_DEFAULT)
    assert tts.updates == []


def test_missing_or_legacy_settings_keep_the_provider_default():
    tts, _ = compiled(SPEAKING_SPEED_DEFAULT, include_settings=False)
    assert tts.updates == []


@pytest.mark.parametrize(
    ("stored", "applied"),
    [(0.5, SPEAKING_SPEED_MIN), (1.5, SPEAKING_SPEED_MAX), (-1, SPEAKING_SPEED_MIN), ("fast", SPEAKING_SPEED_DEFAULT)],
)
def test_speed_is_clamped_to_the_installed_plugin_interval(stored, applied):
    tts = RecordingTTS()
    control = DeepgramSpeedController("aura-2-thalia-en")
    assert control.apply(tts, stored) == applied
    if applied == SPEAKING_SPEED_DEFAULT:
        assert tts.updates == []
    else:
        assert tts.updates[0]["speed"] == applied


def test_repeated_value_does_not_invalidate_the_live_connection():
    tts = RecordingTTS()
    control = DeepgramSpeedController("aura-2-thalia-en")
    assert control.apply(tts, 0.9) == 0.9
    assert control.apply(tts, 0.9) == 0.9
    assert len(tts.updates) == 1


def test_double_compile_does_not_thrash_the_tts_connection():
    """The SDK compiles a user message twice; only the first may touch TTS."""
    tts, turn_starts = compiled(0.9, compiles=2)
    assert turn_starts == 1
    assert len(tts.updates) == 1


def test_returning_to_default_resets_a_previously_set_speed():
    tts = RecordingTTS()
    control = DeepgramSpeedController("aura-2-thalia-en")
    control.apply(tts, 0.8)
    control.apply(tts, SPEAKING_SPEED_DEFAULT)
    assert [update["speed"] for update in tts.updates] == [0.8, SPEAKING_SPEED_DEFAULT]


def test_installed_plugin_accepts_the_speed_option():
    """Evidence that the installed (unmocked) plugin supports this option."""
    tts = deepgram.TTS(model="aura-2-thalia-en", api_key="test-key")
    calls = []
    original = tts.update_options

    def recording(*, speed):
        calls.append(speed)
        return original(speed=speed)

    tts.update_options = recording
    control = DeepgramSpeedController("aura-2-thalia-en")
    assert control.apply(tts, 0.8) == 0.8
    assert calls == [0.8]
    assert control.apply(tts, 0.8) == 0.8
    assert len(calls) == 1
