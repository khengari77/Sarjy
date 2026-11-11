from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    language: str = "ar"
    voice_id: str | None = None

class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime: Literal["livekit_remote"] = "livekit_remote"
    stt: ModelChoice
    llm: ModelChoice
    tts: ModelChoice
    max_output_tokens: int = Field(default=96, ge=32, le=800)
    revision: int = 1

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
