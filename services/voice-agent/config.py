"""Load job configuration lazily without changing the importing process environment."""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

WORKER_KEYS = (
    "API_INTERNAL_URL",
    "AGENT_SERVICE_SECRET",
    "DEEPGRAM_API_KEY",
    "ELEVEN_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
)


def read_worker_environment(env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    path = env_file if env_file is not None else Path(__file__).resolve().parents[2] / ".env"
    local = dotenv_values(path) if path.exists() else {}
    environment = os.environ if environ is None else environ
    return {key: environment.get(key, local.get(key) or "") for key in WORKER_KEYS}


def require_values(values: Mapping[str, str], *names: str) -> dict[str, str]:
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise ValueError("Missing worker configuration: " + ", ".join(missing))
    return {name: values[name] for name in names}


def provider_credentials(values: Mapping[str, str], primary_provider: str) -> dict[str, str]:
    """Return required primary credentials plus an optional OpenAI fallback key.

    The fallback key is optional: a missing OpenAI key must not block a
    DeepSeek-primary session, but when it is configured it must reach the
    provider factory so the fallback can be constructed.
    """
    provider_key = "OPENAI_API_KEY" if primary_provider == "openai_direct" else "DEEPSEEK_API_KEY"
    credentials = require_values(values, "DEEPGRAM_API_KEY", "ELEVEN_API_KEY", provider_key)
    if primary_provider == "deepseek_direct" and values.get("OPENAI_API_KEY"):
        credentials["OPENAI_API_KEY"] = values["OPENAI_API_KEY"]
    return credentials
