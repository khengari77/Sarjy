import asyncio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.infrastructure.database import Base, Identity, DevelopmentBudget
from app.services.budget import guarded_openai_request, reserve_in_transaction


def test_guard_never_commits_or_rolls_back_business_changes(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/guard.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with factory() as business:
                pending = Identity(id="pending", token_hash="hash", expires_at="2030")
                business.add(pending)

                async def uncertain():
                    async with factory() as observer:
                        assert await observer.get(Identity, "pending") is None
                        assert await observer.scalar(select(DevelopmentBudget.reserved_units)) == 20000
                    raise RuntimeError("uncertain delivery")

                with pytest.raises(RuntimeError, match="uncertain delivery"):
                    await guarded_openai_request(business, uncertain)
                assert pending in business.new
                await business.rollback()
            async with factory() as observer:
                assert await observer.get(Identity, "pending") is None
                assert await observer.scalar(select(DevelopmentBudget.reserved_units)) == 20000
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_transaction_neutral_reservation_obeys_owner_rollback(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rollback.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with factory() as db:
                await reserve_in_transaction(db, "openai")
                assert db.in_transaction()
                await db.rollback()
            async with factory() as db:
                assert await db.scalar(select(DevelopmentBudget.reserved_units)) is None
            effects = []

            async def delivered():
                effects.append(True)

            await guarded_openai_request(factory, delivered)
            assert effects == [True]
        finally:
            await engine.dispose()

    asyncio.run(run())
