from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.budget import DEEPSEEK_MINIMUM_REQUEST_MICRO_USD
from app.domain.deepgram_voices import DEFAULT_DEEPGRAM_VOICE_ID
from app.domain.voices import parse_voice_ids


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite+aiosqlite:///./sarjy.db"
    cookie_secure: bool = False
    development_protected: bool = False
    web_service_secret: str = ""
    context_proposal_secret: str = ""
    demo_ttl_hours: int = 24
    deepgram_api_key: str = ""
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    default_llm_provider: Literal["openai_direct", "deepseek_direct"] = "deepseek_direct"
    deepseek_budget_enabled: bool = True
    deepseek_daily_micro_usd: int = 250_000
    # Shared daily ElevenLabs character reservation. Application guard, not a provider quota (#151).
    elevenlabs_daily_characters: int = Field(default=2500, ge=1)
    deepseek_request_micro_usd: int = 7_000
    eleven_api_key: str = ""
    # Text-to-speech provider (#175). Deepgram Aura-2 has no Arabic; ElevenLabs stays available.
    tts_provider: Literal["deepgram", "elevenlabs"] = "deepgram"
    # Shared daily Deepgram TTS character reservation. Application guard, not a provider quota.
    deepgram_daily_characters: int = Field(default=50_000, ge=1)
    # Jev UI-command classifier (#168). An empty key or a false flag disables the feature.
    jev_api_key: str = ""
    jev_commands_enabled: bool = True
    jev_daily_requests: int = Field(default=2000, ge=1)
    approved_voice_ids: str = ""
    default_voice_id: str = Field(default="", pattern=r"^$|^[A-Za-z0-9_-]{1,100}$")
    default_voice_language: Literal["ar", "en"] = "en"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    agent_service_secret: str = ""
    reviewer_access_enabled: bool = False
    # Accounts replace the reviewer/demo identities as the way in. Credentials live in Supabase Auth.
    account_access_enabled: bool = False
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    # Daily Wordle kill switch.  Off by default so an unconfigured deployment
    # never reaches the external source.
    wordle_enabled: bool = False
    wordle_source_url: str = "https://www.nytimes.com/svc/wordle/v2/{date}.json"
    # The *publisher's* publication schedule, not the player's local zone.  The
    # answer for date D is only published once D begins in this zone; resolving
    # "today" in a player zone would request an unpublished future date for the
    # first hours of every local day.
    wordle_timezone: str = "America/New_York"
    # Optional pre-warm hour (0-23) in ``wordle_timezone``.  When unset only
    # the protected refresh endpoint and lazy request-time fetch run.
    wordle_refresh_hour: int | None = Field(default=None, ge=0, le=23)

    @field_validator("approved_voice_ids")
    @classmethod
    def validate_approved_voice_ids(cls, value: str) -> str:
        # Fail at startup rather than admitting a malformed allow-list later.
        parse_voice_ids(value)
        return value

    @model_validator(mode="after")
    def default_deepgram_voice(self):
        # Deepgram's voice id is a model string, so an ElevenLabs id can never be a default.
        if self.tts_provider == "deepgram" and not self.default_voice_id:
            self.default_voice_id = DEFAULT_DEEPGRAM_VOICE_ID
        return self

    @model_validator(mode="after")
    def validate_wordle_timezone(self):
        # A disabled feature must never prevent boot; an empty value is the
        # ordinary compose/TZ interpolation case.
        if not self.wordle_enabled:
            return self
        try:
            ZoneInfo(self.wordle_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("WORDLE_TIMEZONE_INVALID") from exc
        return self

    @model_validator(mode="after")
    def validate_deepseek_budget(self):
        if self.deepseek_budget_enabled and not (
            DEEPSEEK_MINIMUM_REQUEST_MICRO_USD <= self.deepseek_request_micro_usd <= self.deepseek_daily_micro_usd
        ):
            raise ValueError("DEEPSEEK_BUDGET_CONFIGURATION_INVALID")
        return self

    @model_validator(mode="after")
    def validate_account_access(self):
        if not self.account_access_enabled:
            return self
        if self.reviewer_access_enabled:
            raise ValueError("ACCOUNT_ACCESS_EXCLUDES_REVIEWER_ACCESS")
        if not self.cookie_secure:
            raise ValueError("ACCOUNT_ACCESS_REQUIRES_SECURE_COOKIES")
        if not (self.development_protected and self.web_service_secret):
            raise ValueError("ACCOUNT_ACCESS_REQUIRES_API_PROTECTION")
        if not (self.supabase_url.startswith("https://") and self.supabase_publishable_key):
            raise ValueError("ACCOUNT_ACCESS_REQUIRES_SUPABASE")
        if not self.supabase_publishable_key.startswith("sb_publishable_"):
            # Refuses secret keys and legacy JWT keys, which may carry the service role.
            raise ValueError("ACCOUNT_ACCESS_REQUIRES_PUBLISHABLE_KEY")
        return self

    @model_validator(mode="after")
    def validate_reviewer_access(self):
        if not self.reviewer_access_enabled:
            return self
        if not self.cookie_secure:
            raise ValueError("REVIEWER_ACCESS_REQUIRES_SECURE_COOKIES")
        if not self.development_protected:
            raise ValueError("REVIEWER_ACCESS_REQUIRES_API_PROTECTION")
        if not self.web_service_secret:
            raise ValueError("REVIEWER_ACCESS_REQUIRES_WEB_SERVICE_SECRET")
        return self
