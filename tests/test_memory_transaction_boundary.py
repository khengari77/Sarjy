"""Memory writes must never commit or roll back the caller's business session."""
import pytest
from sqlalchemy import select

from app.infrastructure.database import Conversation, MemoryWriteRequest
from app.services.memory import MemoryService
from test_memory import FakeEmbedder, setup


@pytest.mark.parametrize('flush', [False, True])
def test_pending_caller_write_is_rejected_without_mutation(tmp_path, flush):
    async def run():
        engine, sessions = await setup(tmp_path / 'boundary.db')
        try:
            async with sessions() as caller:
                conversation = await caller.get(Conversation, 'a1')
                conversation.title = 'Edited title'
                if flush:
                    await caller.flush()
                with pytest.raises(Exception) as error:
                    await MemoryService(caller, FakeEmbedder()).create_memory(
                        'alice', 'a1', None, 'Tripoli', 'boundary')
                assert getattr(error.value, 'detail', {}).get('code') == 'MEMORY_CALLER_TRANSACTION_ACTIVE'
                assert conversation.title == 'Edited title'
                await caller.rollback()
            async with sessions() as db:
                assert (await db.get(Conversation, 'a1')).title == 'New conversation'
                assert await db.scalar(select(MemoryWriteRequest)) is None
        finally:
            await engine.dispose()
    __import__('asyncio').run(run())


def test_clean_caller_session_writes_without_touching_its_transaction(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / 'read-txn.db')
        try:
            async with sessions() as caller:
                await caller.scalar(select(Conversation).where(Conversation.id == 'a1'))
                assert caller.in_transaction()
                result = await MemoryService(caller, FakeEmbedder()).create_memory(
                    'alice', 'a1', None, 'Tripoli', 'clean-caller')
                assert result.content == 'Tripoli'
                assert caller.in_transaction()
                await caller.rollback()
            async with sessions() as db:
                assert (await db.scalar(select(MemoryWriteRequest))).state == 'completed'
        finally:
            await engine.dispose()
    __import__('asyncio').run(run())
