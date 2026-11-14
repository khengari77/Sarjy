from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.budget import DEEPSEEK_MINIMUM_REQUEST_MICRO_USD


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
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    agent_service_secret: str = ""
    demo_session_budget: int = 80
    demo_calls_per_identity: int = 3
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

    @model_validator(mode="after")
    def validate_deepseek_budget(self):
        if self.deepseek_budget_enabled and not (
            DEEPSEEK_MINIMUM_REQUEST_MICRO_USD <= self.deepseek_request_micro_usd <= self.deepseek_daily_micro_usd
        ):
            raise ValueError("DEEPSEEK_BUDGET_CONFIGURATION_INVALID")
        return self
