"""Counted topic forget: preview, one yes, one batch hide, one undo."""

import asyncio
import json
import sqlite3
from contextlib import closing

import app.services.voice_exclusions as voice_exclusions
import pytest
from app.config import Settings
from app.domain.commands import CommandKind, MemoryForgetRequest, parse_memory_forget
from app.infrastructure.database import ContextOperation, Memory
from app.main import create_app
from app.services.memory_terms import search_all_memories, terms_of
from app.services.memory_visibility import delete_memory
from app.services.voice_exclusions import (
    HIDE_CHUNK_SIZE,
    _hide_memories_in_chunks,
    hide_target,
    resolve_confirmation,
    undo_last_hide,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
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
                calls.append(("conversation", lock))
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
            assert calls == [("conversation", True), ("owner", "alice")]
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
            assert HIDE_CHUNK_SIZE == 500
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

        preview = voice(client, chat, "Forget everything about Paris.", "preview")
        assert preview["kind"] == "needs_confirmation"
        assert preview["detail"] == "MEMORY_HIDE_PROPOSED"
        assert preview["spoken"] == "I found 1 memory about Paris. Hide it?"
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

        assert voice(client, chat, "Forget Paris.", "preview")["detail"] == "MEMORY_HIDE_PROPOSED"
        first = voice(client, chat, "Yes.", "same-confirm")
        second = voice(client, chat, "Yes.", "same-confirm")
        assert first == second
        assert first["spoken"] == "Hidden 2 memories about Paris. Say undo to bring them back."
        assert memory_status(file, "p1") == memory_status(file, "p2") == "hidden"


def test_a_memory_deleted_between_preview_and_yes_is_skipped_and_reported(tmp_path):
    file, settings = configured(tmp_path, "deleted")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("p1", "I travelled to Paris"), ("p2", "I proposed in Paris")])

        preview = voice(client, chat, "Forget Paris.", "preview")
        assert preview["spoken"] == "I found 2 memories about Paris. Hide all of them?"
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE memories SET status='deleted', content='' WHERE id='p2'")
            db.commit()

        applied = voice(client, chat, "Yes.", "confirm")
        assert applied["spoken"] == "Hidden 1 of 2; one had already changed."
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

        preview = voice(client, chat, "Forget everything I just said.", "preview")
        assert preview["kind"] == "needs_confirmation"
        assert preview["detail"] == "MEMORY_HIDE_PROPOSED"
        assert preview["spoken"] == "I found 2 temporary memories from this chat. Clear them?"
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

        preview = voice(client, chat, "Clear this chat's memory.", "preview")
        assert preview["detail"] == "MEMORY_HIDE_PROPOSED"
        assert preview["spoken"] == "I found 2 temporary memories from this chat. Clear them?"
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

        result = voice(client, chat, "Forget the temporary memories.", "zero")
        assert result["kind"] == "unchanged"
        assert result["detail"] == "MEMORY_TARGET_NOT_FOUND"
        assert result["spoken"] == "There are no temporary memories in this chat; nothing was changed."
        assert memory_status(file, "g1") == "active"
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT count(*) FROM context_operations").fetchone()[0] == 0


ALL_MEMORY_PHRASES = [
    "Forget everything.",
    "Forget all my memories.",
    "Forget everything about me.",
    "Forget everything about me everywhere.",
    "Forget everything about me globally.",
    "Forget everything about me in all chats.",
    "Forget everything you know about me.",
]


@pytest.mark.parametrize("phrase", ALL_MEMORY_PHRASES)
def test_every_all_memory_phrase_refuses_and_writes_nothing(tmp_path, phrase):
    file, settings = configured(tmp_path, "all-refusal")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed_sql(file, chat, [("g1", "I live in Tripoli")])

        result = voice(client, chat, phrase, "refuse")
        assert result["kind"] == "unchanged"
        assert result["detail"] == "MEMORY_GLOBAL_RETRACTION_UNAVAILABLE"
        assert result["spoken"] == (
            "I can't clear all your memories by voice. You can review and delete them in Settings, under Memory. "
            "Nothing was changed."
        )
        assert result["changed"] is False
        assert memory_status(file, "g1") == "active"
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT count(*) FROM context_operations").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Forget my name.", MemoryForgetRequest("my name")),
        ("Exclude Paris.", MemoryForgetRequest("Paris")),
        ("Please forget the Paris trip.", MemoryForgetRequest("the Paris trip")),
        ("forget everything about Paris", MemoryForgetRequest("Paris")),
        ("forget the Paris trip everywhere", MemoryForgetRequest("the Paris trip")),
        ("forget everything I just said", MemoryForgetRequest("", this_chat=True)),
        ("forget what I said", MemoryForgetRequest("", this_chat=True)),
        ("forget what we just talked about", MemoryForgetRequest("", this_chat=True)),
        ("forget everything from this chat", MemoryForgetRequest("", this_chat=True)),
        ("forget everything from this conversation", MemoryForgetRequest("", this_chat=True)),
        ("clear this chat's memory", MemoryForgetRequest("", this_chat=True)),
        ("forget the temporary memories", MemoryForgetRequest("", this_chat=True)),
        ("forget temporary memories in this chat", MemoryForgetRequest("", this_chat=True)),
        ("forget everything", MemoryForgetRequest("", all_memories=True)),
        ("forget all my memories", MemoryForgetRequest("", all_memories=True)),
        ("forget all of my memories globally", MemoryForgetRequest("", all_memories=True)),
        ("forget everything about me", MemoryForgetRequest("", all_memories=True)),
        ("forget everything about me everywhere", MemoryForgetRequest("", all_memories=True)),
        ("forget everything about me in all chats", MemoryForgetRequest("", all_memories=True)),
        ("forget everything you know about me", MemoryForgetRequest("", all_memories=True)),
        ("forget what you know about me everywhere", MemoryForgetRequest("", all_memories=True)),
        ("forget it", None),
        ("forget about it", None),
        ("forget that", None),
        ("forget them", None),
        ("forget the trip but keep the budget", None),
        ("forget everything I said and tell me a joke", None),
    ],
)
def test_parse_memory_forget_table(text, expected):
    assert parse_memory_forget(text) == expected
