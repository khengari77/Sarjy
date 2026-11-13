"""Wordle migration upgrade/downgrade parity with the ORM models."""

import os
import sqlite3
import subprocess
from contextlib import closing

WORDLE_TABLES = {"wordle_puzzles", "wordle_games", "wordle_guesses"}
WORDLE_INDEXES = {
    "ix_wordle_games_owner_status",
    "ix_wordle_games_puzzle_date",
    "ix_wordle_guesses_game_id",
}
PREVIOUS_HEAD = "0010_context_retrieval_indexes"


def alembic(url, *commands):
    env = os.environ | {"DATABASE_URL": url}
    result = subprocess.run([".venv/bin/alembic", *commands], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def seed(connection):
    connection.execute("INSERT INTO identities VALUES ('owner-1','hash','2030-01-01T00:00:00+00:00')")
    connection.execute(
        "INSERT INTO conversations VALUES ('conversation-1','owner-1','Tripoli trip',3,'2026-11-11T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO turns VALUES ('turn-1','conversation-1','request-1','user','Plan a trip','completed',"
        "'2026-11-11T00:00:00+00:00',NULL)"
    )
    connection.execute("INSERT INTO development_budgets VALUES ('2026-11-11','openai',2)")
    connection.commit()


def snapshot(connection):
    return {
        "conversation": connection.execute("SELECT id,title,revision FROM conversations").fetchall(),
        "turn": connection.execute("SELECT id,text,status FROM turns").fetchall(),
        "budget": connection.execute("SELECT day,provider,reserved_units FROM development_budgets").fetchall(),
    }


def tables(path):
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def indexes(path):
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def columns(path, table):
    with closing(sqlite3.connect(path)) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_wordle_migration_upgrades_downgrades_and_matches_models(tmp_path):
    path = tmp_path / "wordle.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(url, "upgrade", "head")
    assert WORDLE_TABLES.issubset(tables(path))
    assert WORDLE_INDEXES.issubset(indexes(path))
    assert columns(path, "wordle_puzzles") == {
        "puzzle_date",
        "answer",
        "source",
        "fetched_at",
        "payload_hash",
        "claim_id",
        "claim_expires_at",
        "last_attempt_at",
    }
    assert columns(path, "wordle_games") == {
        "id",
        "owner_id",
        "puzzle_date",
        "status",
        "created_at",
        "updated_at",
    }
    assert columns(path, "wordle_guesses") == {
        "id",
        "game_id",
        "attempt",
        "word",
        "marks",
        "request_id",
        "created_at",
    }
    alembic(url, "check")

    alembic(url, "downgrade", PREVIOUS_HEAD)
    assert not WORDLE_TABLES & tables(path)
    assert not WORDLE_INDEXES & indexes(path)
    assert {"identities", "conversations", "turns", "memories"} <= tables(path)
    alembic(url, "upgrade", "head")
    alembic(url, "check")


def test_wordle_migration_preserves_existing_data(tmp_path):
    path = tmp_path / "wordle-data.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(url, "upgrade", PREVIOUS_HEAD)
    with closing(sqlite3.connect(path)) as connection, connection:
        seed(connection)
        before = snapshot(connection)
    alembic(url, "upgrade", "head")
    with closing(sqlite3.connect(path)) as connection:
        assert snapshot(connection) == before
    alembic(url, "downgrade", PREVIOUS_HEAD)
    with closing(sqlite3.connect(path)) as connection:
        assert snapshot(connection) == before
        assert not WORDLE_TABLES & tables(path)


def test_wordle_uniqueness_constraints(tmp_path):
    path = tmp_path / "wordle-constraints.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(url, "upgrade", "head")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO wordle_games (id, owner_id, puzzle_date, status, created_at, updated_at)"
            " VALUES ('game-1','owner-1','2026-11-13','in_progress','now','now')"
        )
        connection.execute(
            "INSERT INTO wordle_guesses (id, game_id, attempt, word, marks, request_id, created_at)"
            " VALUES ('guess-1','game-1',1,'SLATE','[\"gray\"]','request-1','now')"
        )
        for statement in (
            "INSERT INTO wordle_games (id, owner_id, puzzle_date, status, created_at, updated_at)"
            " VALUES ('game-2','owner-1','2026-11-13','in_progress','now','now')",
            "INSERT INTO wordle_guesses (id, game_id, attempt, word, marks, request_id, created_at)"
            " VALUES ('guess-2','game-1',1,'CRANE','[]','request-2','now')",
            "INSERT INTO wordle_guesses (id, game_id, attempt, word, marks, request_id, created_at)"
            " VALUES ('guess-3','game-1',2,'CRANE','[]','request-1','now')",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f"constraint missing for: {statement}")
