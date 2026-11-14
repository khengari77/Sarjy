from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.budget import DEEPSEEK_MINIMUM_REQUEST_MICRO_USD
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
    deepseek_request_micro_usd: int = 7_000
    eleven_api_key: str = ""
    approved_voice_ids: str = ""
    default_voice_id: str = Field(default="", pattern=r"^$|^[A-Za-z0-9_-]{1,100}$")
    default_voice_language: Literal["ar", "en"] = "en"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    agent_service_secret: str = ""
    demo_session_budget: int = 80
    demo_calls_per_identity: int = 3
    reviewer_access_enabled: bool = False
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
    def validate_voice_configuration(self):
        # Fail at startup, not on the first demo tap: with voice configured but no
        # approved or default voice, every session request would fail at runtime.
        if self.eleven_api_key and not (self.approved_voice_ids.strip() or self.default_voice_id):
            raise ValueError("APPROVED_VOICE_IDS or DEFAULT_VOICE_ID is required when ELEVEN_API_KEY is set")
        return self

    @model_validator(mode="after")
    def validate_deepseek_budget(self):
        if self.deepseek_budget_enabled and not (
            DEEPSEEK_MINIMUM_REQUEST_MICRO_USD <= self.deepseek_request_micro_usd <= self.deepseek_daily_micro_usd
        ):
            raise ValueError("DEEPSEEK_BUDGET_CONFIGURATION_INVALID")
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
