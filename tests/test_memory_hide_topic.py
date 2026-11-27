"""Counted topic forget: preview, one yes, one batch hide, one undo."""

import asyncio
import json
import sqlite3
from contextlib import closing

import app.services.voice_exclusions as voice_exclusions
import pytest
from app.config import Settings
from app.domain.commands import CommandKind, CommandResult
from app.infrastructure.database import ContextOperation, Conversation, Memory
from app.main import create_app
from app.services.memory_terms import search_all_memories, terms_of
from app.services.memory_visibility import delete_memory
from app.services.voice_exclusions import (
    _apply_pending,
    _finish_pending,
    _hide_memories_in_chunks,
    _pending,
    hide_target,
    resolve_confirmation,
    undo_last_hide,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_memory_capture import set_mode
from test_memory_terms import database, row, seed

SECRET = "hide-topic-secret"


async def _statuses(db, memory_ids):
    rows = (await db.execute(select(Memory.id, Memory.status).where(Memory.id.in_(memory_ids)))).all()
    return {record_id: status for record_id, status in rows}


def test_three_paris_memories_preview_confirm_hide_and_undo(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "paris.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("p1", "I travelled to Paris in spring", offset=1),
                        row("p2", "I proposed in Paris", offset=2),
                        row("p3", "My favourite café is in Paris", offset=3),
                        row("other", "I like green tea", offset=4),
                    ],
                )
                await db.commit()

            async with sessions() as db:
                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )
                assert preview.kind is CommandKind.NEEDS_CONFIRMATION
                assert preview.detail == "MEMORY_HIDE_PROPOSED"
                assert preview.spoken == "I found 3 memories about Paris. Hide all of them?"
                assert await _statuses(db, ["p1", "p2", "p3", "other"]) == {
                    "p1": "active",
                    "p2": "active",
                    "p3": "active",
                    "other": "active",
                }

            async with sessions() as db:
                applied = await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert applied is not None and applied.kind is CommandKind.EXECUTED
                assert applied.spoken == "Hidden 3 memories about Paris. Say undo to bring them back."
                assert await _statuses(db, ["p1", "p2", "p3", "other"]) == {
                    "p1": "hidden",
                    "p2": "hidden",
                    "p3": "hidden",
                    "other": "active",
                }
                assert (await search_all_memories(db, owner_id="alice", conversation_id="a1", query="Paris")) == ()

            async with sessions() as db:
                undo = await undo_last_hide(db, owner_id="alice", conversation_id="a1", request_id="undo")
                assert undo.detail == "MEMORY_UNHIDDEN"
                assert undo.spoken == "Brought back 3 memories."
                assert await _statuses(db, ["p1", "p2", "p3", "other"]) == {
                    "p1": "active",
                    "p2": "active",
                    "p3": "active",
                    "other": "active",
                }
                assert len(await search_all_memories(db, owner_id="alice", conversation_id="a1", query="Paris")) == 3
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_other_owners_and_other_chats_temporary_memories_are_never_hidden(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "scope.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("global", "Paris global fact", offset=1),
                        row("temp-a", "Paris temporary here", conversation_id="a1", scope="conversation", offset=2),
                        row(
                            "temp-b", "Paris temporary elsewhere", conversation_id="a2", scope="conversation", offset=3
                        ),
                        row("foreign", "Paris foreign", owner_id="bob", conversation_id="b1", offset=4),
                    ],
                )
                await db.commit()

            async with sessions() as db:
                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )
                assert preview.spoken == "I found 2 memories about Paris. Hide all of them?"
                await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert await _statuses(db, ["global", "temp-a", "temp-b", "foreign"]) == {
                    "global": "hidden",
                    "temp-a": "hidden",
                    "temp-b": "active",
                    "foreign": "active",
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_confirm_hides_neither_another_owners_nor_another_chats_tampered_ids(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "tampered-confirm.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("p1", "I travelled to Paris", offset=1),
                        row("foreign", "Paris foreign", owner_id="bob", conversation_id="b1", offset=2),
                        row("temp-b", "Paris elsewhere", conversation_id="a2", scope="conversation", offset=3),
                    ],
                )
                await db.commit()

            async with sessions() as db:
                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )
                assert preview.detail == "MEMORY_HIDE_PROPOSED"
                pending = await _pending(db, owner_id="alice", conversation_id="a1")
                assert pending is not None
                payload = dict(pending.payload or {})
                payload["memory_ids"] = [*payload["memory_ids"], "foreign", "temp-b"]
                payload["count"] = len(payload["memory_ids"])
                pending.payload = payload
                await db.commit()

            async with sessions() as db:
                applied = await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert applied is not None and applied.detail == "MEMORY_HIDDEN"
                assert await _statuses(db, ["p1", "foreign", "temp-b"]) == {
                    "p1": "hidden",
                    "foreign": "active",
                    "temp-b": "active",
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_oldest_match_beyond_the_old_500_row_window_is_found_and_hidden(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "oldest.db")
        try:
            async with sessions() as db:
                specs = [row("paris-old", "My first trip to Paris", offset=0)]
                specs += [row(f"malta-{index:03d}", f"Malta note {index}", offset=10 + index) for index in range(600)]
                await seed(db, specs)
                await db.commit()

            async with sessions() as db:
                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )
                assert preview.spoken == "I found 1 memory about Paris. Hide it?"
                await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert await _statuses(db, ["paris-old"]) == {"paris-old": "hidden"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_confirm_takes_the_conversation_lock_before_the_owner_memory_lock(tmp_path, monkeypatch):
    async def run():
        engine, sessions = await database(tmp_path / "lock.db")
        try:
            async with sessions() as db:
                await seed(db, [row("p1", "I travelled to Paris", offset=1)])
                await db.commit()
            async with sessions() as db:
                await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )

            calls = []
            real_owned = voice_exclusions.owned_conversation

            async def owned_spy(db, owner_id, conversation_id, *, lock=False, include_deleted=False, key_share=False):
                calls.append(("conversation", lock, key_share))
                return await real_owned(
                    db, owner_id, conversation_id, lock=lock, include_deleted=include_deleted, key_share=key_share
                )

            async def owner_spy(db, owner_id):
                calls.append(("owner", owner_id))

            monkeypatch.setattr(voice_exclusions, "owned_conversation", owned_spy)
            monkeypatch.setattr(voice_exclusions, "lock_owner_memories", owner_spy)
            async with sessions() as db:
                applied = await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert applied is not None and applied.kind is CommandKind.EXECUTED
            assert calls == [("conversation", True, True), ("owner", "alice")]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_ten_thousand_ids_are_hidden_in_bind_safe_chunks(tmp_path, monkeypatch):
    async def run():
        engine, sessions = await database(tmp_path / "chunks.db")
        try:
            async with sessions() as db:
                await seed(db, [row("p1", "I travelled to Paris", offset=1)])
                await db.commit()
            monkeypatch.setattr(voice_exclusions, "HIDE_CHUNK_SIZE", 37)
            assert voice_exclusions.HIDE_CHUNK_SIZE == 37
            ids = ["p1"] + [f"missing-{index:05d}" for index in range(9_999)]
            async with sessions() as db:
                hidden = await _hide_memories_in_chunks(db, owner_id="alice", conversation_id="a1", memory_ids=ids)
                await db.commit()
                assert hidden == 1
                assert await _statuses(db, ["p1"]) == {"p1": "hidden"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_deleting_one_hidden_memory_scrubs_it_and_undo_still_restores_the_rest(tmp_path):
    erased_text = "Paris secret alpha"

    async def run():
        engine, sessions = await database(tmp_path / "leak.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("p1", "I travelled to Paris", offset=1),
                        row("p2", erased_text, offset=2),
                        row("p3", "I proposed in Paris", offset=3),
                    ],
                )
                await db.commit()
            async with sessions() as db:
                await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="Paris",
                    request_id="preview",
                    secret=SECRET,
                )
                await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
            async with sessions() as db:
                await delete_memory(db, "alice", "p2")
                await db.commit()
            async with sessions() as db:
                operation = await db.scalar(
                    select(ContextOperation).where(ContextOperation.command_type == "memory_hide")
                )
                assert operation is not None
                serialized = json.dumps(operation.payload, ensure_ascii=False)
                assert erased_text not in serialized
                assert "query" not in operation.payload
                assert set(operation.payload["memory_ids"]) == {"p1", "p3"}
                assert operation.payload["count"] == 3
                assert operation.payload["erased_count"] == 1
            async with sessions() as db:
                undo = await undo_last_hide(db, owner_id="alice", conversation_id="a1", request_id="undo")
                assert undo.spoken == "Brought back 2 of 3; one was deleted."
                assert await _statuses(db, ["p1", "p2", "p3"]) == {
                    "p1": "active",
                    "p2": "deleted",
                    "p3": "active",
                }
                assert erased_text not in json.dumps(undo.model_dump(mode="json"), ensure_ascii=False)
                assert set((await db.scalars(select(Memory.content))).all()) == {
                    "",
                    "I travelled to Paris",
                    "I proposed in Paris",
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_cross_chat_undo_leaves_the_hiding_chats_list_operation_undoable(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "cross-chat-undo.db")
        try:
            async with sessions() as db:
                await seed(
                    db,
                    [
                        row("temp-1", "First temporary note", conversation_id="a1", scope="conversation", offset=1),
                        row("temp-2", "Second temporary note", conversation_id="a1", scope="conversation", offset=2),
                    ],
                )
                await db.commit()
            async with sessions() as db:
                preview = await hide_target(
                    db,
                    owner_id="alice",
                    conversation_id="a1",
                    target="",
                    request_id="preview",
                    secret=SECRET,
                    scope="chat",
                )
                assert preview.detail == "MEMORY_HIDE_PROPOSED"
                await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
            async with sessions() as db:
                other_chat = await undo_last_hide(db, owner_id="alice", conversation_id="a2", request_id="undo-b")
                assert other_chat.detail == "NO_HIDDEN_MEMORY"
                assert await _statuses(db, ["temp-1", "temp-2"]) == {"temp-1": "hidden", "temp-2": "hidden"}
                operation = await db.scalar(
                    select(ContextOperation).where(ContextOperation.command_type == "memory_hide")
                )
                assert operation.outcome == "applied"
            async with sessions() as db:
                restored = await undo_last_hide(db, owner_id="alice", conversation_id="a1", request_id="undo-a")
                assert restored.detail == "MEMORY_UNHIDDEN"
                assert await _statuses(db, ["temp-1", "temp-2"]) == {"temp-1": "active", "temp-2": "active"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_finish_pending_never_overwrites_an_already_resolved_row(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "conditional-finish.db")
        try:
            async with sessions() as db:
                await seed(db, [row("p1", "I travelled to Paris", offset=1)])
                await db.commit()
            async with sessions() as db:
                await hide_target(
                    db, owner_id="alice", conversation_id="a1", target="Paris", request_id="preview", secret=SECRET
                )
            async with sessions() as db:
                stale = await _pending(db, owner_id="alice", conversation_id="a1")
            assert stale is not None
            first = CommandResult(
                request_id="yes-one", kind=CommandKind.EXECUTED, spoken="first", detail="MEMORY_HIDDEN"
            )
            second = CommandResult(
                request_id="yes-two", kind=CommandKind.EXECUTED, spoken="second", detail="MEMORY_HIDDEN"
            )
            async with sessions() as db:
                current = await _pending(db, owner_id="alice", conversation_id="a1")
                assert current is not None
                assert await _finish_pending(db, current, "applied", first, "yes-one") is True
            async with sessions() as db:
                assert await _finish_pending(db, stale, "applied", second, "yes-two") is False
                stored = await db.scalar(select(ContextOperation).where(ContextOperation.command_type == "memory_hide"))
                assert stored.payload["response"]["request_id"] == "yes-one"
                assert stored.payload["response"]["spoken"] == "first"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_fully_erased_pending_hide_is_deleted_and_its_request_id_can_be_reused(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "erased-retry.db")
        try:
            async with sessions() as db:
                await seed(db, [row("p1", "I travelled to Paris", offset=1), row("p2", "I live in London", offset=2)])
                await db.commit()
            async with sessions() as db:
                preview = await hide_target(
                    db, owner_id="alice", conversation_id="a1", target="Paris", request_id="preview", secret=SECRET
                )
                assert preview.detail == "MEMORY_HIDE_PROPOSED"
            async with sessions() as db:
                await delete_memory(db, "alice", "p1")
                await db.commit()
            async with sessions() as db:
                operation = await db.scalar(
                    select(ContextOperation).where(ContextOperation.command_type == "memory_hide")
                )
                assert operation.outcome == "deleted"
                assert operation.payload.get("response") is None
                replay = await hide_target(
                    db, owner_id="alice", conversation_id="a1", target="London", request_id="preview", secret=SECRET
                )
                assert replay.detail == "MEMORY_HIDE_PROPOSED"
                assert replay.spoken == "I found 1 memory about London. Hide it?"
                applied = await resolve_confirmation(
                    db, owner_id="alice", conversation_id="a1", confirmed=True, request_id="confirm", secret=SECRET
                )
                assert applied.detail == "MEMORY_HIDDEN"
                assert await _statuses(db, ["p2"]) == {"p2": "hidden"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def configured(tmp_path, name):
    file = tmp_path / f"{name}.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret=SECRET,
        _env_file=None,
    )
    return file, settings


def seed_sql(file, chat, rows, *, scope="global"):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        for record_id, content in rows:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,scope,embedding_model,source_conversation_id,version,"
                "status,created_at,updated_at) VALUES (?,?,?,?,'text-embedding-3-small',?,1,'active',"
                "'2026-11-12T00:00:00+00:00','2026-11-12T00:00:00+00:00')",
                (record_id, owner, content, scope, chat),
            )
            for term in terms_of(content):
                db.execute(
                    "INSERT INTO memory_terms (memory_id, owner_id, term) VALUES (?, ?, ?)",
                    (record_id, owner, term),
                )
        db.commit()


def voice(client, conversation, text, request):
    return client.post(
        "/v1/actions",
        json={
            "conversation_id": conversation,
            "request_id": request,
            "expected_revision": 0,
            "source": "transcript",
            "transcript": text,
        },
    ).json()["result"]


def preview_forget(file, chat, topic, request_id, *, scope="topic"):
    """Create the hide preview through the service the model tool calls (no voice grammar)."""

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                owner = await db.scalar(select(Conversation.owner_id).where(Conversation.id == chat))
                return await hide_target(
                    db,
                    owner_id=owner,
                    conversation_id=chat,
                    target=topic,
                    request_id=request_id,
                    secret=SECRET,
                    scope=scope,
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def memory_status(file, memory_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()[0]


@pytest.mark.parametrize("mode", ["auto", "ask", "off"])
def test_one_yes_hides_in_every_memory_mode(tmp_path, mode):
    file, settings = configured(tmp_path, f"mode-{mode}")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("p1", "I travelled to Paris")])
        set_mode(file, owner, mode)

        preview = preview_forget(file, chat, "Paris", "preview")
        assert preview.kind is CommandKind.NEEDS_CONFIRMATION
        assert preview.detail == "MEMORY_HIDE_PROPOSED"
        assert preview.spoken == "I found 1 memory about Paris. Hide it?"
        assert memory_status(file, "p1") == "active"

        applied = voice(client, chat, "Yes.", "confirm")
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert applied["spoken"] == "Hidden 1 memory about Paris. Say undo to bring it back."
        assert memory_status(file, "p1") == "hidden"


def test_replaying_the_same_yes_does_not_hide_twice(tmp_path):
    file, settings = configured(tmp_path, "replay")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("p1", "I travelled to Paris"), ("p2", "I proposed in Paris")])

        assert preview_forget(file, chat, "Paris", "preview").detail == "MEMORY_HIDE_PROPOSED"
        first = voice(client, chat, "Yes.", "same-confirm")
        second = voice(client, chat, "Yes.", "same-confirm")
        assert first == second
        assert first["spoken"] == "Hidden 2 memories about Paris. Say undo to bring them back."
        assert memory_status(file, "p1") == memory_status(file, "p2") == "hidden"


def test_a_memory_deleted_between_preview_and_yes_is_skipped_and_scrubbed(tmp_path):
    erased_text = "Paris secret alpha"
    file, settings = configured(tmp_path, "deleted")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("p1", "I travelled to Paris"), ("p2", erased_text)])
        preview = preview_forget(file, chat, "Paris", "preview")
        assert preview.spoken == "I found 2 memories about Paris. Hide all of them?"

        async def stale_pending_delete_and_apply():
            engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    owner = await db.scalar(select(Conversation.owner_id).where(Conversation.id == chat))
                    stale = await _pending(db, owner_id=owner, conversation_id=chat)
                assert stale is not None
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    await delete_memory(db, owner, "p2")
                    await db.commit()
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    applied = await _apply_pending(
                        db,
                        pending=stale,
                        owner_id=owner,
                        conversation_id=chat,
                        request_id="confirm",
                        secret=SECRET,
                    )
                    assert applied.spoken == "Hidden 1 of 2; one had already changed."
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    operation = await db.scalar(
                        select(ContextOperation).where(ContextOperation.command_type == "memory_hide")
                    )
                    assert operation is not None
                    serialized = json.dumps(operation.payload, ensure_ascii=False)
                    assert erased_text not in serialized
                    assert "p2" not in serialized
            finally:
                await engine.dispose()

        asyncio.run(stale_pending_delete_and_apply())
        assert memory_status(file, "p1") == "hidden"
        assert memory_status(file, "p2") == "deleted"


def _seed_other_chat_temporary(file, owner, other_chat, memory_id, content):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO memories (id,owner_id,content,scope,embedding_model,source_conversation_id,version,"
            "status,created_at,updated_at) VALUES (?,?,?,'conversation','m',?,1,'active',"
            "'2026-11-12T00:00:00+00:00','2026-11-12T00:00:00+00:00')",
            (memory_id, owner, content, other_chat),
        )
        for term in terms_of(content):
            db.execute(
                "INSERT INTO memory_terms (memory_id, owner_id, term) VALUES (?, ?, ?)",
                (memory_id, owner, term),
            )
        db.commit()


def test_chat_scope_clears_only_this_chats_temporary_memories_and_undo_restores_them(tmp_path):
    file, settings = configured(tmp_path, "chat-scope")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        other = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("g1", "I live in Tripoli")])
        seed_sql(
            file,
            chat,
            [("temp-1", "A temporary note here"), ("temp-2", "Another temporary note")],
            scope="conversation",
        )
        _seed_other_chat_temporary(file, owner, other, "temp-other", "Another chat's secret")

        preview = preview_forget(file, chat, "", "preview", scope="chat")
        assert preview.kind is CommandKind.NEEDS_CONFIRMATION
        assert preview.detail == "MEMORY_HIDE_PROPOSED"
        assert preview.spoken == "I found 2 temporary memories from this chat. Clear them?"
        assert memory_status(file, "temp-1") == memory_status(file, "temp-2") == "active"

        applied = voice(client, chat, "Yes.", "confirm")
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert applied["spoken"] == "Cleared 2 temporary memories from this chat. Say undo to bring them back."
        assert memory_status(file, "temp-1") == memory_status(file, "temp-2") == "hidden"
        assert memory_status(file, "g1") == memory_status(file, "temp-other") == "active"

        undo = voice(client, chat, "Undo", "undo")
        assert undo["detail"] == "MEMORY_UNHIDDEN"
        assert memory_status(file, "temp-1") == memory_status(file, "temp-2") == "active"
        assert memory_status(file, "g1") == memory_status(file, "temp-other") == "active"


@pytest.mark.parametrize("mode", ["auto", "ask", "off"])
def test_chat_scope_takes_one_yes_in_every_memory_mode(tmp_path, mode):
    file, settings = configured(tmp_path, f"chat-mode-{mode}")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(
            file, chat, [("temp-1", "First temporary note"), ("temp-2", "Second temporary note")], scope="conversation"
        )
        set_mode(file, owner, mode)

        preview = preview_forget(file, chat, "", "preview", scope="chat")
        assert preview.detail == "MEMORY_HIDE_PROPOSED"
        assert preview.spoken == "I found 2 temporary memories from this chat. Clear them?"
        assert memory_status(file, "temp-1") == memory_status(file, "temp-2") == "active"

        applied = voice(client, chat, "Yes.", "confirm")
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert memory_status(file, "temp-1") == memory_status(file, "temp-2") == "hidden"


def test_chat_scope_zero_is_honest_and_writes_nothing(tmp_path):
    file, settings = configured(tmp_path, "chat-zero")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("g1", "I live in Tripoli")])

        result = preview_forget(file, chat, "", "zero", scope="chat")
        assert result.kind is CommandKind.UNCHANGED
        assert result.detail == "MEMORY_TARGET_NOT_FOUND"
        assert result.spoken == "There are no temporary memories in this chat; nothing was changed."
        assert memory_status(file, "g1") == "active"
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT count(*) FROM context_operations").fetchone()[0] == 0


def test_chat_scope_keeps_a_budget_note_out_of_the_clear(tmp_path):
    file, settings = configured(tmp_path, "chat-budget")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(
            file,
            chat,
            [("budget", "Budget 500 USD for this trip"), ("temp-1", "A temporary note here")],
            scope="conversation",
        )

        preview = preview_forget(file, chat, "", "preview", scope="chat")
        assert preview.kind is CommandKind.NEEDS_CONFIRMATION
        assert preview.detail == "MEMORY_HIDE_PROPOSED"
        assert preview.spoken == (
            "I found 1 temporary memory from this chat; I'll keep 1 budget note unless you name it. Clear it?"
        )
        assert memory_status(file, "budget") == memory_status(file, "temp-1") == "active"

        applied = voice(client, chat, "Yes.", "confirm")
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert applied["spoken"] == "Cleared 1 temporary memory from this chat. Say undo to bring it back."
        assert memory_status(file, "temp-1") == "hidden"
        assert memory_status(file, "budget") == "active"


def test_double_yes_keeps_the_first_response(tmp_path):
    file, settings = configured(tmp_path, "double-yes")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("p1", "I travelled to Paris"), ("p2", "I proposed in Paris")])
        preview_forget(file, chat, "Paris", "preview")

        async def overlapping_yes():
            engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    owner = await db.scalar(select(Conversation.owner_id).where(Conversation.id == chat))
                    stale = await _pending(db, owner_id=owner, conversation_id=chat)
                assert stale is not None
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    first = await _apply_pending(
                        db,
                        pending=stale,
                        owner_id=owner,
                        conversation_id=chat,
                        request_id="yes-one",
                        secret=SECRET,
                    )
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    second = await _apply_pending(
                        db,
                        pending=stale,
                        owner_id=owner,
                        conversation_id=chat,
                        request_id="yes-two",
                        secret=SECRET,
                    )
                assert second == first
            finally:
                await engine.dispose()

        asyncio.run(overlapping_yes())
        assert memory_status(file, "p1") == memory_status(file, "p2") == "hidden"
