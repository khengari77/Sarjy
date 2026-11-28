"""The conversation-scoped (temporary) memory write path: scope, floor, dedupe, cap and erasure."""

import asyncio

import pytest
from app.domain.contracts import SCOPE_CONVERSATION, SCOPE_GLOBAL
from app.infrastructure.database import Conversation, Memory, MemoryTerm, MemoryWriteRequest, Turn, now
from app.services import memory as memory_module
from app.services.memory import MemoryService
from app.services.memory_terms import terms_of
from app.services.temporary_memory import (
    TemporaryFactOutcome,
    _request_key,
    write_temporary_fact,
)
from fastapi import HTTPException
from sqlalchemy import func, select, update
from test_memory import FakeEmbedder
from test_memory import setup as memory_setup


async def add_turn(sessions, turn_id: str, conversation_id: str) -> None:
    async with sessions() as db:
        db.add(
            Turn(
                id=turn_id,
                conversation_id=conversation_id,
                request_id=turn_id,
                role="user",
                text="hello",
                status="completed",
            )
        )
        await db.commit()


async def memories(sessions, owner_id: str = "alice") -> list[Memory]:
    async with sessions() as db:
        return list(
            (await db.scalars(select(Memory).where(Memory.owner_id == owner_id).order_by(Memory.created_at))).all()
        )


async def count(sessions, table, *conditions) -> int:
    async with sessions() as db:
        return int(await db.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)


def test_temporary_fact_is_conversation_scoped_unembedded_and_lexically_indexed(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "temporary.db")
        await add_turn(sessions, "turn-temp", "a1")
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-temp",
                text="I live in Benghazi",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert result.memory is not None
            async with sessions() as db:
                row = await db.get(Memory, result.memory.id)
                embedding = await db.scalar(select(Memory.embedding).where(Memory.id == result.memory.id))
                assert row.scope == SCOPE_CONVERSATION
                assert row.source_conversation_id == "a1"
                assert row.source_turn_id == "turn-temp"
                assert row.subject == "location"
                assert row.status == "active"
                assert embedding is None
                terms = set(
                    (await db.scalars(select(MemoryTerm.term).where(MemoryTerm.memory_id == result.memory.id))).all()
                )
                assert terms == terms_of("I live in Benghazi")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unknown_foreign_and_tombstoned_chats_write_nothing(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "missing-chat.db")
        try:
            foreign = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="b1",
                turn_id="foreign",
                text="I live in Benghazi",
                subject="location",
            )
            assert foreign.outcome is TemporaryFactOutcome.CHAT_GONE

            unowned = await write_temporary_fact(
                sessions,
                owner_id="bob",
                conversation_id="a1",
                turn_id="unowned",
                text="I live in Benghazi",
                subject="location",
            )
            assert unowned.outcome is TemporaryFactOutcome.CHAT_GONE

            async with sessions() as db:
                await db.execute(
                    update(Conversation).where(Conversation.id == "a2").values(deleted_at="2026-11-28T00:00:00+00:00")
                )
                await db.commit()
            tombstoned = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a2",
                turn_id="tombstoned",
                text="I live in Benghazi",
                subject="location",
            )
            assert tombstoned.outcome is TemporaryFactOutcome.CHAT_GONE
            assert await count(sessions, Memory) == 0
            assert await count(sessions, MemoryTerm) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_the_service_raises_a_typed_error_for_a_missing_chat(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "typed.db")
        try:
            with pytest.raises(HTTPException) as error:
                await MemoryService(engine, FakeEmbedder()).create_memory(
                    "alice",
                    "b1",
                    None,
                    "I live in Benghazi",
                    "missing-chat",
                    scope=SCOPE_CONVERSATION,
                    embed=False,
                )
            assert error.value.status_code == 404
            assert error.value.detail == {"code": "CONVERSATION_NOT_FOUND"}
            assert await count(sessions, Memory) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_retried_write_is_one_row_and_writes_nothing_again(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "replay.db")
        await add_turn(sessions, "turn-replay", "a1")
        try:
            first = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-replay",
                text="I prefer tea",
                subject="preference:beverage",
            )
            second = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-replay",
                text="I prefer tea",
                subject="preference:beverage",
            )
            assert second.outcome is TemporaryFactOutcome.WRITTEN
            assert second.memory is not None and first.memory is not None
            assert second.memory.id == first.memory.id
            assert await count(sessions, Memory) == 1
            assert await count(sessions, MemoryWriteRequest) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_replay_with_a_different_scope_is_rejected(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "scope-replay.db")
        await add_turn(sessions, "turn-scope", "a1")
        try:
            await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-scope",
                text="I prefer tea",
                subject="preference:beverage",
            )
            key = _request_key("turn-scope", "I prefer tea")
            with pytest.raises(HTTPException) as error:
                await MemoryService(engine, FakeEmbedder()).create_memory(
                    "alice",
                    "a1",
                    "turn-scope",
                    "I prefer tea",
                    key,
                    # Same text, key and (no) embedding: only the scope differs, so the scope must be in the fingerprint.
                    scope=SCOPE_GLOBAL,
                    embed=False,
                )
            assert error.value.status_code == 409
            assert error.value.detail == {"code": "IDEMPOTENCY_CONFLICT"}
            assert await count(sessions, Memory) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_same_subject_temporary_fact_is_replaced_only_in_its_own_chat(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "same-subject.db")
        await add_turn(sessions, "turn-a1", "a1")
        await add_turn(sessions, "turn-a2", "a2")
        try:
            old = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-a1",
                text="I live in Benghazi",
                subject="location",
            )
            other = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a2",
                turn_id="turn-a2",
                text="I live in Tripoli",
                subject="location",
            )
            new = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-a1",
                text="I live in Misrata",
                subject="location",
            )
            assert new.outcome is TemporaryFactOutcome.REPLACED
            assert new.memory is not None and new.memory.supersedes_id == old.memory.id
            assert other.outcome is TemporaryFactOutcome.WRITTEN
            async with sessions() as db:
                old_row = await db.get(Memory, old.memory.id)
                other_row = await db.get(Memory, other.memory.id)
                assert old_row.status == "superseded"
                assert other_row.status == "active" and other_row.source_conversation_id == "a2"
            assert await count(sessions, Memory, Memory.status == "active") == 2
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_an_exact_duplicate_in_the_chat_is_a_no_op(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "duplicate.db")
        await add_turn(sessions, "turn-one", "a1")
        await add_turn(sessions, "turn-two", "a1")
        try:
            first = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-one",
                text="I prefer tea",
                subject="preference:beverage",
            )
            duplicate = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-two",
                text="I prefer tea",
                subject="preference:beverage",
            )
            assert duplicate.outcome is TemporaryFactOutcome.DUPLICATE
            assert duplicate.memory is not None and duplicate.memory.id == first.memory.id
            assert await count(sessions, Memory) == 1
            assert await count(sessions, MemoryWriteRequest) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_permanent_fact_with_the_same_subject_or_content_wins(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "permanent.db")
        await add_turn(sessions, "turn-permanent", "a1")
        service = MemoryService(engine, FakeEmbedder())
        await service.create_memory("alice", "a1", None, "I live in Benghazi", "permanent-location")
        try:
            skipped = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-permanent",
                text="I live in Tripoli",
                subject="location",
            )
            assert skipped.outcome is TemporaryFactOutcome.SKIPPED_PERMANENT
            assert await count(sessions, Memory, Memory.scope == SCOPE_CONVERSATION) == 0

            await service.create_memory("alice", "a1", None, "I prefer tea", "permanent-content")
            same_content = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-permanent",
                text="I prefer tea",
                subject="preference:other",
            )
            assert same_content.outcome is TemporaryFactOutcome.SKIPPED_PERMANENT
            assert await count(sessions, Memory, Memory.scope == SCOPE_CONVERSATION) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "my password is hunter2",
        "كلمة المرور هي 123456",
        "my card is 4012 8888 8888 1881",
        "email me at person@example.com",
        "call me on 091 234 5678",
        "remember it",
    ],
)
def test_the_credential_and_contact_floor_writes_nothing(tmp_path, text):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "floor.db")
        await add_turn(sessions, "turn-floor", "a1")
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-floor",
                text=text,
                subject=None,
            )
            assert result.outcome is TemporaryFactOutcome.REJECTED
            assert result.memory is None
            assert await count(sessions, Memory) == 0
            assert await count(sessions, MemoryWriteRequest) == 0
            assert await count(sessions, MemoryTerm) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_the_cap_evicts_the_oldest_temporary_fact_and_scrubs_it(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "cap.db")
        await add_turn(sessions, "turn-cap", "a1")
        try:
            first = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-cap",
                text="Fact number 1",
                subject="fact:1",
            )
            async with sessions() as db:
                await db.execute(
                    update(Memory).where(Memory.id == first.memory.id).values(updated_at="2020-01-01T00:00:00+00:00")
                )
                await db.commit()
            for number in range(2, 32):
                result = await write_temporary_fact(
                    sessions,
                    owner_id="alice",
                    conversation_id="a1",
                    turn_id="turn-cap",
                    text=f"Fact number {number}",
                    subject=f"fact:{number}",
                )
                assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert await count(sessions, Memory, Memory.status == "active") == 30
            assert await count(sessions, Memory, Memory.status == "active", Memory.scope == SCOPE_CONVERSATION) == 30
            async with sessions() as db:
                evicted = await db.get(Memory, first.memory.id)
                assert evicted.status == "deleted"
                assert evicted.content == ""
                assert evicted.subject is None
                assert (await db.scalar(select(Memory.embedding).where(Memory.id == first.memory.id))) is None
            assert await count(sessions, MemoryTerm, MemoryTerm.memory_id == first.memory.id) == 0
            assert await count(sessions, Memory, Memory.status == "active", Memory.subject == "fact:31") == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_delete_that_wins_the_race_leaves_no_row_or_terms(tmp_path, monkeypatch):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "race.db")
        await add_turn(sessions, "turn-race", "a1")
        original = MemoryService._finalize

        async def tombstone_then_finalize(self, *args, **kwargs):
            async with sessions() as db:
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == "a1")
                    .values(deleted_at=now(), title="Deleted conversation")
                )
                await db.commit()
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(MemoryService, "_finalize", tombstone_then_finalize)
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-race",
                text="I live in Benghazi",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.CHAT_GONE
            assert await count(sessions, Memory) == 0
            assert await count(sessions, MemoryTerm) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_temporary_write_takes_the_conversation_lock_before_the_owner_lock(tmp_path, monkeypatch):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "lock-order.db")
        await add_turn(sessions, "turn-lock", "a1")
        calls = []
        real_owned = memory_module.owned_conversation

        async def owned_spy(db, owner_id, conversation_id, *, lock=False, include_deleted=False, key_share=False):
            calls.append(("conversation", lock, key_share))
            return await real_owned(
                db, owner_id, conversation_id, lock=lock, include_deleted=include_deleted, key_share=key_share
            )

        async def owner_spy(db, owner_id):
            calls.append(("owner", owner_id))

        monkeypatch.setattr(memory_module, "owned_conversation", owned_spy)
        monkeypatch.setattr(memory_module, "lock_owner_memories", owner_spy)
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-lock",
                text="I live in Benghazi",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            conversation_lock = calls.index(("conversation", True, True))
            assert conversation_lock < calls.index(("owner", "alice"))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_the_mode_off_does_not_stop_a_temporary_fact(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "mode-off.db")
        await add_turn(sessions, "turn-off", "a1")
        try:
            async with sessions() as db:
                await db.execute(update(Conversation).where(Conversation.id == "a1").values(memory_mode="off"))
                await db.commit()
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-off",
                text="I live in Benghazi",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert await count(sessions, Memory, Memory.scope == SCOPE_CONVERSATION, Memory.status == "active") == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
