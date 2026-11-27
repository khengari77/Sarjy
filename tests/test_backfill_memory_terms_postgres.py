"""Disposable PostgreSQL acceptance for the memory_terms backfill after migration 0020."""

import asyncio
import importlib.util
import os
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest
from alembic_runner import run_alembic
from app.infrastructure.database import Conversation, Identity, Memory, MemoryTerm
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_terms import terms_of
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_memory_terms.py"


def load_script():
    spec = importlib.util.spec_from_file_location("backfill_memory_terms", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def postgres_url():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; disposable PostgreSQL required")
    run_alembic(url, "upgrade", "head")
    return url


async def seed(url, memories):
    """Insert one owner, conversation and memory per (owner, content) pair."""
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    seeded = []
    try:
        async with sessions() as db:
            for owner, content in memories:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.flush()
                chat = str(uuid4())
                db.add(Conversation(id=chat, owner_id=owner))
                await db.flush()
                memory_id = str(uuid4())
                db.add(
                    Memory(
                        id=memory_id,
                        owner_id=owner,
                        content=content,
                        embedding_model="text-embedding-3-small",
                        source_conversation_id=chat,
                        version=1,
                        status="active",
                    )
                )
                seeded.append((owner, memory_id, content))
            await db.commit()
    finally:
        await engine.dispose()
    return seeded


async def terms_for(url, memory_ids):
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            rows = (
                await db.execute(
                    select(MemoryTerm.memory_id, MemoryTerm.term).where(MemoryTerm.memory_id.in_(memory_ids))
                )
            ).all()
    finally:
        await engine.dispose()
    return [(memory_id, term) for memory_id, term in rows]


def test_postgres_backfill_indexes_once_and_dry_run_writes_nothing():
    url = postgres_url()
    script = load_script()
    memories = [(str(uuid4()), "I prefer green tea"), (str(uuid4()), "I like black coffee")]

    async def run():
        seeded = await seed(url, memories)
        memory_ids = [memory_id for _, memory_id, _ in seeded]
        expected = {(memory_id, term) for _, memory_id, content in seeded for term in terms_of(content)}

        assert await terms_for(url, memory_ids) == []
        assert await script.backfill(url, dry_run=True) > 0
        assert await terms_for(url, memory_ids) == [], "a dry run must not write term rows"

        assert await script.backfill(url, dry_run=False) > 0
        rows = await terms_for(url, memory_ids)
        assert set(rows) == expected
        assert all(count == 1 for count in Counter(rows).values())

        # Idempotent: nothing is inserted again and no duplicate (memory_id, term) rows appear.
        assert await script.backfill(url, dry_run=False) == 0
        assert await terms_for(url, memory_ids) == rows

    asyncio.run(run())


def test_postgres_backfill_holds_the_owner_lock_across_a_batch(monkeypatch):
    url = postgres_url()
    script = load_script()
    owner = str(uuid4())

    async def run():
        seeded = await seed(url, [(owner, "I prefer green tea")])
        memory_id = seeded[0][1]
        blocked = []

        async def probe(connection, memory_ids):
            # The script has taken every batch owner's lock already; a second connection must wait for the commit.
            batch_owner = await connection.scalar(select(Memory.owner_id).where(Memory.id == memory_ids[0]))
            probe_engine = create_async_engine(url)
            try:
                async with AsyncSession(probe_engine, expire_on_commit=False) as session:
                    try:
                        await asyncio.wait_for(lock_owner_memories(session, batch_owner), timeout=0.25)
                    except TimeoutError:
                        blocked.append(batch_owner)
                    finally:
                        await session.rollback()
            finally:
                await probe_engine.dispose()

        monkeypatch.setattr(script, "_before_batch_insert", probe)
        inserted = await script.backfill(url, dry_run=False)
        assert inserted >= len(terms_of("I prefer green tea"))
        assert blocked, "the backfill must hold the owner lock while its batch transaction is open"
        assert await terms_for(url, [memory_id]) != []

        # The batch committed, so the same lock is free again rather than leaked as a session lock.
        release_engine = create_async_engine(url)
        try:
            async with AsyncSession(release_engine, expire_on_commit=False) as session:
                await asyncio.wait_for(lock_owner_memories(session, blocked[0]), timeout=2)
                await session.rollback()
        finally:
            await release_engine.dispose()

    asyncio.run(run())
