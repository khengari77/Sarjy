"""Native provider construction; only called after backend capability validation."""

from typing import Any

import httpx
import openai as openai_sdk
from app.domain.deepgram_voices import DEFAULT_DEEPGRAM_VOICE_ID, deepgram_voice
from livekit.plugins import deepgram, openai
from stream_completion import KEEPALIVE_LIMITS, HttpWarmup, ObservedTransport

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# The contract interval is [0.8 - 1.2]; Deepgram accepts a subset of its own
# [0.7 - 1.5] range. Keep this worker-side clamp independent of the stored
# settings so a stale or tampered snapshot can never send an unsupported value.
SPEAKING_SPEED_MIN = 0.8
SPEAKING_SPEED_MAX = 1.2
SPEAKING_SPEED_DEFAULT = 1.0


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


class DeepgramSpeedController:
    """Apply pace to Deepgram Aura-2 through the plugin's public ``update_options``.

    Deepgram accepts a per-request ``speed`` (0.7-1.5) for English and Spanish
    voices only. The contract range 0.8-1.2 is a subset. An unsupported voice is
    refused honestly: nothing is sent and ``None`` is returned. ``update_options``
    is only called when the value changes, since it refreshes the connection pool.
    """

    def __init__(self, voice_id: str = "") -> None:
        self.voice_id = voice_id
        self._tts: Any = None
        self._applied: float | None = None

    @staticmethod
    def supports_speed(voice_id: str) -> bool:
        return voice_id.endswith(("-en", "-es"))

    def apply(self, tts: Any, value: object) -> float | None:
        if not self.supports_speed(self.voice_id):
            return None
        speed = clamp_speaking_speed(value)
        if tts is self._tts and speed == self._applied:
            return speed
        if speed == SPEAKING_SPEED_DEFAULT and (tts is not self._tts or self._applied is None):
            self._tts, self._applied = tts, speed
            return speed
        tts.update_options(speed=speed)
        self._tts, self._applied = tts, speed
        return speed


def speed_controller_for(pipeline: dict) -> DeepgramSpeedController:
    return DeepgramSpeedController((pipeline.get("tts") or {}).get("voice_id") or "")


def tts_budget_provider(pipeline: dict) -> str:
    """Shared-ledger key for TTS; Deepgram Aura-2 is the only provider."""
    return "deepgram_tts"


def build_tts(config: dict, credentials: dict[str, str]):
    choice = config["tts"]
    voice_id = choice.get("voice_id")
    if choice.get("provider") != "deepgram_direct" or deepgram_voice(voice_id) is None:
        # An unknown or previously saved ElevenLabs provider/voice falls back
        # rather than sending an unsupported model string to Deepgram.
        voice_id = DEFAULT_DEEPGRAM_VOICE_ID
    if choice.get("language", "en") != "en":
        raise ValueError("TTS_LANGUAGE_UNSUPPORTED")
    return deepgram.TTS(
        model=voice_id,
        encoding="linear16",
        sample_rate=24000,
        api_key=credentials["DEEPGRAM_API_KEY"],
    )


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
        http_client = None
        if client is None:
            http_client = httpx.AsyncClient(
                transport=ObservedTransport(httpx.AsyncHTTPTransport(retries=0, limits=KEEPALIVE_LIMITS))
            )
            sdk_client = openai_sdk.AsyncOpenAI(
                api_key=credentials["DEEPSEEK_API_KEY"],
                base_url=DEEPSEEK_BASE_URL,
                max_retries=0,
                http_client=http_client,
            )
        else:
            sdk_client = client
        llm = OwnedDeepSeekLLM(
            owns_client=client is None,
            model=choice["model"],
            api_key=credentials["DEEPSEEK_API_KEY"],
            base_url=DEEPSEEK_BASE_URL,
            client=sdk_client,
            max_retries=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        if http_client is not None:
            # Same pool the SDK uses for completions, so this connection is the one the turn reuses.
            llm.warm_connection = HttpWarmup(
                http_client, f"{DEEPSEEK_BASE_URL}/models", credentials["DEEPSEEK_API_KEY"]
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
        "tts": build_tts(config, credentials),
        "fallback_llm": build_fallback_llm(credentials) if primary == "deepseek_direct" else None,
    }
