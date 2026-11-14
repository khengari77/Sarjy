"""Required integration checks must fail before tests can silently miss coverage."""

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
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


def _database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def test_committed_workflow_preserves_service_and_required_gates():
    workflow = yaml.safe_load(Path(".github/workflows/checks.yml").read_text())
    postgres = workflow["jobs"]["postgres"]

    service = postgres["services"]["postgres"]
    assert service["image"] == "pgvector/pgvector:pg16"
    assert "5432:5432" in service["ports"]
    assert "pg_isready" in service["options"]
    preflight.validate_environment(postgres["env"])

    steps = postgres["steps"]
    preflight_steps = [step for step in steps if "scripts/check_regression_env.py" in step.get("run", "")]
    assert len(preflight_steps) == 1, "postgres job must run the regression environment check exactly once"

    migration_steps = [step for step in steps if step.get("name") == "Migration preservation"]
    assert len(migration_steps) == 1, "postgres job must keep the migration preservation step"
    migration = migration_steps[0]
    assert _database_name(migration["env"]["TEST_POSTGRES_URL"]) == "migration_tests", (
        "migration step must override TEST_POSTGRES_URL to the isolated migration database"
    )
    assert steps.index(preflight_steps[0]) < steps.index(migration), (
        "regression environment check must run before the migration step it gates"
    )

    commands = [step.get("run", "") for step in workflow["jobs"]["checks"]["steps"]]
    assert "pnpm --dir apps/web test:deadline" in commands
    assert "pnpm --dir apps/web test:smoke-setup" in commands
