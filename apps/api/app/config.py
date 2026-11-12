from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite+aiosqlite:///./sarjy.db'
    cookie_secure: bool = False
    development_protected: bool = False
    web_service_secret: str = ''
    context_proposal_secret: str = ''
    demo_ttl_hours: int = 24
    deepgram_api_key: str = ''
    deepseek_api_key: str = ''
    openai_api_key: str = ''
    default_llm_provider: Literal['openai_direct','deepseek_direct'] = 'deepseek_direct'
    deepseek_budget_enabled: bool = True
    deepseek_daily_micro_usd: int = 250_000
    deepseek_request_micro_usd: int = 5_000
    eleven_api_key: str = ''
    livekit_url: str = ''
    livekit_api_key: str = ''
    livekit_api_secret: str = ''
    agent_service_secret: str = ''
    demo_session_budget: int = 80
    demo_calls_per_identity: int = 3

    @model_validator(mode='after')
    def validate_deepseek_budget(self):
        if self.deepseek_budget_enabled and not (0 < self.deepseek_request_micro_usd <= self.deepseek_daily_micro_usd):
            raise ValueError('DEEPSEEK_BUDGET_CONFIGURATION_INVALID')
        return self
