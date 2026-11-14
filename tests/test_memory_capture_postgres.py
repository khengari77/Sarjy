"""Disposable PostgreSQL acceptance for consent-aware memory capture."""

import asyncio
import os
from uuid import uuid4

import pytest
from app.infrastructure.database import Conversation, Identity, Memory, Turn
from app.services.memory import MemoryService
from app.services.memory_capture import MemoryCaptureError, confirm_memory, propose_memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_memory import FakeEmbedder

SECRET = "capture-secret"


def test_postgres_capture_creates_once_and_corrects_across_conversations():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")

    async def run():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat, other_chat, turn_id = (str(uuid4()) for _ in range(4))
        embedder = FakeEmbedder()
        try:
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add_all([Conversation(id=chat, owner_id=owner), Conversation(id=other_chat, owner_id=owner)])
                await db.commit()
                db.add(Turn(id=turn_id, conversation_id=chat, request_id="capture", role="user", text="I prefer tea"))
                await db.commit()

                proposal = propose_memory(
                    "I prefer tea",
                    owner_id=owner,
                    conversation_id=chat,
                    source_turn_id=turn_id,
                    secret=SECRET,
                )
                assert proposal is not None
                first = await confirm_memory(
                    db,
                    MemoryService(db, embedder),
                    token=proposal["token"],
                    decision="save",
                    owner_id=owner,
                    conversation_id=chat,
                    source_turn_id=None,
                    secret=SECRET,
                )
                assert first["status"] == "saved" and first["memory"]["version"] == 1
                replay = await confirm_memory(
                    db,
                    MemoryService(db, embedder),
                    token=proposal["token"],
                    decision="save",
                    owner_id=owner,
                    conversation_id=chat,
                    source_turn_id=None,
                    secret=SECRET,
                )
                assert replay["status"] == "saved" and replay.get("idempotent") is True

            # A corrected preference from another conversation supersedes the first version.
            async with sessions() as db:
                db.add(
                    Turn(id=str(uuid4()), conversation_id=other_chat, request_id="correct", role="user", text="coffee")
                )
                await db.commit()
                correction = propose_memory(
                    "I prefer coffee now",
                    owner_id=owner,
                    conversation_id=other_chat,
                    source_turn_id=(await db.scalar(select(Turn.id).where(Turn.conversation_id == other_chat))),
                    secret=SECRET,
                )
                clarify = None
                try:
                    await confirm_memory(
                        db,
                        MemoryService(db, embedder),
                        token=correction["token"],
                        decision="save",
                        owner_id=owner,
                        conversation_id=other_chat,
                        source_turn_id=None,
                        secret=SECRET,
                    )
                except MemoryCaptureError as exc:
                    clarify = exc
                assert clarify is not None and clarify.code == "MEMORY_CLARIFICATION_REQUIRED"
                result = await confirm_memory(
                    db,
                    MemoryService(db, embedder),
                    token=clarify.details["token"],
                    decision="replace",
                    owner_id=owner,
                    conversation_id=other_chat,
                    source_turn_id=None,
                    secret=SECRET,
                )
                assert result["status"] == "corrected" and result["memory"]["version"] == 2
        finally:
            await engine.dispose()

    asyncio.run(run())


class _PausingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.pause = False
        self.entered = None
        self.release = None

    async def embed(self, db, text):
        if self.pause:
            self.entered.set()
            await self.release.wait()
        return await super().embed(db, text)


def _postgres_harness(url):
    from app.infrastructure.database import MemoryCaptureDecision
    from app.services.memory_capture import MemoryCaptureError, confirm_memory, propose_memory
    from sqlalchemy import select

    async def open_store(texts):
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat = (str(uuid4()) for _ in range(2))
        turns = []
        async with sessions() as db:
            db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
            await db.commit()
            db.add(Conversation(id=chat, owner_id=owner))
            await db.commit()
            for text in texts:
                turn_id = str(uuid4())
                turns.append(turn_id)
                db.add(Turn(id=turn_id, conversation_id=chat, request_id=str(uuid4()), role="user", text=text))
            await db.commit()
        return engine, sessions, owner, chat, turns

    def proposal(owner, chat, turn, text):
        return propose_memory(
            text,
            owner_id=owner,
            conversation_id=chat,
            source_turn_id=turn,
            secret=SECRET,
        )

    async def decision(sessions, embedder, token, value, owner, chat):
        async with sessions() as db:
            try:
                return "ok", await confirm_memory(
                    db,
                    MemoryService(db, embedder),
                    token=token,
                    decision=value,
                    owner_id=owner,
                    conversation_id=chat,
                    source_turn_id=None,
                    secret=SECRET,
                )
            except MemoryCaptureError as exc:
                return "error", exc

    async def rows(sessions, owner):
        async with sessions() as db:
            memories = list(
                await db.scalars(select(Memory).where(Memory.owner_id == owner).order_by(Memory.created_at))
            )
            decisions = list(
                await db.scalars(
                    select(MemoryCaptureDecision)
                    .where(MemoryCaptureDecision.owner_id == owner)
                    .order_by(MemoryCaptureDecision.revision)
                )
            )
        return (
            [(row.content, row.version, row.status) for row in memories],
            [(row.revision, row.decision, row.outcome, row.target_id, row.target_version) for row in decisions],
        )

    return asyncio, open_store, proposal, decision, rows, MemoryCaptureError


def test_postgres_concurrent_confirms_never_leave_contradictory_active_rows():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")
    asyncio, open_store, proposal, decision, rows, MemoryCaptureError = _postgres_harness(url)

    async def run():
        engine, sessions, owner, chat, turns = await open_store(["I prefer tea", "I prefer coffee now"])
        embedder = FakeEmbedder()
        try:
            tea = proposal(owner, chat, turns[0], "I prefer tea")
            coffee = proposal(owner, chat, turns[1], "I prefer coffee now")
            outcomes = await asyncio.gather(
                decision(sessions, embedder, tea["token"], "save", owner, chat),
                decision(sessions, embedder, coffee["token"], "save", owner, chat),
            )
            successes = [value for kind, value in outcomes if kind == "ok"]
            conflicts = [value for kind, value in outcomes if kind == "error"]
            assert len(successes) == 1 and successes[0]["status"] == "saved"
            assert len(conflicts) == 1 and isinstance(conflicts[0], MemoryCaptureError)
            assert conflicts[0].code == "MEMORY_CLARIFICATION_REQUIRED"
            memories, decisions = await rows(sessions, owner)
            assert len([row for row in memories if row[2] == "active"]) == 1
            assert {(revision, outcome) for revision, _, outcome, _, _ in decisions} == {
                (1, "saved"),
                (2, "awaiting_confirmation"),
            }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_concurrent_save_and_discard_claim_one_revision(monkeypatch):
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")
    asyncio, open_store, proposal, decision, rows, MemoryCaptureError = _postgres_harness(url)

    async def run():
        from app.services import memory_capture as capture_service

        engine, sessions, owner, chat, turns = await open_store(["I prefer tea"])
        embedder = _PausingEmbedder()
        embedder.entered, embedder.release = asyncio.Event(), asyncio.Event()
        request = proposal(owner, chat, turns[0], "I prefer tea")
        try:
            embedder.pause = True
            saving = asyncio.create_task(decision(sessions, embedder, request["token"], "save", owner, chat))
            await embedder.entered.wait()
            kind, value = await decision(sessions, embedder, request["token"], "discard", owner, chat)
            assert kind == "error" and isinstance(value, MemoryCaptureError)
            assert value.code == "MEMORY_DECISION_IN_PROGRESS"
            embedder.release.set()
            kind, value = await saving
            assert kind == "ok" and value["status"] == "saved"
            memories, decisions = await rows(sessions, owner)
            assert memories == [("I prefer tea", 1, "active")]
            assert [(revision, choice, outcome) for revision, choice, outcome, _, _ in decisions] == [
                (1, "save", "saved")
            ]
        finally:
            await engine.dispose()

        engine, sessions, owner, chat, turns = await open_store(["I prefer coffee"])
        embedder = _PausingEmbedder()
        embedder.entered, embedder.release = asyncio.Event(), asyncio.Event()
        request = proposal(owner, chat, turns[0], "I prefer coffee")
        original = capture_service._claim_decision

        async def gated_claim(db, *, decision, **kwargs):
            if decision == "save":
                embedder.entered.set()
                await embedder.release.wait()
            return await original(db, decision=decision, **kwargs)

        monkeypatch.setattr(capture_service, "_claim_decision", gated_claim)
        try:
            saving = asyncio.create_task(decision(sessions, embedder, request["token"], "save", owner, chat))
            await embedder.entered.wait()
            kind, value = await decision(sessions, embedder, request["token"], "discard", owner, chat)
            assert kind == "ok" and value["status"] == "discarded"
            embedder.release.set()
            kind, value = await saving
            assert kind == "error" and isinstance(value, MemoryCaptureError)
            assert value.code == "DECISION_ALREADY_FINAL"
            memories, decisions = await rows(sessions, owner)
            assert memories == []
            assert [(revision, choice, outcome) for revision, choice, outcome, _, _ in decisions] == [
                (1, "discard", "discarded")
            ]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_concurrent_replace_and_discard_claim_one_clarification(monkeypatch):
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")
    asyncio, open_store, proposal, decision, rows, MemoryCaptureError = _postgres_harness(url)

    async def clarification(sessions, embedder, owner, chat, turns):
        first = proposal(owner, chat, turns[0], "I prefer tea")
        kind, value = await decision(sessions, embedder, first["token"], "save", owner, chat)
        assert kind == "ok" and value["status"] == "saved"
        second = proposal(owner, chat, turns[1], "I prefer coffee")
        kind, value = await decision(sessions, embedder, second["token"], "save", owner, chat)
        assert kind == "error" and isinstance(value, MemoryCaptureError)
        assert value.code == "MEMORY_CLARIFICATION_REQUIRED"
        return value.details

    async def run():
        from app.services import memory_capture as capture_service

        engine, sessions, owner, chat, turns = await open_store(["I prefer tea", "I prefer coffee"])
        embedder = _PausingEmbedder()
        embedder.entered, embedder.release = asyncio.Event(), asyncio.Event()
        try:
            detail = await clarification(sessions, embedder, owner, chat, turns)
            embedder.pause = True
            replacing = asyncio.create_task(decision(sessions, embedder, detail["token"], "replace", owner, chat))
            await embedder.entered.wait()
            kind, value = await decision(sessions, embedder, detail["token"], "discard", owner, chat)
            assert kind == "error" and isinstance(value, MemoryCaptureError)
            assert value.code == "MEMORY_DECISION_IN_PROGRESS"
            embedder.release.set()
            kind, value = await replacing
            assert kind == "ok" and value["status"] == "corrected"
            memories, decisions = await rows(sessions, owner)
            assert [(content, status) for content, _, status in memories] == [
                ("I prefer tea", "superseded"),
                ("I prefer coffee", "active"),
            ]
            assert [(revision, choice, outcome, target) for revision, choice, outcome, target, _ in decisions] == [
                (1, "save", "saved", None),
                (2, "replace", "corrected", detail["target_id"]),
            ]
        finally:
            await engine.dispose()

        engine, sessions, owner, chat, turns = await open_store(["I prefer tea", "I prefer coffee"])
        embedder = _PausingEmbedder()
        embedder.entered, embedder.release = asyncio.Event(), asyncio.Event()
        detail = await clarification(sessions, embedder, owner, chat, turns)
        original = capture_service._claim_decision

        async def gated_claim(db, *, decision, **kwargs):
            if decision == "replace":
                embedder.entered.set()
                await embedder.release.wait()
            return await original(db, decision=decision, **kwargs)

        monkeypatch.setattr(capture_service, "_claim_decision", gated_claim)
        try:
            replacing = asyncio.create_task(decision(sessions, embedder, detail["token"], "replace", owner, chat))
            await embedder.entered.wait()
            kind, value = await decision(sessions, embedder, detail["token"], "discard", owner, chat)
            assert kind == "ok" and value["status"] == "discarded"
            embedder.release.set()
            kind, value = await replacing
            assert kind == "error" and isinstance(value, MemoryCaptureError)
            assert value.code == "DECISION_ALREADY_FINAL"
            memories, decisions = await rows(sessions, owner)
            assert [(content, status) for content, _, status in memories] == [("I prefer tea", "active")]
            assert [(revision, choice, outcome) for revision, choice, outcome, _, _ in decisions] == [
                (1, "save", "saved"),
                (2, "discard", "discarded"),
            ]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_subject_lock_releases_at_transaction_boundary():
    """pg_advisory_xact_lock is held by one transaction and released by its commit."""
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")

    async def run():
        from app.services import memory_capture
        from sqlalchemy import text

        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = f"lock-{uuid4()}"
        try:
            async with (
                sessions() as holder,
                memory_capture._advisory_subject_lock(holder, owner, "location"),
                sessions() as probe,
            ):
                held = await probe.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"memcap:{owner}:location"},
                )
                assert held is False, "the locked transaction must block another worker"
                await probe.rollback()
            # The helper's transaction committed; the lock is gone, not leaked.
            async with sessions() as probe:
                free = await probe.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"memcap:{owner}:location"},
                )
                assert free is True, "a session-level lock would still be held here"
                await probe.rollback()
        finally:
            await engine.dispose()

    asyncio.run(run())
