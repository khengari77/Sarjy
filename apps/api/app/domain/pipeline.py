from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, computed_field, model_validator

from app.domain.contracts import PREFERENCE_MAX_TIER, TIER_CAPS, ResponsePreference


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str
    language: str = "ar"
    voice_id: str | None = None


class PipelineConfig(BaseModel):
    """Resolved session configuration. The output cap is derived, never supplied."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    runtime: Literal["livekit_remote"] = "livekit_remote"
    stt: ModelChoice
    llm: ModelChoice
    tts: ModelChoice
    response_tier: int = Field(default=2, ge=1, le=len(TIER_CAPS))
    response_preference: ResponsePreference = ResponsePreference.AUTOMATIC
    revision: int = 2

    @computed_field
    @property
    def max_output_tokens(self) -> int:
        """Hard provider ceiling for the resolved tier; the instruction targets less."""
        return TIER_CAPS[self.response_tier - 1]


PRESET = {"stt": ("deepgram_direct", "nova-3"), "llm": ("openai_direct", "gpt-6-luna"), "tts": ("elevenlabs_direct", "eleven_flash_v2_5")}
LLM_CHOICES = {("openai_direct", "gpt-6-luna"): "OPENAI_API_KEY", ("deepseek_direct", "deepseek-flash"): "DEEPSEEK_API_KEY"}
KEYS = {"stt": "DEEPGRAM_API_KEY", "llm": "OPENAI_API_KEY", "tts": "ELEVEN_API_KEY"}


def validate_pipeline(config: PipelineConfig, configured: set[str]) -> list[dict]:
    errors = []
    for role in PRESET:
        choice = getattr(config, role)
        if choice.language not in {"ar", "en"}:
            errors.append({"role": role, "code": "UNSUPPORTED_LANGUAGE"})
        if role == "tts" and choice.language == "ar" and choice.provider == "deepgram_direct":
            errors.append({"role": role, "code": "UNSUPPORTED_LANGUAGE"})
        elif (choice.provider, choice.model) not in (LLM_CHOICES if role == "llm" else {PRESET[role]}):
            errors.append({"role": role, "code": "UNSUPPORTED_MODEL"})
        key = LLM_CHOICES.get((choice.provider, choice.model)) if role == "llm" else KEYS[role]
        if key and key not in configured:
            errors.append({"role": role, "code": "MISSING_CREDENTIAL"})
        if role == "tts" and not choice.voice_id:
            errors.append({"role": role, "code": "VOICE_REQUIRED"})
    return errors
