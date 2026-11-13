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


def test_postgres_advisory_lock_serializes_same_subject_confirms():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_MEMORY_POSTGRES_URL requires disposable migrated pgvector PostgreSQL")

    async def run():
        from app.domain.context_operations import sign_proposal
        from app.domain.memory_capture import parse_capture
        from app.services.memory_capture import confirm_memory
        from sqlalchemy import select

        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, chat = (str(uuid4()) for _ in range(2))
        embedder = FakeEmbedder()
        secret = "capture-secret"
        try:
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add(Conversation(id=chat, owner_id=owner))
                await db.commit()
                tea_turn, coffee_turn = str(uuid4()), str(uuid4())
                for turn_id, text in ((tea_turn, "I prefer tea"), (coffee_turn, "I prefer coffee now")):
                    db.add(Turn(id=turn_id, conversation_id=chat, request_id=turn_id, role="user", text=text))
                await db.commit()

            def token(text, turn):
                capture = parse_capture(text)
                return sign_proposal(
                    {
                        "v": 1,
                        "kind": "memory_capture",
                        "owner_id": owner,
                        "conversation_id": chat,
                        "source_turn_id": turn,
                        "content": text,
                        "subject": capture.subject,
                        "value": capture.value,
                        "explicit": False,
                        "replaces": capture.replaces,
                        "negated": capture.negated,
                        "exp": 9999999999,
                    },
                    secret,
                )

            async def apply(value):
                async with sessions() as db:
                    try:
                        return await confirm_memory(
                            db,
                            MemoryService(db, embedder),
                            token=value,
                            decision="save",
                            owner_id=owner,
                            conversation_id=chat,
                            source_turn_id=None,
                            secret=secret,
                        )
                    except Exception as exc:
                        return {"status": type(exc).__name__}

            await asyncio.gather(
                apply(token("I prefer tea", tea_turn)), apply(token("I prefer coffee now", coffee_turn))
            )
            async with sessions() as db:
                active = list(
                    await db.scalars(select(Memory).where(Memory.owner_id == owner, Memory.status == "active"))
                )
            assert len(active) <= 1
        finally:
            await engine.dispose()

    asyncio.run(run())
