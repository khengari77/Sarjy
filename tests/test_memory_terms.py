"""Uncapped lexical memory recall: tokenizer, SQL search, visibility, ranking and erasure."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.infrastructure.database import (
    Base,
    ContextExclusion,
    ContextOperation,
    Conversation,
    Identity,
    Memory,
    MemoryTerm,
)
from app.services.context_operations import keyword_search
from app.services.memory import MemoryService
from app.services.memory_terms import search_all_memories, terms_of
from app.services.memory_tools import memory_recall_tool
from app.services.memory_visibility import delete_memory
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_memory import FakeEmbedder
from test_memory import setup as memory_setup

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def stamp(offset: int = 0) -> str:
    return (BASE_TIME + timedelta(seconds=offset)).isoformat()


def row(
    memory_id: str,
    content: str,
    *,
    owner_id: str = "alice",
    conversation_id: str = "a1",
    status: str = "active",
    scope: str = "global",
    offset: int = 0,
) -> dict:
    return {
        "id": memory_id,
        "owner_id": owner_id,
        "content": content,
        "embedding_model": "text-embedding-3-small",
        "source_conversation_id": conversation_id,
        "version": 1,
        "status": status,
        "scope": scope,
        "created_at": stamp(offset),
        "updated_at": stamp(offset),
    }


async def database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all(
            [
                Identity(id="alice", token_hash="a", expires_at="2030"),
                Identity(id="bob", token_hash="b", expires_at="2030"),
            ]
        )
        await db.commit()
        db.add_all(
            [
                Conversation(id="a1", owner_id="alice"),
                Conversation(id="a2", owner_id="alice"),
                Conversation(id="b1", owner_id="bob"),
            ]
        )
        await db.commit()
    return engine, sessions


async def seed(db, specs: list[dict]) -> None:
    await db.execute(insert(Memory), specs)
    term_rows = [
        {"memory_id": spec["id"], "owner_id": spec["owner_id"], "term": term}
        for spec in specs
        for term in terms_of(spec["content"])
    ]
    if term_rows:
        await db.execute(insert(MemoryTerm), term_rows)


def test_terms_of_normalises_accents_case_arabic_diacritics_and_stopwords():
    assert terms_of("I prefer Café in Paris!") == frozenset({"prefer", "cafe", "paris"})
    assert terms_of("مَقْهَى") == frozenset({"مقهى"})
    assert terms_of("بَارِيس") == frozenset({"باريس"})
    assert terms_of("a I x 1234567890") == frozenset({"x", "1234567890"})
    assert len(next(iter(terms_of("x" * 100)))) == 64


def test_oldest_matches_are_found_beyond_the_old_preview_window(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "oldest.db")
        try:
            specs = [
                row("paris-1", "My first trip to Paris", offset=0),
                row("paris-2", "I proposed in Paris", offset=1),
            ]
            specs += [row(f"malta-{index:03d}", f"Malta note {index}", offset=10 + index) for index in range(598)]
            async with sessions() as db:
                await seed(db, specs)
                await db.commit()
            async with sessions() as db:
                hits = await search_all_memories(db, owner_id="alice", conversation_id="a1", query="Paris")
                assert {hit.id for hit in hits} == {"paris-1", "paris-2"}
                # The old preview search materialises only the newest 500 rows before matching, so it misses both.
                assert await keyword_search(db, owner_id="alice", conversation_id="a1", query="Paris", limit=10) == ()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_all_matching_memories_are_returned_without_a_five_or_ten_cap(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "all.db")
        try:
            async with sessions() as db:
                await seed(db, [row(f"m-{index:02d}", f"Paris note {index}", offset=index) for index in range(40)])
                await db.commit()
            async with sessions() as db:
                body = await memory_recall_tool(db, owner_id="alice", conversation_id="a1", query="Paris")
            assert len(body["memories"]) == 40
            assert body["total"] == 40 and body["truncated"] is False
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_recall_truncates_at_the_character_budget_but_reports_the_true_total(tmp_path, monkeypatch):
    from app.services import memory_tools

    monkeypatch.setattr(memory_tools, "RECALL_CHAR_BUDGET", 30)

    async def run():
        engine, sessions = await database(tmp_path / "budget.db")
        try:
            async with sessions() as db:
                await seed(db, [row(f"m-{index:02d}", f"Paris note {index}", offset=index) for index in range(40)])
                await db.commit()
            async with sessions() as db:
                body = await memory_recall_tool(db, owner_id="alice", conversation_id="a1", query="Paris")
            assert 0 < len(body["memories"]) < 40
            assert body["total"] == 40 and body["truncated"] is True
            assert sum(len(item["content"]) for item in body["memories"]) <= 30
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_ranking_counts_distinct_overlap_and_orders_by_score_then_recency(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "rank.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("one", "Paris", offset=30),
                        row("three", "Paris work trip", offset=10),
                        row("two", "Paris work work", offset=20),
                    ],
                )
                await db.commit()
            async with sessions() as db:
                hits = await search_all_memories(db, owner_id="alice", conversation_id="a1", query="Paris work trip")
            assert [hit.id for hit in hits] == ["three", "two", "one"]
            assert [hit.score for hit in hits] == [3.0, 2.0, 1.0]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_accented_and_arabic_memories_match_the_normalised_query(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "accents.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("cafe", "I like café au lait", offset=1),
                        row("arabic", "زرت مَقْهَى في باريس", offset=2),
                    ],
                )
                await db.commit()
            async with sessions() as db:
                assert [
                    hit.id
                    for hit in await search_all_memories(db, owner_id="alice", conversation_id="a1", query="cafe")
                ] == ["cafe"]
                assert [
                    hit.id
                    for hit in await search_all_memories(db, owner_id="alice", conversation_id="a1", query="مقهى")
                ] == ["arabic"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_search_respects_visibility_exclusions_and_hidden_or_deleted_status(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "visibility.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("global", "Paris global", offset=1),
                        row("temp-a", "Paris temporary", conversation_id="a1", scope="conversation", offset=2),
                        row("temp-b", "Paris elsewhere", conversation_id="a2", scope="conversation", offset=3),
                        row("hidden", "Paris hidden", status="hidden", offset=4),
                        row("deleted", "Paris deleted", status="deleted", offset=5),
                        row("excluded", "Paris excluded", offset=6),
                        row("foreign", "Paris foreign", owner_id="bob", conversation_id="b1", offset=7),
                    ],
                )
                db.add(
                    ContextOperation(
                        id="op-ex",
                        owner_id="alice",
                        conversation_id="a1",
                        command_type="exclude",
                        payload={},
                        idempotency_key="op-ex-key",
                        outcome="applied",
                        created_at=stamp(),
                    )
                )
                await db.flush()
                db.add(
                    ContextExclusion(
                        id="ex-excluded",
                        owner_id="alice",
                        conversation_id="a1",
                        memory_id="excluded",
                        operation_id="op-ex",
                        scope="conversation",
                        active=True,
                        created_at=stamp(),
                    )
                )
                await db.commit()
            async with sessions() as db:
                hits = await search_all_memories(db, owner_id="alice", conversation_id="a1", query="Paris")
            assert {hit.id for hit in hits} == {"global", "temp-a"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_terms_are_written_on_save_and_removed_on_permanent_delete(tmp_path):
    async def run():
        engine, sessions = await memory_setup(tmp_path / "terms-lifecycle.db")
        try:
            async with sessions() as db:
                service = MemoryService(db, FakeEmbedder())
                saved = await service.create_memory("alice", "a1", "source", "I prefer café in Paris", "k1")
            async with sessions() as db:
                stored = set((await db.scalars(select(MemoryTerm.term).where(MemoryTerm.memory_id == saved.id))).all())
                assert stored == terms_of("I prefer café in Paris")
                await delete_memory(db, "alice", saved.id)
                await db.commit()
            async with sessions() as db:
                left = (await db.scalars(select(MemoryTerm).where(MemoryTerm.memory_id == saved.id))).all()
                assert left == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_single_letter_is_a_search_term_only_when_it_is_the_whole_query(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "single.db")
        try:
            specs = [row("blood", "My blood type is B", offset=0), row("math", "I got a D in math", offset=1)]
            async with sessions() as db:
                await seed(db, specs)
                await db.commit()
            async with sessions() as db:
                whole = await search_all_memories(db, owner_id="alice", conversation_id="a1", query="B")
                assert [hit.id for hit in whole] == ["blood"]
                # Next to other words a lone "d" must not elect "I got a D in math".
                beside = await search_all_memories(db, owner_id="alice", conversation_id="a1", query="vitamin D")
                assert beside == ()
        finally:
            await engine.dispose()

    asyncio.run(run())
