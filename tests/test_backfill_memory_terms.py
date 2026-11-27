"""The hosted backfill after migration 0019 indexes existing memories once and only once."""

import asyncio
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

from app.infrastructure.database import Base
from sqlalchemy.ext.asyncio import create_async_engine

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_memory_terms.py"


def load_script():
    spec = importlib.util.spec_from_file_location("backfill_memory_terms", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_indexes_missing_rows_is_idempotent_and_honours_dry_run(tmp_path):
    path = tmp_path / "backfill.db"
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
        for memory_id, status, content in (
            ("m1", "active", "I love café in Paris"),
            ("m2", "superseded", "I lived in Benghazi"),
            ("m3", "deleted", ""),
        ):
            db.execute(
                "INSERT INTO memories (id, owner_id, content, embedding_model, source_conversation_id, version, status,"
                " created_at, updated_at) VALUES (?, 'o1', ?, 'e', 'c1', 1, ?, 'x', 'x')",
                (memory_id, content, status),
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
