"""The hosted backfill after migration 0020 indexes existing memories once and only once."""

import asyncio
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

from app.infrastructure.database import Base, Memory, MemoryTerm
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import create_async_engine

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_memory_terms.py"


def load_script():
    spec = importlib.util.spec_from_file_location("backfill_memory_terms", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_database(path, rows):
    url = f"sqlite+aiosqlite:///{path}"

    async def create():
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO identities (id, token_hash, expires_at) VALUES ('o1', 'h', '2030-01-01T00:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO conversations (id, owner_id, title, revision, created_at) VALUES ('c1', 'o1', 't', 0, 'x')"
        )
        for memory_id, status, content in rows:
            db.execute(
                "INSERT INTO memories (id, owner_id, content, embedding_model, source_conversation_id, version, status,"
                " created_at, updated_at) VALUES (?, 'o1', ?, 'e', 'c1', 1, ?, 'x', 'x')",
                (memory_id, content, status),
            )
    return url


def test_backfill_indexes_missing_rows_is_idempotent_and_honours_dry_run(tmp_path):
    path = tmp_path / "backfill.db"
    url = create_database(
        path,
        [
            ("m1", "active", "I love café in Paris"),
            ("m2", "superseded", "I lived in Benghazi"),
            ("m3", "deleted", ""),
        ],
    )
    script = load_script()
    assert asyncio.run(script.backfill(url, dry_run=True)) > 0
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM memory_terms").fetchone()[0] == 0
    inserted = asyncio.run(script.backfill(url, dry_run=False))
    assert inserted > 0
    with closing(sqlite3.connect(path)) as db:
        terms = {row for row in db.execute("SELECT memory_id, term FROM memory_terms")}
    assert ("m1", "cafe") in terms and ("m1", "paris") in terms and ("m2", "benghazi") in terms
    assert not any(memory_id == "m3" for memory_id, _ in terms)
    assert asyncio.run(script.backfill(url, dry_run=False)) == 0


def test_a_memory_deleted_between_read_and_insert_gets_no_terms(tmp_path, monkeypatch):
    path = tmp_path / "deleted-race.db"
    url = create_database(
        path,
        [("m1", "active", "I love café in Paris"), ("m2", "active", "I lived in Benghazi")],
    )
    script = load_script()

    async def delete_m1(connection, memory_ids):
        # Simulates the erase path deleting and scrubbing m1 after the backfill read it.
        await connection.execute(update(Memory).where(Memory.id == "m1").values(status="deleted", content=""))

    monkeypatch.setattr(script, "_before_batch_insert", delete_m1)
    assert asyncio.run(script.backfill(url, dry_run=False)) > 0
    with closing(sqlite3.connect(path)) as db:
        terms = set(db.execute("SELECT memory_id, term FROM memory_terms"))
    assert not any(memory_id == "m1" for memory_id, _ in terms)
    assert ("m2", "benghazi") in terms


def test_a_memory_saved_by_the_app_between_read_and_insert_is_not_duplicated(tmp_path, monkeypatch):
    path = tmp_path / "saved-race.db"
    url = create_database(
        path,
        [("m1", "active", "I love café in Paris"), ("m2", "active", "I lived in Benghazi")],
    )
    script = load_script()

    async def save_m1(connection, memory_ids):
        # The app commits a new version of m1 and its full terms after the backfill read the old version.
        await connection.execute(update(Memory).where(Memory.id == "m1").values(content="I love café in Berlin"))
        await connection.execute(
            insert(MemoryTerm),
            [
                {"memory_id": "m1", "owner_id": "o1", "term": "cafe"},
                {"memory_id": "m1", "owner_id": "o1", "term": "love"},
                {"memory_id": "m1", "owner_id": "o1", "term": "berlin"},
            ],
        )

    monkeypatch.setattr(script, "_before_batch_insert", save_m1)
    assert asyncio.run(script.backfill(url, dry_run=False)) > 0
    with closing(sqlite3.connect(path)) as db:
        terms = db.execute(
            "SELECT term, count(*) FROM memory_terms WHERE memory_id = 'm1' GROUP BY term ORDER BY term"
        ).fetchall()
    assert terms == [("berlin", 1), ("cafe", 1), ("love", 1)]
