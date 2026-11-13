import asyncio
import os
import sqlite3
import subprocess
from contextlib import closing

import pytest

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")


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
        "INSERT INTO turns VALUES ('turn-1','conversation-1','request-1','user','Plan a trip','completed','2026-11-11T00:00:00+00:00',NULL)"
    )
    connection.execute("INSERT INTO development_budgets VALUES ('2026-11-11','openai',2)")
    connection.commit()


def snapshot(connection):
    return {
        "conversation": connection.execute("SELECT id,title,revision FROM conversations").fetchall(),
        "turn": connection.execute("SELECT id,text,status FROM turns").fetchall(),
        "budget": connection.execute("SELECT day,provider,reserved_units FROM development_budgets").fetchall(),
    }


def test_initial_migration_matches_models(tmp_path):
    alembic(f"sqlite+aiosqlite:///{tmp_path}/migration.db", "upgrade", "head")
    alembic(f"sqlite+aiosqlite:///{tmp_path}/migration.db", "check")


def test_day2_migration_is_additive_on_existing_sqlite_database(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/migration.db"
    alembic(url, "upgrade", "0003")
    with closing(sqlite3.connect(tmp_path / "migration.db")) as connection, connection:
        seed(connection)
        before = snapshot(connection)
    alembic(url, "upgrade", "head")
    with closing(sqlite3.connect(tmp_path / "migration.db")) as connection, connection:
        assert snapshot(connection) == before
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "memories",
        "context_revisions",
        "context_operations",
        "context_exclusions",
        "context_snapshots",
        "turn_metrics",
        "user_settings",
        "setting_revisions",
    } <= tables
    alembic(url, "check")
    alembic(url, "downgrade", "0003")
    with closing(sqlite3.connect(tmp_path / "migration.db")) as connection, connection:
        assert "memories" not in {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert snapshot(connection) == before


def test_day2_migration_uses_pgvector_1536_and_preserves_seed():
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL not set; disposable PostgreSQL required")
    asyncio.run(_postgres_migration())


async def _postgres_migration():
    import asyncpg

    plain = POSTGRES_URL.replace("postgresql+asyncpg://", "postgresql://")
    alembic(POSTGRES_URL, "downgrade", "base")
    alembic(POSTGRES_URL, "upgrade", "0003")
    connection = await asyncpg.connect(plain)
    try:
        for role in ("anon", "authenticated"):
            if not await connection.fetchval("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=$1)", role):
                await connection.execute("CREATE ROLE " + role)
        await connection.execute("INSERT INTO identities VALUES ('owner-1','hash','2030-01-01T00:00:00+00:00')")
        await connection.execute(
            "INSERT INTO conversations VALUES ('conversation-1','owner-1','Tripoli trip',3,'2026-11-11T00:00:00+00:00')"
        )
        await connection.execute(
            "INSERT INTO turns VALUES ('turn-1','conversation-1','request-1','user','Plan a trip','completed','2026-11-11T00:00:00+00:00',NULL)"
        )
        await connection.execute("INSERT INTO development_budgets VALUES ('2026-11-11','openai',2)")
        before = (
            await connection.fetch("SELECT id,title,revision FROM conversations"),
            await connection.fetch("SELECT id,text,status FROM turns"),
            await connection.fetch("SELECT day,provider,reserved_units FROM development_budgets"),
        )
    finally:
        await connection.close()
    alembic(POSTGRES_URL, "upgrade", "head")
    alembic(POSTGRES_URL, "check")
    connection = await asyncpg.connect(plain)
    try:
        embedding = await connection.fetchval(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='memories'::regclass AND attname='embedding'"
        )
        extension = await connection.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        after = (
            await connection.fetch("SELECT id,title,revision FROM conversations"),
            await connection.fetch("SELECT id,text,status FROM turns"),
            await connection.fetch("SELECT day,provider,reserved_units FROM development_budgets"),
        )
        for table in (
            "memories",
            "context_revisions",
            "context_operations",
            "context_exclusions",
            "context_snapshots",
            "turn_metrics",
            "user_settings",
            "setting_revisions",
            "memory_write_requests",
        ):
            rls = await connection.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname=$1", table)
            assert rls is True, table
            for role in ("anon", "authenticated"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    assert not await connection.fetchval(
                        "SELECT has_table_privilege($1, $2, $3)", role, table, privilege
                    ), (role, table, privilege)
    finally:
        await connection.close()
    assert embedding == "vector(1536)"
    assert extension
    assert after == before


RETRIEVAL_INDEXES = (
    "ix_turns_conversation_status_created",
    "ix_memories_owner_status_updated",
    "ix_memories_supersedes_id",
    "ix_tool_calls_turn_state",
)


def test_retrieval_indexes_upgrade_and_downgrade_on_sqlite(tmp_path):
    path = tmp_path / "retrieval.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(url, "upgrade", "head")
    with closing(sqlite3.connect(path)) as connection:
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert set(RETRIEVAL_INDEXES) <= indexes
    alembic(url, "downgrade", "0009_dispatcher_leases")
    with closing(sqlite3.connect(path)) as connection:
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not set(RETRIEVAL_INDEXES) & indexes
    assert {"turns", "memories", "tool_calls"} <= tables, "downgrade must not drop data tables"
    alembic(url, "upgrade", "head")
    alembic(url, "check")


def test_retrieval_indexes_upgrade_and_downgrade_on_postgres():
    if not POSTGRES_URL:
        pytest.skip("TEST_POSTGRES_URL not set; disposable PostgreSQL required")

    async def index_names():
        import asyncpg

        plain = POSTGRES_URL.replace("postgresql+asyncpg://", "postgresql://")
        connection = await asyncpg.connect(plain)
        try:
            rows = await connection.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename IN ('turns','memories','tool_calls')"
            )
        finally:
            await connection.close()
        return {row["indexname"] for row in rows}

    alembic(POSTGRES_URL, "upgrade", "head")
    assert set(RETRIEVAL_INDEXES) <= asyncio.run(index_names())
    alembic(POSTGRES_URL, "downgrade", "0009_dispatcher_leases")
    assert not set(RETRIEVAL_INDEXES) & asyncio.run(index_names())
    alembic(POSTGRES_URL, "upgrade", "head")
    assert set(RETRIEVAL_INDEXES) <= asyncio.run(index_names())


def test_cleanup_migration_preserves_existing_sessions_and_ledger(tmp_path):
    import sqlite3

    path = tmp_path / "cleanup.db"
    env = os.environ | {"DATABASE_URL": f"sqlite+aiosqlite:///{path}"}

    def migrate(*args):
        result = subprocess.run([".venv/bin/alembic", *args], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0003")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO identities VALUES ('owner','hash','2030-01-01')")
        db.execute("INSERT INTO conversations VALUES ('chat','owner','Test',0,'2026-11-11')")
        db.execute("INSERT INTO voice_sessions VALUES ('session','chat','owner','room','ended','2026-11-11','{}')")
        db.execute("INSERT INTO development_budgets VALUES ('2026-11-11','openai',20000)")
        db.commit()
    migrate("upgrade", "head")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT room_cleanup_completed_at FROM voice_sessions").fetchone() == (None,)
        assert db.execute("SELECT reserved_units FROM development_budgets").fetchone() == (20000,)
    migrate("check")
    migrate("downgrade", "0003")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT id,status FROM voice_sessions").fetchone() == ("session", "ended")


def test_day2_upgrade_from_cleanup_head_keeps_confirmation(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/ancestry.db"
    alembic(url, "upgrade", "0004_voice_cleanup")
    with closing(sqlite3.connect(tmp_path / "ancestry.db")) as db, db:
        seed(db)
        db.execute(
            "INSERT INTO voice_sessions VALUES ('voice','conversation-1','owner-1','room','ended','2026-11-11','{}','2026-11-11T10:00:00+00:00')"
        )
        db.commit()
        before = snapshot(db)
    alembic(url, "upgrade", "head")
    alembic(url, "check")
    with closing(sqlite3.connect(tmp_path / "ancestry.db")) as db, db:
        assert snapshot(db) == before
        assert (
            db.execute("SELECT room_cleanup_completed_at FROM voice_sessions").fetchone()[0]
            == "2026-11-11T10:00:00+00:00"
        )
