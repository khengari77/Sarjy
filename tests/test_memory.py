import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.infrastructure.database import Base, Identity, Conversation, Turn, Memory, DevelopmentBudget
from app.services.memory import MemoryService, OpenAIEmbedder, validate_embedding


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    async def embed(self, db, text):
        self.calls.append(text)
        return tuple(
            [1.0 if "budget" not in text.lower() else 0.0, 1.0 if "budget" in text.lower() else 0.0] + [0.0] * 1534
        )


async def setup(path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(path))
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all(
            [
                Identity(id="alice", token_hash="a", expires_at="2030"),
                Identity(id="bob", token_hash="b", expires_at="2030"),
            ]
        )
        await db.commit()
        db.add_all(
            [
                Conversation(id="a1", owner_id="alice"),
                Conversation(id="a2", owner_id="alice"),
                Conversation(id="b1", owner_id="bob"),
            ]
        )
        await db.commit()
        db.add(
            Turn(
                id="source",
                conversation_id="a1",
                request_id="source",
                role="user",
                text="Remember city",
                status="completed",
            )
        )
        await db.commit()
    return engine, sessions


def test_memory_persists_across_connections_and_corrections_are_atomic(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "memory.db")
        embedder = FakeEmbedder()
        try:
            async with sessions() as db:
                service = MemoryService(db, embedder)
                city = await service.create_memory("alice", "a1", "source", "Tripoli", "city")
                assert await service.create_memory("alice", "a1", "source", "Tripoli", "city") == city
                assert len(embedder.calls) == 1
                budget = await service.create_memory("alice", "a1", "source", "Budget 500", "budget")
            async with sessions() as db:
                service = MemoryService(db, embedder)
                found = await service.search_memories("alice", "a2", "city")
                assert found[0].id == city.id and found[0].source_turn_id == "source"
                corrected = await service.correct_memory("alice", city.id, 1, "London", "correction")
                calls = len(embedder.calls)
                assert await service.correct_memory("alice", city.id, 1, "London", "correction") == corrected
                assert len(embedder.calls) == calls
                assert corrected.version == 2 and corrected.supersedes_id == city.id
                results = await service.search_memories("alice", "a2", "city")
                assert city.id not in {r.id for r in results}
                assert {r.id for r in results} == {corrected.id, budget.id}
                assert (await db.get(Memory, city.id)).status == "superseded"
                with pytest.raises(HTTPException) as error:
                    await service.correct_memory("alice", city.id, 1, "Paris", "stale")
                assert error.value.status_code == 409
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unauthorized_access_and_key_conflicts_do_not_embed(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "memory.db")
        embedder = FakeEmbedder()
        try:
            async with sessions() as db:
                service = MemoryService(db, embedder)
                city = await service.create_memory("alice", "a1", "source", "Tripoli", "key")
                before = len(embedder.calls)
                for operation in [
                    service.create_memory("bob", "a1", None, "secret", "x"),
                    service.search_memories("bob", "a1", "secret"),
                    service.correct_memory("bob", city.id, 1, "secret", "x"),
                    service.create_memory("alice", "a1", "source", "Different", "key"),
                ]:
                    with pytest.raises(HTTPException):
                        await operation
                assert len(embedder.calls) == before
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("vector", [[0.0] * 1536, [1.0] * 3, [float("nan")] * 1536, [float("inf")] * 1536])
def test_embeddings_must_be_finite_nonzero_and_have_exact_dimension(vector):
    with pytest.raises(ValueError):
        validate_embedding(vector)


def test_real_adapter_reserves_before_transport_and_denial_sends_nothing(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "memory.db")
        calls = []

        def transport(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": [{"embedding": [1.0] + [0.0] * 1535}]})

        try:
            async with sessions() as db:
                adapter = OpenAIEmbedder("test", transport=httpx.MockTransport(transport))
                await adapter.embed(db, "city")
                row = await db.scalar(select(DevelopmentBudget))
                assert row.reserved_units == 20000
                row.reserved_units = 1000000
                await db.commit()
                with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
                    await adapter.embed(db, "city")
                assert len(calls) == 1 and calls[0]["dimensions"] == 1536
        finally:
            await engine.dispose()

    asyncio.run(run())
