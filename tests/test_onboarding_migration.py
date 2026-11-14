"""Additive onboarding migration, independent of in-flight Wordle/memory work."""

import os
import sqlite3
import subprocess
from contextlib import closing


def alembic(url, *commands):
    result = subprocess.run(
        [".venv/bin/alembic", *commands],
        env=os.environ | {"DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_onboarding_table_is_additive_and_reversible(tmp_path):
    path = tmp_path / "onboarding.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(url, "upgrade", "0011_wordle")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO identities VALUES ('owner','hash','2030-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO conversations VALUES ('chat','owner','Test',0,'2026-11-11')")
        db.commit()
    alembic(url, "upgrade", "head")
    with closing(sqlite3.connect(path)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(onboarding_progress)")}
        assert {
            "owner_id",
            "schema_version",
            "stage",
            "revision",
            "preferred_name",
            "selected_voice_id",
            "selected_pace",
            "intro_played_at",
            "started_at",
            "completed_at",
            "suspended_from",
            "last_event_id",
            "applied_events",
            "updated_at",
        } == columns
        assert db.execute("SELECT id,title FROM conversations").fetchone() == ("chat", "Test")
    alembic(url, "check")
    alembic(url, "downgrade", "0011_wordle")
    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "onboarding_progress" not in tables
        assert db.execute("SELECT id,title FROM conversations").fetchone() == ("chat", "Test")
    alembic(url, "upgrade", "head")
    alembic(url, "check")
