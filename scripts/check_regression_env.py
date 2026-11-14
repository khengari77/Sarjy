"""Offline preflight for required PostgreSQL regression jobs; never print URLs."""

import os

from alembic.config import Config
from alembic.script import ScriptDirectory


def validate_environment(environment: dict[str, str]) -> None:
    required = ("TEST_POSTGRES_URL", "TEST_MEMORY_POSTGRES_URL")
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise ValueError("Missing required regression settings: " + ", ".join(missing))
    for name in required:
        if not environment[name].startswith("postgresql+asyncpg://"):
            raise ValueError("Expected PostgreSQL regression setting: " + name)


def validate_head(config: Config) -> None:
    if len(ScriptDirectory.from_config(config).get_heads()) != 1:
        raise ValueError("Expected exactly one migration head")


def main() -> None:
    validate_environment(dict(os.environ))
    validate_head(Config("alembic.ini"))
    print("Regression environment and migration head verified")


if __name__ == "__main__":
    main()
