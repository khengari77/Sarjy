"""Memory writes must never commit or roll back the caller's business session."""

import pytest
from app.infrastructure.database import Conversation, MemoryWriteRequest
from app.services.memory import MemoryService
from sqlalchemy import select
from test_memory import FakeEmbedder, setup


@pytest.mark.parametrize("flush", [False, True])
def test_pending_caller_write_is_rejected_without_mutation(tmp_path, flush):
    async def run():
        engine, sessions = await setup(tmp_path / "boundary.db")
        try:
            async with sessions() as caller:
                conversation = await caller.get(Conversation, "a1")
                conversation.title = "Edited title"
                if flush:
                    await caller.flush()
                with pytest.raises(Exception) as error:
                    await MemoryService(caller, FakeEmbedder()).create_memory(
                        "alice", "a1", None, "Tripoli", "boundary"
                    )
                assert getattr(error.value, "detail", {}).get("code") == "MEMORY_CALLER_TRANSACTION_ACTIVE"
                assert conversation.title == "Edited title"
                await caller.rollback()
            async with sessions() as db:
                assert (await db.get(Conversation, "a1")).title == "New conversation"
                assert await db.scalar(select(MemoryWriteRequest)) is None
        finally:
            await engine.dispose()

    __import__("asyncio").run(run())


def test_clean_caller_session_writes_without_touching_its_transaction(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "read-txn.db")
        try:
            async with sessions() as caller:
                await caller.scalar(select(Conversation).where(Conversation.id == "a1"))
                assert caller.in_transaction()
                result = await MemoryService(caller, FakeEmbedder()).create_memory(
                    "alice", "a1", None, "Tripoli", "clean-caller"
                )
                assert result.content == "Tripoli"
                assert caller.in_transaction()
                await caller.rollback()
            async with sessions() as db:
                assert (await db.scalar(select(MemoryWriteRequest))).state == "completed"
        finally:
            await engine.dispose()

    __import__("asyncio").run(run())


def test_sqlite_busy_uses_error_code_not_message(tmp_path, monkeypatch):
    import sqlite3

    from app.services.memory import _sqlite_busy
    from sqlalchemy.exc import OperationalError

    class FakeOrig(Exception):
        sqlite_errorcode = 5

    assert _sqlite_busy(OperationalError("stmt", {}, FakeOrig()))
    assert _sqlite_busy(OperationalError("stmt", {}, sqlite3.OperationalError("database is locked")))
    assert not _sqlite_busy(OperationalError("stmt", {}, sqlite3.OperationalError("no such table")))


def test_postgres_preserves_uncommitted_caller_title(tmp_path):
    import os
    from uuid import uuid4

    from app.infrastructure.database import Base, Identity
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        owner, conversation = str(uuid4()), str(uuid4())
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as caller:
                caller.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await caller.flush()
                caller.add(Conversation(id=conversation, owner_id=owner, title="Original", revision=0))
                await caller.commit()
                chat = await caller.get(Conversation, conversation)
                chat.title = "Edited title"
                result = await MemoryService(caller, FakeEmbedder()).create_memory(
                    owner, conversation, None, "Tripoli", "pg-boundary"
                )
                assert result.content == "Tripoli"
                assert chat.title == "Edited title"
                await caller.commit()
            async with factory() as db:
                assert (await db.get(Conversation, conversation)).title == "Edited title"
                claim = await db.scalar(select(MemoryWriteRequest))
                assert claim.state == "completed"
        finally:
            await engine.dispose()

    __import__("asyncio").run(run())


def test_in_memory_static_pool_is_rejected_before_create(tmp_path):
    from app.infrastructure.database import Base, Identity, Memory, MemoryWriteRequest
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as seed:
                seed.add(Identity(id="owner", token_hash="h", expires_at="2030"))
                seed.add(Conversation(id="chat", owner_id="owner", title="Original", revision=0))
                await seed.commit()
            async with factory() as caller:
                chat = await caller.get(Conversation, "chat")
                chat.title = "Edited"
                await caller.flush()
                with pytest.raises(HTTPException) as error:
                    await MemoryService(caller, FakeEmbedder()).create_memory(
                        "owner", "chat", None, "Tripoli", "shared-create"
                    )
                assert error.value.detail["code"] == "MEMORY_SHARED_CONNECTION_UNSUPPORTED"
                await caller.rollback()
            async with factory() as db:
                assert (await db.get(Conversation, "chat")).title == "Original"
                assert await db.scalar(select(Memory)) is None
                assert await db.scalar(select(MemoryWriteRequest)) is None
        finally:
            await engine.dispose()

    __import__("asyncio").run(run())


def test_in_memory_static_pool_is_rejected_before_correction_read(tmp_path):
    from app.infrastructure.database import Base, Identity, Memory
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as seed:
                seed.add(Identity(id="owner", token_hash="h", expires_at="2030"))
                seed.add(Conversation(id="chat", owner_id="owner", title="Original", revision=0))
                await seed.flush()
                seed.add(
                    Memory(
                        id="memory",
                        owner_id="owner",
                        content="Tripoli",
                        source_conversation_id="chat",
                        version=1,
                        status="active",
                    )
                )
                await seed.commit()
            async with factory() as caller:
                chat = await caller.get(Conversation, "chat")
                chat.title = "Edited"
                await caller.flush()
                with pytest.raises(HTTPException) as error:
                    await MemoryService(caller, FakeEmbedder()).correct_memory(
                        "owner", "memory", 1, "London", "shared-correct"
                    )
                assert error.value.detail["code"] == "MEMORY_SHARED_CONNECTION_UNSUPPORTED"
                await caller.rollback()
            async with factory() as db:
                assert (await db.get(Conversation, "chat")).title == "Original"
                assert (await db.get(Memory, "memory")).content == "Tripoli"
        finally:
            await engine.dispose()

    __import__("asyncio").run(run())
