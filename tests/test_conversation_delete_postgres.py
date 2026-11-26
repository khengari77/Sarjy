"""Disposable PostgreSQL acceptance: a delete is fenced from a late worker write by the conversation row lock."""

import asyncio
import os
from uuid import uuid4

import pytest
from app.infrastructure.database import Conversation, Identity, Turn
from app.routes.worker import _live_conversation
from app.services.conversation_delete import delete_conversation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_postgres_a_delete_waits_for_a_shared_writer_and_then_erases_what_it_wrote():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires a disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat = str(uuid4()), str(uuid4())
        try:
            async with sessions() as setup:
                setup.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await setup.commit()
                setup.add(Conversation(id=chat, owner_id=owner))
                await setup.commit()
            async with sessions() as writer, sessions() as eraser:
                await _live_conversation(writer, chat, share=True)  # a late finalize holds the row
                deleting = asyncio.create_task(delete_conversation(eraser, owner_id=owner, conversation_id=chat))
                await asyncio.sleep(0.5)
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
