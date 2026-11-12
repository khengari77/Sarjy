"""Durable request identity protects both provenance and paid delivery."""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.infrastructure.database import (
    ContextExclusion,
    ContextOperation,
    DevelopmentBudget,
    Memory,
    MemoryWriteRequest,
    Turn,
)
from app.services.memory import MemoryService, OpenAIEmbedder, _fingerprint
from app.services.memory_eligibility import blocked_memory_ids, blocked_source_turn_ids
from test_memory import FakeEmbedder, setup


async def reserved(sessions):
    async with sessions() as db:
        return await db.scalar(select(DevelopmentBudget.reserved_units))


@pytest.mark.parametrize("source_change", [False, True])
def test_concurrent_claim_sends_once_and_rejects_changed_provenance(tmp_path, source_change):
    async def run():
        engine, sessions = await setup(tmp_path / "claims.db")
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def transport(request):
            calls.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json={"data": [{"embedding": [1.0] + [0.0] * 1535}]})

        adapter = OpenAIEmbedder("test-only", transport=httpx.MockTransport(transport))

        async def write(source):
            async with sessions() as db:
                return await MemoryService(db, adapter).create_memory("alice", "a1", source, "Fact", "once")

        try:
            async with sessions() as db:
                db.add(
                    Turn(
                        id="source2",
                        conversation_id="a1",
                        request_id="s2",
                        role="user",
                        text="Other source",
                        status="completed",
                    )
                )
                await db.commit()
            first = asyncio.create_task(write("source"))
            await asyncio.wait_for(started.wait(), 2)
            with pytest.raises(HTTPException) as failure:
                await write("source2" if source_change else "source")
            assert failure.value.status_code == 409
            assert failure.value.detail["code"] == (
                "IDEMPOTENCY_CONFLICT" if source_change else "MEMORY_REQUEST_IN_PROGRESS"
            )
            assert len(calls) == 1 and await reserved(sessions) == 20000
            # A transaction unrelated to this claim remains usable while HTTP is paused.
            async with sessions() as db:
                db.add(
                    ContextOperation(
                        id="unrelated",
                        owner_id="alice",
                        conversation_id="a2",
                        command_type="fixture",
                        payload={},
                        idempotency_key="other",
                        outcome="completed",
                    )
                )
                await asyncio.wait_for(db.commit(), 2)
            release.set()
            result = await first
            assert await write("source") == result
            assert len(calls) == 1 and await reserved(sessions) == 20000
            async with sessions() as db:
                assert len(list(await db.scalars(select(Memory)))) == 1
        finally:
            release.set()
            await engine.dispose()

    asyncio.run(run())


def test_uncertain_transport_never_reinfers_or_refunds(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "unknown.db")
        calls = []

        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("unknown delivery")

        adapter = OpenAIEmbedder("test-only", transport=httpx.MockTransport(timeout))
        try:
            for _ in range(2):
                async with sessions() as db:
                    with pytest.raises(HTTPException) as error:
                        await MemoryService(db, adapter).create_memory("alice", "a1", None, "Fact", "unknown")
                    assert error.value.status_code == 503
            assert len(calls) == 1 and await reserved(sessions) == 20000
            async with sessions() as db:
                assert (await db.scalar(select(MemoryWriteRequest))).state == "unknown"
                assert not list(await db.scalars(select(Memory)))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_embedded_recovery_and_abandoned_claim_do_not_reserve_again(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "recover.db")
        fake = FakeEmbedder()
        try:
            async with sessions() as db:
                service = MemoryService(db, fake)
                fingerprint = _fingerprint("a1", None, "Fact", None, None)
                claim, winner = await service._claim("alice", "a1", None, "Fact", "recover", None, None, fingerprint)
                assert winner
            async with sessions() as db:
                await db.execute(
                    update(MemoryWriteRequest)
                    .where(MemoryWriteRequest.id == claim["id"])
                    .values(state="embedded", embedding=[1.0] + [0.0] * 1535)
                )
                await db.commit()
            async with sessions() as db:
                result = await MemoryService(db, fake).create_memory("alice", "a1", None, "Fact", "recover")
                assert result.content == "Fact"
            assert fake.calls == [] and await reserved(sessions) == 20000
            async with sessions() as db:
                service = MemoryService(db, fake)
                fingerprint = _fingerprint("a1", None, "Other", None, None)
                abandoned, _ = await service._claim("alice", "a1", None, "Other", "abandoned", None, None, fingerprint)
            async with sessions() as db:
                await db.execute(
                    update(MemoryWriteRequest)
                    .where(MemoryWriteRequest.id == abandoned["id"])
                    .values(created_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
                )
                await db.commit()
            async with sessions() as db:
                with pytest.raises(HTTPException) as error:
                    await MemoryService(db, fake).create_memory("alice", "a1", None, "Other", "abandoned")
                assert error.value.status_code == 503 and error.value.detail["code"] == "MEMORY_REQUEST_UNKNOWN"
            assert fake.calls == [] and await reserved(sessions) == 40000
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_legacy_request_key_backfills_completed_claim_without_embedding(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "legacy.db")
        fake = FakeEmbedder()
        try:
            async with sessions() as db:
                row = Memory(
                    id="legacy",
                    owner_id="alice",
                    content="Fact",
                    embedding=[1.0] + [0.0] * 1535,
                    embedding_model="text-embedding-3-small",
                    source_conversation_id="a1",
                    source_turn_id="source",
                    version=1,
                    status="active",
                    request_key="old",
                )
                db.add(row)
                await db.commit()
            async with sessions() as db:
                result = await MemoryService(db, fake).create_memory("alice", "a1", "source", "Fact", "old")
                assert result.id == "legacy"
            assert fake.calls == [] and await reserved(sessions) is None
            async with sessions() as db:
                assert (await db.scalar(select(MemoryWriteRequest))).state == "completed"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_budget_denial_and_invalid_provider_response_cannot_create_memory(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "denial.db")
        calls = []

        def invalid(request):
            calls.append(request)
            return httpx.Response(200, json={"data": []})

        adapter = OpenAIEmbedder("test-only", transport=httpx.MockTransport(invalid))
        try:
            for _ in range(2):
                async with sessions() as db:
                    with pytest.raises(HTTPException) as error:
                        await MemoryService(db, adapter).create_memory("alice", "a1", None, "Fact", "invalid")
                    assert error.value.status_code == 503
            assert len(calls) == 1 and await reserved(sessions) == 20000
            async with sessions() as db:
                row = await db.scalar(select(DevelopmentBudget))
                row.reserved_units = 1000000
                await db.commit()
            async with sessions() as db:
                with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
                    await MemoryService(db, adapter).create_memory("alice", "a1", None, "New", "denied")
            assert len(calls) == 1
            async with sessions() as db:
                assert not list(await db.scalars(select(Memory)))
                assert len(list(await db.scalars(select(MemoryWriteRequest)))) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_corrections_preserve_scoped_exclusion_lineage_and_sources_until_undo(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "lineage.db")
        fake = FakeEmbedder()
        try:
            async with sessions() as db:
                service = MemoryService(db, fake)
                first = await service.create_memory("alice", "a1", "source", "Tripoli", "first")
                db.add(
                    ContextOperation(
                        id="exclude",
                        owner_id="alice",
                        conversation_id="a2",
                        command_type="exclude",
                        payload={},
                        idempotency_key="exclude",
                        outcome="applied",
                    )
                )
                await db.commit()
                db.add(
                    ContextExclusion(
                        id="ex",
                        owner_id="alice",
                        conversation_id="a2",
                        memory_id=first.id,
                        operation_id="exclude",
                        active=True,
                    )
                )
                await db.commit()
                second = await service.correct_memory("alice", first.id, 1, "Tripoli, Libya", "second")
                third = await service.correct_memory("alice", second.id, 2, "Tripoli in Libya", "third")
                assert await blocked_memory_ids(db, "alice", "a2") == frozenset({first.id, second.id, third.id})
                assert await blocked_source_turn_ids(db, "alice", "a2") == frozenset({"source"})
                assert await service.search_memories("alice", "a2", "city") == ()
                assert (await service.search_memories("alice", "a1", "city"))[0].id == third.id
                await db.execute(update(ContextExclusion).where(ContextExclusion.id == "ex").values(active=False))
                await db.commit()
                assert (await service.search_memories("alice", "a2", "city"))[0].id == third.id
        finally:
            await engine.dispose()

    asyncio.run(run())
