"""Keep local and CI test discovery identical.

The repository may contain ignored worktree copies under ``artifacts/``.  Without
an explicit ``testpaths`` setting pytest recursively collects those copies and
fails before it reaches the real suite, so the documented command and CI drift.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pytest_config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["tool"]["pytest"]["ini_options"]


def test_pytest_only_collects_the_curated_suite():
    config = _pytest_config()
    assert config["testpaths"] == ["tests"]
    assert {"artifacts", ".hypothesis", "node_modules", "build"} <= set(config["norecursedirs"])


def test_documented_command_matches_the_configured_suite():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv run pytest -q" in readme


def test_warnings_fail_the_suite():
    assert _pytest_config()["filterwarnings"] == ["error"]
