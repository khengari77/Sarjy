import asyncio
from datetime import UTC, datetime

import pytest
from app.domain.budget import OPENAI_DAILY_MICRO_USD, budget_day
from app.infrastructure.database import (
    Base,
    Conversation,
    DevelopmentBudget,
    Identity,
    Memory,
)
from app.services.authorization import owned_conversation, owned_memory
from app.services.budget import guarded_openai_request
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_owner_scoped_reads_and_budget_denial_precede_external_effects(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/boundaries.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add_all(
                    [
                        Identity(id="alice", token_hash="a", expires_at="2030-01-01"),
                        Identity(id="bob", token_hash="b", expires_at="2030-01-01"),
                    ]
                )
                await db.commit()
                db.add(Conversation(id="chat", owner_id="alice", title="test", revision=0))
                await db.commit()
                db.add(
                    Memory(
                        id="memory",
                        owner_id="alice",
                        content="Tripoli",
                        source_conversation_id="chat",
                        version=1,
                        status="active",
                    )
                )
                await db.commit()
                assert (await owned_conversation(db, "alice", "chat")).id == "chat"
                assert (await owned_memory(db, "alice", "memory")).id == "memory"
                for lookup, identifier in [(owned_conversation, "chat"), (owned_memory, "memory")]:
                    for owner in ["bob", "unknown"]:
                        with pytest.raises(HTTPException) as error:
                            await lookup(db, owner, identifier)
                        assert error.value.status_code == 404
                    with pytest.raises(HTTPException):
                        await lookup(db, "alice", "missing")
                db.add(
                    DevelopmentBudget(
                        day=budget_day(datetime.now(UTC)), provider="openai", reserved_units=OPENAI_DAILY_MICRO_USD
                    )
                )
                await db.commit()
                effects = []

                async def external():
                    effects.append("called")

                with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
                    await guarded_openai_request(db, external)
                assert effects == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_guard_reserves_before_delivery_and_retains_uncertain_spending(tmp_path):
    from app.domain.budget import OPENAI_REQUEST_MICRO_USD
    from sqlalchemy import select

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/delivery.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:

                async def external():
                    total = await db.scalar(select(DevelopmentBudget.reserved_units))
                    assert total == OPENAI_REQUEST_MICRO_USD
                    raise RuntimeError("delivery uncertain")

                with pytest.raises(RuntimeError, match="delivery uncertain"):
                    await guarded_openai_request(db, external)
            async with factory() as db:
                assert await db.scalar(select(DevelopmentBudget.reserved_units)) == OPENAI_REQUEST_MICRO_USD
        finally:
            await engine.dispose()

    asyncio.run(run())
