"""Real pgvector and concurrent-version acceptance; disposable database only."""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.infrastructure.database import Identity, Conversation, Memory
from app.services.memory import MemoryService
from test_memory import FakeEmbedder


def test_postgres_cosine_retrieval_and_concurrent_correction_have_one_active_version():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat, second = (str(uuid4()) for _ in range(3))
        embedder = FakeEmbedder()
        try:
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add_all([Conversation(id=chat, owner_id=owner), Conversation(id=second, owner_id=owner)])
                await db.commit()
                service = MemoryService(db, embedder)
                city = await service.create_memory(owner, chat, None, "Tripoli", "city")
                budget = await service.create_memory(owner, chat, None, "Budget 500", "budget")
            async with sessions() as db:
                found = await MemoryService(db, embedder).search_memories(owner, second, "budget", 2)
                assert [record.id for record in found] == [budget.id, city.id]
                assert found[0].distance == pytest.approx(0)
                assert found[1].distance == pytest.approx(1)

            async def correct(content, key):
                async with sessions() as db:
                    try:
                        return await MemoryService(db, embedder).correct_memory(owner, city.id, 1, content, key)
                    except HTTPException as error:
                        assert error.status_code == 409
                        return None

            results = await asyncio.gather(correct("London", "one"), correct("Paris", "two"))
            assert sum(result is not None for result in results) == 1
            async with sessions() as db:
                active = list(
                    await db.scalars(select(Memory).where(Memory.owner_id == owner, Memory.status == "active"))
                )
                assert len(active) == 2 and city.id not in {record.id for record in active}
                assert sorted(record.version for record in active) == [1, 2]
        finally:
            await engine.dispose()

    asyncio.run(run())
