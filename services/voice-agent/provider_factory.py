"""Native provider construction; only called after backend capability validation."""

from dataclasses import replace

import httpx
import openai as openai_sdk
from livekit.agents.utils import is_given
from livekit.plugins import deepgram, elevenlabs, openai
from stream_completion import ObservedTransport

# ElevenLabs exposes pace as a voice setting, not a synthesis parameter. The
# installed plugin (livekit-plugins-elevenlabs 1.8.11, VoiceSettings.speed)
# documents the interval as [0.8 - 1.2]; anything outside it is rejected by the
# provider. Keep this worker-side clamp independent of the stored settings so a
# stale or tampered snapshot can never send an unsupported value.
SPEAKING_SPEED_MIN = 0.8
SPEAKING_SPEED_MAX = 1.2
SPEAKING_SPEED_DEFAULT = 1.0
# ElevenLabs' documented defaults for the two required voice settings. They are
# only materialised when speed is set on a voice that has no explicit settings,
# so the default character of the voice is unchanged.
VOICE_STABILITY_DEFAULT = 0.5
VOICE_SIMILARITY_BOOST_DEFAULT = 0.75


class OwnedClientMixin:
    """Close the SDK client when the application factory created it."""

    def __init__(self, *, client, owns_client=False, **kwargs):
        super().__init__(client=client, **kwargs)
        self.application_client = client if owns_client else None

    async def aclose(self):
        try:
            await super().aclose()
        finally:
            if self.application_client is not None:
                await self.application_client.close()


def clamp_speaking_speed(value: object) -> float:
    """Clamp an untrusted settings value to the provider's supported interval."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return SPEAKING_SPEED_DEFAULT
    return min(max(float(value), SPEAKING_SPEED_MIN), SPEAKING_SPEED_MAX)


def _current_voice_settings(tts) -> elevenlabs.VoiceSettings | None:
    # update_options replaces the whole voice-settings object, so the existing
    # one must be read to preserve stability/similarity. The plugin has no
    # public accessor; this is the same object it serialises for every request.
    settings = getattr(getattr(tts, "_opts", None), "voice_settings", None)
    return settings if is_given(settings) else None


def apply_speaking_speed(tts, value: object) -> float:
    """Apply speaking speed to ElevenLabs for the next synthesis boundary.

    Returns the clamped speed. The provider default needs no update, and a
    repeated value must not call ``update_options``: that invalidates the live
    TTS connection and would add latency on every turn.
    """
    speed = clamp_speaking_speed(value)
    current = _current_voice_settings(tts)
    if current is None and speed == SPEAKING_SPEED_DEFAULT:
        return speed
    current_speed = getattr(current, "speed", None)
    if current is not None and is_given(current_speed) and float(current_speed) == speed:
        return speed
    voice_settings = (
        replace(current, speed=speed)
        if current is not None
        else elevenlabs.VoiceSettings(
            stability=VOICE_STABILITY_DEFAULT,
            similarity_boost=VOICE_SIMILARITY_BOOST_DEFAULT,
            speed=speed,
        )
    )
    tts.update_options(voice_settings=voice_settings)
    return speed


class OwnedDeepSeekLLM(OwnedClientMixin, openai.LLM):
    pass


class OwnedOpenAIResponsesLLM(OwnedClientMixin, openai.responses.LLM):
    pass


def build_llm(config: dict, credentials: dict[str, str], *, client=None):
    choice = config["llm"]
    if choice["provider"] == "openai_direct" and choice["model"] == "gpt-6-luna":
        llm = OwnedOpenAIResponsesLLM(
            owns_client=client is None,
            model="gpt-6-luna",
            client=client or openai_sdk.AsyncOpenAI(api_key=credentials["OPENAI_API_KEY"], max_retries=0),
            use_websocket=False,
            reasoning={"effort": "none"},
            service_tier="default",
            store=False,
            prompt_cache_breakpoints=False,
        )
    elif choice["provider"] == "deepseek_direct" and choice["model"] == "deepseek-flash":
        owns_client = client is None
        sdk_client = client or openai_sdk.AsyncOpenAI(
            api_key=credentials["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=ObservedTransport()),
        )
        llm = OwnedDeepSeekLLM(
            owns_client=owns_client,
            model=choice["model"],
            api_key=credentials["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/v1",
            client=sdk_client,
            max_retries=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
    else:
        raise ValueError("Unsupported LLM configuration")
    return llm


def build_fallback_llm(credentials: dict[str, str], *, client=None):
    """Optional OpenAI fallback; absent without an approved OpenAI key."""
    if not credentials.get("OPENAI_API_KEY"):
        return None
    return build_llm(
        {"llm": {"provider": "openai_direct", "model": "gpt-6-luna"}},
        credentials,
        client=client,
    )


def build_providers(config: dict, credentials: dict[str, str]):
    primary = config["llm"]["provider"]
    return {
        "stt": deepgram.STT(
            model=config["stt"]["model"], language=config["stt"]["language"], api_key=credentials["DEEPGRAM_API_KEY"]
        ),
        "llm": build_llm(config, credentials),
        "tts": elevenlabs.TTS(
            model=config["tts"]["model"],
            voice_id=config["tts"]["voice_id"],
            language=config["tts"]["language"],
            api_key=credentials["ELEVEN_API_KEY"],
            enable_logging=False,
        ),
        "fallback_llm": build_fallback_llm(credentials) if primary == "deepseek_direct" else None,
    }
