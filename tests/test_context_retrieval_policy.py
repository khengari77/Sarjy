"""Keyword retrieval and operation history share the production exclusion policy."""

import asyncio

from app.domain.context_operations import MemoryCandidate
from app.infrastructure.database import ContextExclusion, ContextOperation, Memory, now
from app.services.context_operations import create_proposal, keyword_search, recent_memories
from app.services.memory_tools import memory_recall_tool
from test_memory import setup


def test_keyword_and_recent_recall_see_global_and_only_the_calling_conversation(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "scope-recall.db")
        try:
            async with sessions() as db:
                db.add_all(
                    [
                        Memory(
                            id="global",
                            owner_id="alice",
                            content="Trip global",
                            source_conversation_id="a1",
                            version=1,
                            status="active",
                            scope="global",
                        ),
                        Memory(
                            id="temp-a",
                            owner_id="alice",
                            content="Trip in a",
                            source_conversation_id="a1",
                            version=1,
                            status="active",
                            scope="conversation",
                        ),
                        Memory(
                            id="temp-b",
                            owner_id="alice",
                            content="Trip in b",
                            source_conversation_id="a2",
                            version=1,
                            status="active",
                            scope="conversation",
                        ),
                    ]
                )
                await db.commit()
            for chat, visible in (("a1", {"global", "temp-a"}), ("a2", {"global", "temp-b"})):
                async with sessions() as db:
                    hits = await keyword_search(db, owner_id="alice", conversation_id=chat, query="trip", limit=10)
                    assert {hit.id for hit in hits} == visible
                    recent = await recent_memories(db, owner_id="alice", conversation_id=chat, limit=10)
                    assert {item.id for item in recent} == visible
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_preview_reread_and_recall_recheck_hide_another_conversations_temporary_memory(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "scope-defensive.db")
        try:
            async with sessions() as db:
                db.add_all(
                    [
                        Memory(
                            id="temp-a",
                            owner_id="alice",
                            content="Temporary A",
                            source_conversation_id="a1",
                            version=1,
                            status="active",
                            scope="conversation",
                        ),
                        Memory(
                            id="temp-b",
                            owner_id="alice",
                            content="Temporary B",
                            source_conversation_id="a2",
                            version=1,
                            status="active",
                            scope="conversation",
                        ),
                    ]
                )
                await db.commit()

            async def injected(db, *, owner_id, conversation_id, query, limit):
                return (
                    MemoryCandidate(id="temp-a", content="Temporary A", score=1.0, kind="memory"),
                    MemoryCandidate(id="temp-b", content="Temporary B", score=1.0, kind="memory"),
                )

            async with sessions() as db:
                body = await create_proposal(
                    db, owner_id="alice", conversation_id="a1", query="temporary", secret="secret", search=injected
                )
                assert {item["id"] for item in body["matches"]} == {"temp-a"}

                recall = await memory_recall_tool(
                    db, owner_id="alice", conversation_id="a1", query="temporary", search=injected
                )
                assert recall["memories"] == [{"content": "Temporary A"}]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_keyword_search_excludes_corrected_descendants(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "lineage.db")
        try:
            async with sessions() as db:
                old = Memory(
                    id="old",
                    owner_id="alice",
                    content="Trip to Tripoli",
                    source_conversation_id="a1",
                    version=1,
                    status="active",
                )
                db.add(old)
                await db.flush()
                new = Memory(
                    id="new",
                    owner_id="alice",
                    content="Trip to London",
                    source_conversation_id="a1",
                    version=2,
                    status="active",
                    supersedes_id="old",
                )
                db.add(new)
                db.add(
                    ContextOperation(
                        id="op",
                        owner_id="alice",
                        conversation_id="a1",
                        command_type="exclude",
                        idempotency_key="op-key",
                        outcome="applied",
                        payload={},
                        created_at=now(),
                    )
                )
                await db.flush()
                db.add(
                    ContextExclusion(
                        id="ex",
                        owner_id="alice",
                        conversation_id="a1",
                        memory_id="old",
                        operation_id="op",
                        active=True,
                        created_at=now(),
                    )
                )
                await db.commit()
            async with sessions() as db:
                hits = await keyword_search(db, owner_id="alice", conversation_id="a1", query="trip", limit=10)
                assert hits == ()
        finally:
            await engine.dispose()

    asyncio.run(run())
