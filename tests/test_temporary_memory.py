"""The conversation-scoped (temporary) memory write path: scope, floor, dedupe, cap and erasure."""

import asyncio
import hashlib
import json

import pytest
from app.domain.contracts import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, SCOPE_CONVERSATION, SCOPE_GLOBAL
from app.infrastructure.database import Conversation, Memory, MemoryTerm, MemoryWriteRequest, Turn, now
from app.services import memory as memory_module
from app.services.memory import MemoryService, _fingerprint
from app.services.memory_terms import terms_of
from app.services.temporary_memory import (
    PromotionConflict,
    TemporaryFactOutcome,
    _request_key,
    promote_temporary_memory,
    write_temporary_fact,
)
from fastapi import HTTPException
from sqlalchemy import func, null, select, update
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
        "وكلمة المرور هي hunter2",
        "بالرقم السري 8877",
        "ورمز التحقق 4567",
        "للرقم السري 1234",
        "my openai token is sk-proj-abcd1234",
        "the github key is ghp_abcdEFGH12345678",
        "the aws key is AKIAABCDEFGH12345678",
        "the jwt is eyJhbGciOiJIUzI1NiJ9.payload",
        "my card is 4012 8888 8888 1881",
        "my card is 4,012,8888,8888,1881",
        "my card is 4012–8888–8888–1881",
        "my card is 4012٬8888٬8888٬1881",
        "my card is 4012\u00a08888\u00a08888\u00a01881",
        "my card is ٤٠١٢-٨٨٨٨-٨٨٨٨-١٨٨١",
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


@pytest.mark.parametrize(
    "text",
    [
        "أريد بطاقة ائتمان جديدة",
        "the token budget is low",
        "I start my new job on 2026-11-28",
        "the meeting is on 28/11/2026 at 14:30",
        "it costs 1,234,567",
        "my favorite number is 7",
        "I live at 12 Rose Street",
    ],
)
def test_ordinary_prose_about_a_card_or_budget_is_still_written(tmp_path, text):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "ordinary.db")
        await add_turn(sessions, "turn-ordinary", "a1")
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-ordinary",
                text=text,
                subject=None,
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert await count(sessions, Memory, Memory.status == "active") == 1
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


def test_default_fingerprint_keeps_the_pre_temporary_hash_and_a_temporary_one_differs():
    payload = {
        "kind": "create",
        "conversation_id": "a1",
        "source_turn_id": "turn",
        "content": "Fact",
        "previous_id": None,
        "expected_version": None,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
    }
    before = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert _fingerprint("a1", "turn", "Fact", None, None, scope=SCOPE_GLOBAL, embed=True) == before
    assert _fingerprint("a1", "turn", "Fact", None, None, scope=SCOPE_CONVERSATION, embed=False) != before


def test_hidden_permanent_facts_do_not_come_back(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "hidden-permanent.db")
        await add_turn(sessions, "turn-hidden", "a1")
        service = MemoryService(engine, FakeEmbedder())
        await service.create_memory("alice", "a1", None, "I live in Benghazi", "hidden-location")
        try:
            async with sessions() as db:
                await db.execute(update(Memory).values(status="hidden"))
                await db.commit()
            for text, subject in (("I live in Tripoli", "location"), ("I live in Benghazi", "other-subject")):
                result = await write_temporary_fact(
                    sessions,
                    owner_id="alice",
                    conversation_id="a1",
                    turn_id="turn-hidden",
                    text=text,
                    subject=subject,
                )
                assert result.outcome is TemporaryFactOutcome.SKIPPED_HIDDEN
            assert await count(sessions, Memory, Memory.scope == SCOPE_CONVERSATION) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_chat_clear_hidden_temporary_fact_does_not_come_back(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "hidden-chat.db")
        await add_turn(sessions, "turn-one", "a1")
        await add_turn(sessions, "turn-two", "a1")
        try:
            first = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-one",
                text="I live in Benghazi",
                subject="location",
            )
            async with sessions() as db:
                await db.execute(update(Memory).where(Memory.id == first.memory.id).values(status="hidden"))
                await db.commit()
            again = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-two",
                text="I live in Benghazi",
                subject="location",
            )
            assert again.outcome is TemporaryFactOutcome.SKIPPED_HIDDEN
            assert await count(sessions, Memory, Memory.status == "active") == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_the_resolved_subject_supersedes_a_fact_when_the_caller_passes_none(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "resolved-subject.db")
        await add_turn(sessions, "turn-one", "a1")
        await add_turn(sessions, "turn-two", "a1")
        try:
            first = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-one",
                text="I live in Benghazi",
                subject=None,
            )
            second = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-two",
                text="I live in Tripoli",
                subject=None,
            )
            assert first.outcome is TemporaryFactOutcome.WRITTEN
            assert second.outcome is TemporaryFactOutcome.REPLACED
            assert second.memory is not None and second.memory.supersedes_id == first.memory.id
            assert await count(sessions, Memory, Memory.status == "active") == 1
            assert await count(sessions, Memory, Memory.status == "active", Memory.content == "I live in Tripoli") == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _seed_active(sessions, count_: int, prefix: str = "seed") -> list[str]:
    ids = []
    async with sessions() as db:
        for number in range(count_):
            identifier = f"{prefix}-{number}"
            ids.append(identifier)
            db.add(
                Memory(
                    id=identifier,
                    owner_id="alice",
                    content=f"{prefix} fact {number}",
                    subject=f"{prefix}:{number}",
                    source_conversation_id="a1",
                    version=1,
                    status="active",
                    scope=SCOPE_CONVERSATION,
                    created_at=f"2026-01-01T00:00:{number:02d}+00:00",
                    updated_at=f"2026-01-01T00:00:{number:02d}+00:00",
                )
            )
        await db.commit()
    return ids


def test_the_cap_evicts_every_excess_row_after_a_concurrent_overrun(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "cap-overrun.db")
        await add_turn(sessions, "turn-new", "a1")
        try:
            seeded = await _seed_active(sessions, 31)
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-new",
                text="Newest fact",
                subject="newest",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert await count(sessions, Memory, Memory.status == "active") == 30
            async with sessions() as db:
                assert (await db.get(Memory, seeded[0])).status == "deleted"
                assert (await db.get(Memory, seeded[1])).status == "deleted"
                assert (await db.get(Memory, result.memory.id)).status == "active"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_cap_eviction_failure_does_not_fail_the_committed_write(tmp_path, monkeypatch):
    async def run():
        import app.services.temporary_memory as temporary

        engine, sessions = await memory_setup(tmp_path / "cap-failure.db")
        await add_turn(sessions, "turn-new", "a1")

        async def broken_delete(*args, **kwargs):
            raise RuntimeError("eviction failed")

        monkeypatch.setattr(temporary, "delete_memory", broken_delete)
        try:
            await _seed_active(sessions, 31)
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-new",
                text="Newest fact",
                subject="newest",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            assert await count(sessions, Memory, Memory.status == "active") == 32
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("subject", ["my password is hunter2", "it", "x" * 121, 123])
def test_the_subject_floor_and_bound_reject_before_writing(tmp_path, subject):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "subject-floor.db")
        await add_turn(sessions, "turn-subject", "a1")
        try:
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-subject",
                text="I live in Benghazi",
                subject=subject,
            )
            assert result.outcome is TemporaryFactOutcome.REJECTED
            assert await count(sessions, Memory) == 0
            assert await count(sessions, MemoryWriteRequest) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_concurrent_replace_conflict_is_an_outcome_not_an_exception(tmp_path, monkeypatch):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "conflict.db")
        await add_turn(sessions, "turn-one", "a1")
        await add_turn(sessions, "turn-two", "a1")
        try:
            await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-one",
                text="I live in Benghazi",
                subject="location",
            )

            async def conflict(self, *args, **kwargs):
                raise HTTPException(409, detail={"code": "MEMORY_VERSION_CONFLICT"})

            monkeypatch.setattr(MemoryService, "correct_memory", conflict)
            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-two",
                text="I live in Tripoli",
                subject="location",
            )
            assert result.outcome is TemporaryFactOutcome.CONFLICT
            assert await count(sessions, Memory, Memory.status == "active") == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_promote_temporary_memory_changes_the_same_row_in_place(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "promote.db")
        await add_turn(sessions, "turn-promote", "a1")
        try:
            written = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-promote",
                text="I live in Benghazi",
                subject="location",
            )
            promoted = await promote_temporary_memory(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                memory_id=written.memory.id,
                expected_version=1,
                content="I live in Tripoli",
                subject="location",
                embedder=FakeEmbedder(),
            )
            assert promoted.id == written.memory.id
            assert promoted.version == 2
            assert promoted.source_conversation_id == "a1"
            assert promoted.supersedes_id is None
            async with sessions() as db:
                row = await db.get(Memory, written.memory.id)
                assert row.scope == SCOPE_GLOBAL and row.status == "active"
                assert row.content == "I live in Tripoli"
                terms = set(
                    (await db.scalars(select(MemoryTerm.term).where(MemoryTerm.memory_id == written.memory.id))).all()
                )
            assert "benghazi" not in terms and "tripoli" in terms
            with pytest.raises(PromotionConflict):
                await promote_temporary_memory(
                    sessions,
                    owner_id="alice",
                    conversation_id="a1",
                    memory_id=written.memory.id,
                    expected_version=1,
                    content="I live in Tripoli",
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_cap_eviction_is_quiet_for_unbound_proposals_and_scrubs_bound_rows(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "quiet-cap.db")
        await add_turn(sessions, "turn-new", "a1")
        try:
            async with sessions() as db:
                db.add(
                    Memory(
                        id="evict-me",
                        owner_id="alice",
                        content="I prefer tea",
                        subject="preference:beverage",
                        source_conversation_id="a1",
                        version=1,
                        status="active",
                        scope=SCOPE_CONVERSATION,
                        request_key="temp-request",
                        created_at="2020-01-01T00:00:00+00:00",
                        updated_at="2020-01-01T00:00:00+00:00",
                    )
                )
                db.add(
                    MemoryWriteRequest(
                        id="bound-request",
                        owner_id="alice",
                        conversation_id="a1",
                        request_key="temp-request",
                        fingerprint="bound",
                        scope=SCOPE_CONVERSATION,
                        state="completed",
                        result={"id": "evict-me", "owner_id": "alice", "content": "I prefer tea"},
                        created_at="2020-01-01T00:00:00+00:00",
                        updated_at="2020-01-01T00:00:00+00:00",
                    )
                )
                db.add(
                    MemoryWriteRequest(
                        id="other-chat-proposal",
                        owner_id="alice",
                        conversation_id="a2",
                        request_key="other-chat-proposal",
                        fingerprint="proposal",
                        scope=SCOPE_GLOBAL,
                        state="proposed",
                        result={"proposal": {"text": "I prefer tea", "confirmation_id": "confirmation-1"}},
                        created_at="2026-01-01T00:00:00+00:00",
                        updated_at="2026-01-01T00:00:00+00:00",
                    )
                )
                for number in range(30):
                    db.add(
                        Memory(
                            id=f"seed-{number}",
                            owner_id="alice",
                            content=f"Seed fact {number}",
                            subject=f"seed:{number}",
                            source_conversation_id="a1",
                            version=1,
                            status="active",
                            scope=SCOPE_CONVERSATION,
                            created_at=f"2026-01-01T00:00:{number:02d}+00:00",
                            updated_at=f"2026-01-01T00:00:{number:02d}+00:00",
                        )
                    )
                await db.commit()

            result = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-new",
                text="Newest fact",
                subject="newest",
            )
            assert result.outcome is TemporaryFactOutcome.WRITTEN
            async with sessions() as db:
                evicted = await db.get(Memory, "evict-me")
                assert evicted.status == "deleted" and evicted.content == ""
                bound = await db.get(MemoryWriteRequest, "bound-request")
                assert bound.result is None and bound.embedding is None
                proposal = await db.get(MemoryWriteRequest, "other-chat-proposal")
                assert proposal.state == "proposed"
                assert proposal.result["proposal"]["text"] == "I prefer tea"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_embedding_after_promotion_never_writes_back_a_deleted_row(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "promotion-delete-embed.db")
        await add_turn(sessions, "turn-promote", "a1")
        try:
            written = await write_temporary_fact(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                turn_id="turn-promote",
                text="I live in Benghazi",
                subject="location",
            )

            class DeletingEmbedder(FakeEmbedder):
                async def embed(self, db, text):
                    async with sessions() as other:
                        await other.execute(
                            update(Memory)
                            .where(Memory.id == written.memory.id)
                            .values(status="deleted", content="", embedding=null())
                        )
                        await other.commit()
                    return await super().embed(db, text)

            await promote_temporary_memory(
                sessions,
                owner_id="alice",
                conversation_id="a1",
                memory_id=written.memory.id,
                expected_version=1,
                content="I live in Tripoli",
                subject="location",
                embedder=DeletingEmbedder(),
            )
            async with sessions() as db:
                row = await db.get(Memory, written.memory.id)
                assert row.status == "deleted" and row.content == ""
                embedding = await db.scalar(select(Memory.embedding).where(Memory.id == written.memory.id))
                assert embedding is None
        finally:
            await engine.dispose()

    asyncio.run(run())
