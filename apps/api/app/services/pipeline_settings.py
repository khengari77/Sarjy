"""Read provider configuration without leaking Settings into the domain."""

from app.config import Settings
from app.domain.pipeline import KEYS


def configured_keys(config: Settings) -> set[str]:
    return {key for key in {*KEYS.values(), "DEEPSEEK_API_KEY"} if getattr(config, key.lower())}


def default_llm(config: Settings) -> tuple[str, str]:
    if config.default_llm_provider == "openai_direct":
        return ("openai_direct", "gpt-6-luna")
    return ("deepseek_direct", "deepseek-flash")
