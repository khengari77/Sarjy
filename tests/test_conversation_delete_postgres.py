"""Disposable PostgreSQL acceptance: how a conversation delete interleaves with concurrent writers."""

import asyncio
import os
from uuid import uuid4

import pytest
from app.infrastructure.database import Conversation, Identity, Memory, Turn
from app.routes.worker import _live_conversation
from app.services.conversation_delete import delete_conversation
from app.services.memory_erasure_lock import lock_owner_memories
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _url():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires a disposable migrated PostgreSQL")
    return url


async def _blocked_sessions(sessions) -> int:
    """Sessions waiting on a lock in a statement the delete issues (its row lock or the owner advisory lock)."""
    async with sessions() as observer:
        return await observer.scalar(
            text(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and wait_event_type = 'Lock' and query ~* '(for no key update|pg_advisory)'"
            )
        )


async def _wait_until_blocked(sessions) -> None:
    for _ in range(100):
        if await _blocked_sessions(sessions):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the delete never started waiting on a lock")


async def _setup(sessions):
    owner, chat = str(uuid4()), str(uuid4())
    async with sessions() as setup:
        setup.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
        await setup.commit()
        setup.add(Conversation(id=chat, owner_id=owner))
        await setup.commit()
    return owner, chat


def test_postgres_a_delete_waits_for_a_shared_writer_and_then_erases_what_it_wrote():
    url = _url()

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, chat = await _setup(sessions)
            async with sessions() as writer, sessions() as eraser:
                await _live_conversation(writer, chat, share=True)  # a late finalize holds the row
                deleting = asyncio.create_task(delete_conversation(eraser, owner_id=owner, conversation_id=chat))
                await _wait_until_blocked(sessions)
                assert not deleting.done(), "the delete must wait for a writer that already read the live chat"
                writer.add(
                    Turn(id=str(uuid4()), conversation_id=chat, request_id="late", role="assistant", text="late")
                )
                await writer.commit()
                await asyncio.wait_for(deleting, 10)
            async with sessions() as check:
                assert await check.scalar(select(Turn.text).where(Turn.conversation_id == chat)) == ""
            async with sessions() as late:
                with pytest.raises(Exception, match="404"):
                    await _live_conversation(late, chat, share=True)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_a_delete_cannot_deadlock_with_a_memory_write_holding_the_owner_lock():
    """MemoryService writes under the owner lock and inserts a memory (a foreign key to the conversation row).

    The delete holds the conversation row and then waits for the owner lock. A full row lock would block the memory
    insert's key check and deadlock; FOR NO KEY UPDATE lets the insert through, and the global fact survives.
    """
    url = _url()

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, chat = await _setup(sessions)
            memory = str(uuid4())
            async with sessions() as writer, sessions() as eraser:
                await lock_owner_memories(writer, owner)
                deleting = asyncio.create_task(delete_conversation(eraser, owner_id=owner, conversation_id=chat))
                await _wait_until_blocked(sessions)
                writer.add(Memory(id=memory, owner_id=owner, source_conversation_id=chat, content="I like tea"))
                await writer.commit()  # a deadlock would abort one side here
                await asyncio.wait_for(deleting, 10)
            async with sessions() as check:
                row = await check.get(Memory, memory)
                assert row is not None and row.status == "active" and row.content == "I like tea"
        finally:
            await engine.dispose()

    asyncio.run(run())
