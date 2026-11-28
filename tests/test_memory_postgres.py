"""Real pgvector and concurrent-version acceptance; disposable database only."""

import asyncio
import os
from uuid import uuid4

import pytest
from app.infrastructure.database import (
    ContextOperation,
    Conversation,
    Identity,
    Memory,
    MemoryTerm,
    MemoryWriteRequest,
    Turn,
    now,
)
from app.services.conversation_delete import delete_conversation
from app.services.memory import MemoryService
from app.services.memory_auto import undo_change
from app.services.memory_terms import terms_of
from app.services.memory_visibility import delete_memory
from app.services.temporary_memory import (
    PromotionConflict,
    TemporaryFactOutcome,
    promote_temporary_memory,
    write_temporary_fact,
)
from app.services.voice_exclusions import hide_target, resolve_confirmation
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_memory import FakeEmbedder


def test_postgres_confirm_and_memory_insert_in_one_chat_do_not_deadlock():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat = (str(uuid4()) for _ in range(2))
        secret = "postgres-confirm-secret"
        try:
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add(Conversation(id=chat, owner_id=owner))
                await db.commit()
            await MemoryService(sessions, FakeEmbedder()).create_memory(
                owner, chat, None, "I travelled to Paris", "seed-paris"
            )
            async with sessions() as db:
                preview = await hide_target(
                    db, owner_id=owner, conversation_id=chat, target="Paris", request_id="preview", secret=secret
                )
                assert preview.detail == "MEMORY_HIDE_PROPOSED"

            async def confirm():
                async with sessions() as db:
                    return await resolve_confirmation(
                        db,
                        owner_id=owner,
                        conversation_id=chat,
                        confirmed=True,
                        request_id="confirm",
                        secret=secret,
                    )

            async def insert():
                return await MemoryService(sessions, FakeEmbedder()).create_memory(
                    owner, chat, None, "I live in London", "insert-during-confirm"
                )

            applied, _created = await asyncio.wait_for(asyncio.gather(confirm(), insert()), timeout=10)
            assert applied is not None and applied.detail == "MEMORY_HIDDEN"
        finally:
            await engine.dispose()

    asyncio.run(run())


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


def test_postgres_temporary_fact_is_written_and_erased_with_its_chat():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat = (str(uuid4()) for _ in range(2))
        try:
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add(Conversation(id=chat, owner_id=owner))
                await db.commit()
                db.add(
                    Turn(
                        id="turn",
                        conversation_id=chat,
                        request_id="turn",
                        role="user",
                        text="hello",
                        status="completed",
                    )
                )
                await db.commit()

            result = await write_temporary_fact(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                turn_id="turn",
                text="I live in Benghazi",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            async with sessions() as db:
                row = await db.get(Memory, result.memory.id)
                assert row.scope == "conversation" and row.source_conversation_id == chat
                assert (await db.scalar(select(Memory.embedding).where(Memory.id == result.memory.id))) is None
                terms = set(
                    (await db.scalars(select(MemoryTerm.term).where(MemoryTerm.memory_id == result.memory.id))).all()
                )
                assert terms == terms_of("I live in Benghazi")

            async with sessions() as db:
                await delete_conversation(db, owner_id=owner, conversation_id=chat)
            async with sessions() as db:
                row = await db.get(Memory, result.memory.id)
                assert row.status == "deleted" and row.content == ""
                assert (
                    await db.scalar(
                        select(func.count()).select_from(MemoryTerm).where(MemoryTerm.memory_id == result.memory.id)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _postgres_setup(sessions, *, chats: int = 1):
    owner, *chat_ids = (str(uuid4()) for _ in range(chats + 1))
    async with sessions() as db:
        db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
        await db.commit()
        for chat in chat_ids:
            db.add(Conversation(id=chat, owner_id=owner))
        await db.commit()
        db.add(
            Turn(
                id=f"turn-{owner[:31]}",
                conversation_id=chat_ids[0],
                request_id=f"turn-{owner[:31]}",
                role="user",
                text="hello",
                status="completed",
            )
        )
        await db.commit()
    return owner, chat_ids


def test_postgres_promote_temporary_memory_happy_path_and_stale_version():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, (chat,) = await _postgres_setup(sessions)
            written = await write_temporary_fact(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                turn_id=f"turn-{owner[:31]}",
                text="I live in Benghazi",
                subject="location",
            )
            promoted = await promote_temporary_memory(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                memory_id=written.memory.id,
                expected_version=1,
                content="I live in Tripoli",
                subject="location",
                embedder=FakeEmbedder(),
            )
            assert promoted.id == written.memory.id and promoted.version == 2
            assert promoted.source_conversation_id == chat and promoted.supersedes_id is None
            async with sessions() as db:
                row = await db.get(Memory, written.memory.id)
                assert row.scope == "global" and row.status == "active"
                assert row.content == "I live in Tripoli"
                terms = set(
                    (await db.scalars(select(MemoryTerm.term).where(MemoryTerm.memory_id == written.memory.id))).all()
                )
                assert "tripoli" in terms and "benghazi" not in terms
            with pytest.raises(PromotionConflict):
                await promote_temporary_memory(
                    sessions,
                    owner_id=owner,
                    conversation_id=chat,
                    memory_id=written.memory.id,
                    expected_version=1,
                    content="I live in Tripoli",
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_promoted_undo_demotes_the_row():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, (chat,) = await _postgres_setup(sessions)
            written = await write_temporary_fact(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                turn_id=f"turn-{owner[:31]}",
                text="My name is Sam",
                subject="attribute:name",
            )
            promoted = await promote_temporary_memory(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                memory_id=written.memory.id,
                expected_version=1,
                content="My name is Sam",
                subject="attribute:name",
                embedder=FakeEmbedder(),
            )
            async with sessions() as db:
                operation = ContextOperation(
                    id=str(uuid4()),
                    owner_id=owner,
                    conversation_id=chat,
                    command_type="memory_auto_save",
                    payload={
                        "kind": "memory_saved",
                        "promoted_from": "conversation",
                        "memory_id": promoted.id,
                        "version": promoted.version,
                    },
                    idempotency_key=f"promote-{promoted.id}",
                    outcome="applied",
                )
                db.add(operation)
                await db.commit()
                await undo_change(db, owner_id=owner, change_id=operation.id)
            async with sessions() as db:
                row = await db.get(Memory, promoted.id)
                assert row.status == "active" and row.scope == "conversation"
                assert row.version == 3 and row.source_conversation_id == chat
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_cap_eviction_leaves_another_chats_proposal():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, (chat, other) = await _postgres_setup(sessions, chats=2)
            async with sessions() as db:
                db.add(
                    Memory(
                        id="evict-me",
                        owner_id=owner,
                        content="I prefer tea",
                        subject="preference:beverage",
                        source_conversation_id=chat,
                        version=1,
                        status="active",
                        scope="conversation",
                        created_at="2020-01-01T00:00:00+00:00",
                        updated_at="2020-01-01T00:00:00+00:00",
                    )
                )
                db.add(
                    MemoryWriteRequest(
                        id=str(uuid4()),
                        owner_id=owner,
                        conversation_id=other,
                        request_key="other-chat-proposal",
                        fingerprint="proposal",
                        scope="global",
                        state="proposed",
                        result={"proposal": {"text": "I prefer tea", "confirmation_id": "c1"}},
                        created_at=now(),
                        updated_at=now(),
                    )
                )
                for number in range(30):
                    db.add(
                        Memory(
                            id=f"seed-{number}",
                            owner_id=owner,
                            content=f"Seed fact {number}",
                            subject=f"seed:{number}",
                            source_conversation_id=chat,
                            version=1,
                            status="active",
                            scope="conversation",
                            created_at=f"2026-01-01T00:00:{number:02d}+00:00",
                            updated_at=f"2026-01-01T00:00:{number:02d}+00:00",
                        )
                    )
                await db.commit()

            result = await write_temporary_fact(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                turn_id=f"turn-{owner[:31]}",
                text="Newest fact",
                subject="newest",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            async with sessions() as db:
                evicted = await db.get(Memory, "evict-me")
                assert evicted.status == "deleted" and evicted.content == ""
                proposal = await db.scalar(
                    select(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner, MemoryWriteRequest.request_key == "other-chat-proposal"
                    )
                )
                assert proposal.state == "proposed"
                assert proposal.result["proposal"]["text"] == "I prefer tea"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_quiet_retirement_keeps_unbound_proposals():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, (chat, other) = await _postgres_setup(sessions, chats=2)
            async with sessions() as db:
                db.add(
                    Memory(
                        id="temp-duplicate",
                        owner_id=owner,
                        content="My name is Sam",
                        subject="attribute:name",
                        source_conversation_id=chat,
                        version=1,
                        status="active",
                        scope="conversation",
                        request_key="temp-request",
                        created_at=now(),
                        updated_at=now(),
                    )
                )
                db.add(
                    MemoryWriteRequest(
                        id=str(uuid4()),
                        owner_id=owner,
                        conversation_id=chat,
                        request_key="temp-request",
                        fingerprint="bound",
                        scope="conversation",
                        state="completed",
                        result={"response": {"memory": {"id": "temp-duplicate"}}},
                        created_at=now(),
                        updated_at=now(),
                    )
                )
                db.add(
                    MemoryWriteRequest(
                        id=str(uuid4()),
                        owner_id=owner,
                        conversation_id=other,
                        request_key="other-chat-proposal",
                        fingerprint="proposal",
                        scope="global",
                        state="proposed",
                        result={"proposal": {"text": "My name is Sam", "confirmation_id": "c1"}},
                        created_at=now(),
                        updated_at=now(),
                    )
                )
                await db.commit()

            async with sessions() as db:
                await delete_memory(db, owner, "temp-duplicate", scrub_proposals=False)
                await db.commit()
            async with sessions() as db:
                row = await db.get(Memory, "temp-duplicate")
                assert row.status == "deleted" and row.content == ""
                bound = await db.scalar(
                    select(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner, MemoryWriteRequest.request_key == "temp-request"
                    )
                )
                assert bound.result is None
                proposal = await db.scalar(
                    select(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner, MemoryWriteRequest.request_key == "other-chat-proposal"
                    )
                )
                assert proposal.state == "proposed"
                assert proposal.result["proposal"]["text"] == "My name is Sam"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_two_concurrent_promotions_of_one_row_only_one_wins():
    """One winner is guaranteed by the owner advisory lock plus the version-guarded UPDATE.

    The conversation row lock is not what serializes two promotions: both take the owner lock before the UPDATE, and
    the second conditional UPDATE matches zero rows. That row lock protects a promotion against a concurrent delete,
    so removing it would not make this test vacuous.
    """
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner, (chat,) = await _postgres_setup(sessions)
            written = await write_temporary_fact(
                sessions,
                owner_id=owner,
                conversation_id=chat,
                turn_id=f"turn-{owner[:31]}",
                text="My name is Sam",
                subject="attribute:name",
            )

            async def promote():
                return await promote_temporary_memory(
                    sessions,
                    owner_id=owner,
                    conversation_id=chat,
                    memory_id=written.memory.id,
                    expected_version=1,
                    content="My name is Sam",
                    subject="attribute:name",
                    embedder=FakeEmbedder(),
                )

            results = await asyncio.gather(promote(), promote(), return_exceptions=True)
            winners = [result for result in results if not isinstance(result, Exception)]
            conflicts = [result for result in results if isinstance(result, PromotionConflict)]
            assert len(winners) == 1 and len(conflicts) == 1
            async with sessions() as db:
                active = list(
                    await db.scalars(
                        select(Memory).where(
                            Memory.owner_id == owner,
                            Memory.status == "active",
                            Memory.scope == "global",
                        )
                    )
                )
                assert [row.id for row in active] == [written.memory.id]
                assert active[0].version == 2
        finally:
            await engine.dispose()

    asyncio.run(run())
