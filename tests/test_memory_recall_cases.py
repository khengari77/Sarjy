"""Fixture-driven recall and counted-forget cases: search ids, preview, yes and undo."""

import asyncio
import json
from pathlib import Path

import pytest
from app.domain.commands import CommandKind
from app.infrastructure.database import Memory
from app.services.memory_terms import all_visible_memories, search_all_memories
from app.services.voice_exclusions import hide_target, resolve_confirmation, undo_last_hide
from sqlalchemy import select
from test_memory_terms import database, row, seed

SECRET = "recall-cases-secret"
CASES = json.loads((Path(__file__).parent / "fixtures" / "memory_recall_cases.json").read_text())


async def statuses(db, memory_ids):
    rows = (await db.execute(select(Memory.id, Memory.status).where(Memory.id.in_(memory_ids)))).all()
    return {record_id: status for record_id, status in rows}


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_recall_case_preview_confirm_and_undo(case, tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / f"{case['name'][:20].replace(' ', '-')}.db")
        try:
            specs = [
                row(
                    memory["id"],
                    memory["content"],
                    owner_id=memory.get("owner_id", "alice"),
                    conversation_id=memory.get("conversation_id", "a1"),
                    status=memory.get("status", "active"),
                    scope=memory.get("scope", "global"),
                    offset=index,
                )
                for index, memory in enumerate(case["memories"])
            ]
            async with sessions() as db:
                await seed(db, specs)
                await db.commit()

            expected_ids = set(case["expected_memory_ids"])
            all_ids = [memory["id"] for memory in case["memories"]]
            original = {memory["id"]: memory.get("status", "active") for memory in case["memories"]}
            async with sessions() as db:
                found = (
                    await all_visible_memories(db, owner_id="alice", conversation_id="a1")
                    if case["all_memories"]
                    else await search_all_memories(db, owner_id="alice", conversation_id="a1", query=case["query"])
                )
                assert {hit.id for hit in found} == expected_ids

                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target=case["query"],
                    request_id="preview",
                    secret=SECRET,
                    all_memories=case["all_memories"],
                )
                if not expected_ids:
                    assert preview.kind is CommandKind.UNCHANGED
                    assert preview.detail == "MEMORY_TARGET_NOT_FOUND"
                    assert await statuses(db, all_ids) == original
                    return
                assert preview.kind is CommandKind.NEEDS_CONFIRMATION
                assert preview.detail == "MEMORY_HIDE_PROPOSED"
                assert f"I found {len(expected_ids)} " in preview.spoken
                assert await statuses(db, all_ids) == original

            async with sessions() as db:
                applied = await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert applied is not None and applied.kind is CommandKind.EXECUTED
                after = await statuses(db, all_ids)
                for memory_id in expected_ids:
                    assert after[memory_id] == "hidden"
                for memory_id in set(all_ids) - expected_ids:
                    assert after[memory_id] == original[memory_id]

            async with sessions() as db:
                undo = await undo_last_hide(db, owner_id="alice", conversation_id="a1", request_id="undo")
                assert undo.detail == "MEMORY_UNHIDDEN"
                after = await statuses(db, all_ids)
                for memory_id in expected_ids:
                    assert after[memory_id] == "active"
                for memory_id in set(all_ids) - expected_ids:
                    assert after[memory_id] == original[memory_id]
        finally:
            await engine.dispose()

    asyncio.run(run())
