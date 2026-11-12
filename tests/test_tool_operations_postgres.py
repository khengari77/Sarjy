"""Required PostgreSQL duplicate-claim and terminal replay regression."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.infrastructure.database import Identity, Conversation, Turn
from app.services.tool_operations import (
    claim_tool_operation,
    complete_tool_operation,
    unknown_tool_operation,
    ToolOperationError,
)


def test_postgres_concurrent_operation_claim_and_terminal_replay():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("Disposable PostgreSQL required")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        key = str(uuid4())
        async with sessions() as db:
            db.add(Identity(id=key, token_hash=key, expires_at="2030"))
            await db.flush()
            db.add(Conversation(id=key, owner_id=key))
            await db.flush()
            db.add(Turn(id=key, conversation_id=key, request_id=key, role="user", text="Weather?"))
            await db.commit()

        async def claim(operation=key, city="Tripoli"):
            async with sessions() as db:
                result = await claim_tool_operation(
                    db, turn_id=key, operation_key=operation, name="weather", arguments={"city": city}
                )
                await db.commit()
                return result

        try:
            claims = await asyncio.gather(*(claim() for _ in range(8)))
            effects = 0
            for result in claims:
                if result.kind == "claimed":
                    effects += 1
                    async with sessions() as db:
                        await complete_tool_operation(db, operation_key=key, result={"city": "Tripoli"})
                        await db.commit()
            assert effects == 1
            assert sum(result.kind == "pending" for result in claims) == 7
            assert (await claim()).result == {"city": "Tripoli"}
            with pytest.raises(ToolOperationError, match="TOOL_OPERATION_CONFLICT"):
                await claim(city="Rome")
            uncertain = key + ":unknown"
            assert (await claim(uncertain)).kind == "claimed"
            async with sessions() as db:
                await unknown_tool_operation(db, operation_key=uncertain, error_code="TOOL_DELIVERY_UNKNOWN")
                await db.commit()
            assert (await claim(uncertain)).kind == "unknown"
        finally:
            await engine.dispose()

    asyncio.run(run())
