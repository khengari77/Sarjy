"""Required integration checks must fail before tests can silently miss coverage."""

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config

_spec = importlib.util.spec_from_file_location("regression_env", Path("scripts/check_regression_env.py"))
assert _spec is not None and _spec.loader is not None
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


def test_preflight_requires_both_databases_without_exposing_values():
    with pytest.raises(ValueError, match="TEST_MEMORY_POSTGRES_URL") as error:
        preflight.validate_environment({"TEST_POSTGRES_URL": "postgresql+asyncpg://secret@localhost/test"})
    assert "secret" not in str(error.value)


def test_preflight_refuses_sqlite_for_concurrency():
    with pytest.raises(ValueError, match="Expected PostgreSQL"):
        preflight.validate_environment({"TEST_POSTGRES_URL": "sqlite:///test", "TEST_MEMORY_POSTGRES_URL": "test"})
    preflight.validate_environment(
        dict.fromkeys(("TEST_POSTGRES_URL", "TEST_MEMORY_POSTGRES_URL"), "postgresql+asyncpg://localhost/test")
    )


def test_migration_preflight_rejects_multiple_heads(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    for revision in ("a", "b"):
        (versions / f"{revision}.py").write_text(f"revision = {revision!r}\ndown_revision = None\n")
    config = Config()
    config.set_main_option("script_location", str(tmp_path))
    with pytest.raises(ValueError, match="exactly one"):
        preflight.validate_head(config)
    (versions / "b.py").unlink()
    preflight.validate_head(config)
