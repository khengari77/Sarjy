"""Deleting a conversation tombstones it, scrubs every stored copy and leaves global memories alone."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from app.config import Settings
from app.infrastructure.database import Base, Conversation, Memory
from app.main import create_app
from app.services.memory_terms import terms_of
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, turn

MARKER = "UNIQUE-ERASURE-MARKER-302"


def configured(tmp_path):
    file = tmp_path / "conversation-delete.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="delete-secret",
        _env_file=None,
    )
    return file, settings


def stamp():
    return datetime.now(UTC).isoformat()


def seed_copy(file, chat, owner, turn_id):
    """One row of each stored copy of the chat's text: tool result, snapshot, exclusion, operation, temp memory."""
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO tool_calls (id, turn_id, request_id, name, state, result, error_code, arguments_hash)"
            " VALUES ('tool-1', ?, 'tool-request-1', 'get_current_weather', 'completed', ?, NULL, NULL)",
            (turn_id, json.dumps({"summary": MARKER})),
        )
        db.execute(
            "INSERT INTO context_snapshots (id, turn_id, revision, canonical_messages, manifest, pipeline_config, created_at)"
            " VALUES ('snap-1', ?, 0, ?, '{}', '{}', ?)",
            (turn_id, json.dumps([{"role": "user", "content": MARKER}]), stamp()),
        )
        db.execute(
            "INSERT INTO context_operations"
            " (id, owner_id, conversation_id, command_type, payload, revision_before, revision_after, idempotency_key,"
            " outcome, created_at)"
            " VALUES ('op-1', ?, ?, 'context_exclude', ?, 0, 1, 'operation-key-1', 'applied', ?)",
            (owner, chat, json.dumps({"kind": "context_exclude", "label": MARKER}), stamp()),
        )
        db.execute(
            "INSERT INTO context_exclusions"
            " (id, owner_id, conversation_id, memory_id, operation_id, scope, active, created_at)"
            " VALUES ('exclusion-1', ?, ?, 'm-global', 'op-1', 'conversation', 1, ?)",
            (owner, chat, stamp()),
        )
        db.execute(
            "INSERT INTO memories"
            " (id, owner_id, subject, content, embedding_model, source_turn_id, source_conversation_id, version, status,"
            " scope, request_key, created_at, updated_at)"
            " VALUES ('m-temp', ?, 'note', ?, 'text-embedding-3-small', ?, ?, 1, 'active', 'conversation',"
            " 'temp-request-key', ?, ?)",
            (owner, MARKER, turn_id, chat, stamp(), stamp()),
        )
        for term in terms_of(MARKER):
            db.execute(
                "INSERT INTO memory_terms (memory_id, owner_id, term) VALUES ('m-temp', ?, ?)",
                (owner, term),
            )
        db.execute(
            "INSERT INTO memory_write_requests"
            " (id, owner_id, conversation_id, request_key, fingerprint, state, embedding, result, error_code, error_status,"
            " created_at, updated_at)"
            " VALUES ('write-1', ?, ?, 'temp-request-key', 'fingerprint', 'completed', NULL, ?, NULL, NULL, ?, ?)",
            (
                owner,
                chat,
                json.dumps({"response": {"status": "saved", "memory": {"id": "m-temp", "content": MARKER}}}),
                stamp(),
                stamp(),
            ),
        )
        db.execute(
            "INSERT INTO memory_capture_decisions"
            " (confirmation_id, revision, owner_id, conversation_id, token_hash, decision, outcome, target_id,"
            " target_version, result, lease_id, lease_expires_at, created_at, expires_at)"
            " VALUES ('confirmation-1', 1, ?, ?, 'hash', 'save', 'saved', 'm-temp', 1, ?, NULL, NULL, ?, ?)",
            (owner, chat, json.dumps({"status": "saved", "memory": {"content": MARKER}}), stamp(), stamp()),
        )


def marker_locations(file, marker):
    """Every table and column where the marker still appears, found from the ORM metadata."""
    found = []
    with closing(sqlite3.connect(file)) as db:
        for table in Base.metadata.sorted_tables:
            columns = [column.name for column in table.columns]
            for row in db.execute(f'SELECT * FROM "{table.name}"').fetchall():
                for name, value in zip(columns, row, strict=True):
                    if value is not None and marker in str(value):
                        found.append((table.name, name, row[0]))
    return found


def test_delete_is_idempotent_owner_scoped_and_hides_the_conversation(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        other = client.post("/v1/conversations").json()["id"]

        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        assert [item["id"] for item in client.get("/v1/conversations").json()] == [other]
        assert client.get(f"/v1/conversations/{chat}/turns").status_code == 404
        assert client.get(f"/v1/conversations/{chat}/tool-calls").status_code == 404

        with closing(TestClient(client.app)) as stranger:
            stranger.post("/v1/demo-session")
            assert stranger.delete(f"/v1/conversations/{chat}").status_code == 404

        with closing(sqlite3.connect(file)) as db:
            title, revision, deleted_at = db.execute(
                "SELECT title, revision, deleted_at FROM conversations WHERE id=?", (chat,)
            ).fetchone()
        assert title == "Deleted conversation" and revision == 1 and deleted_at is not None


def test_delete_erases_every_stored_copy_of_the_conversation_text(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        started = turn(client, headers, f"{MARKER} as a user turn", "delete-leak-1")
        assert started.status_code == 200, started.text
        turn_id = started.json()["turn_id"]
        finished = client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": turn_id, "text": f"{MARKER} as an assistant turn", "status": "completed"},
        )
        assert finished.status_code == 200, finished.text
        seed_copy(file, chat, owner, turn_id)
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE voice_sessions SET status='ended' WHERE conversation_id=?", (chat,))

        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        assert marker_locations(file, MARKER) == []
        with closing(sqlite3.connect(file)) as db:
            # Terms are derived copies of the marker text too: the temporary memory must take them with it.
            assert (
                db.execute(
                    "SELECT term FROM memory_terms WHERE memory_id='m-temp' AND term IN (?, ?, ?, ?)",
                    tuple(terms_of(MARKER)),
                ).fetchall()
                == []
            )


def test_a_global_memory_from_the_chat_survives_and_stays_readable(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        created = client.post(
            f"/v1/conversations/{chat}/memories",
            json={"content": "I prefer green tea", "idempotency_key": "global-1"},
        )
        assert created.status_code == 201, created.text
        memory = created.json()["id"]

        assert client.delete(f"/v1/conversations/{chat}").status_code == 204

        items = client.get("/v1/memories").json()["items"]
        assert [(item["id"], item["content"], item["status"]) for item in items] == [
            (memory, "I prefer green tea", "active")
        ]
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT scope FROM memories WHERE id=?", (memory,)).fetchone() == ("global",)


def test_delete_refuses_a_live_voice_session_and_works_after_it_ends(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        add_turn_session(file, chat, owner)

        refused = client.delete(f"/v1/conversations/{chat}")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "CONVERSATION_ACTIVE"
        assert [item["id"] for item in client.get("/v1/conversations").json()] == [chat]

        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "UPDATE voice_sessions SET status='ended', expires_at=? WHERE conversation_id=?",
                (datetime.now(UTC).isoformat(), chat),
            )
        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        assert client.get("/v1/conversations").json() == []


def test_late_worker_writes_and_titles_cannot_repopulate_a_deleted_conversation(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        started = turn(client, headers, f"{MARKER} late write", "delete-late-1")
        assert started.status_code == 200, started.text
        turn_id = started.json()["turn_id"]
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE voice_sessions SET status='ended' WHERE conversation_id=?", (chat,))

        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        # A session admitted before the delete can still be live in the worker; every late write must refuse.
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE voice_sessions SET status='active' WHERE conversation_id=?", (chat,))

        assert turn(client, headers, "a new late turn", "delete-late-2").status_code == 404
        assert (
            client.post(
                "/internal/agent/turn/finalize",
                headers=headers,
                json={"turn_id": turn_id, "text": f"{MARKER} late reply", "status": "completed"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/internal/agent/turn/metrics", headers=headers, json={"turn_id": turn_id, "stt_ms": 5}
            ).status_code
            == 404
        )
        assert client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id}).status_code == 404
        assert (
            client.post(
                "/internal/agent/tool/memory-propose",
                headers=headers,
                json={"turn_id": turn_id, "request_id": "late-proposal", "fact": "a late fact"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/internal/agent/memory/confirm", headers=headers, json={"token": "x", "decision": "save"}
            ).status_code
            == 404
        )
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM turn_metrics").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
            assert db.execute("SELECT text FROM turns").fetchall() == [("",)]
            assert (
                db.execute("SELECT title FROM conversations WHERE id=?", (chat,)).fetchone()[0]
                == "Deleted conversation"
            )

    async def title_cannot_write():
        from app.services import titles

        engine = create_async_engine(settings.database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)

            async def sources(_sessions, _conversation_id):
                return ("late user text", "late assistant text")

            async def fake_fetch(*_args, **_kwargs):
                return "Late generated title"

            monkeypatch.setattr(titles, "_title_sources", sources)
            monkeypatch.setattr(titles, "fetch_title", fake_fetch)
            assert (
                await titles._generate_title(
                    sessions, chat, api_key="test-key", reserve_budget=False, provider=titles.DEEPSEEK_PROVIDER
                )
                is None
            )
            async with sessions() as db:
                conversation = await db.get(Conversation, chat)
                assert conversation.title == "Deleted conversation"
        finally:
            await engine.dispose()

    asyncio.run(title_cannot_write())


def test_delete_takes_the_conversation_row_lock_then_the_owner_lock(tmp_path, monkeypatch):
    from app.services import conversation_delete

    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lock.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                db.add(Conversation(id="chat", owner_id="owner", title="Trip", revision=0))
                await db.commit()

                calls = []
                real_owned = conversation_delete.owned_conversation

                async def owned_spy(
                    db, owner_id, conversation_id, *, lock=False, include_deleted=False, key_share=False
                ):
                    calls.append(("conversation", lock, include_deleted))
                    assert key_share, "the delete must use FOR NO KEY UPDATE so memory inserts are not blocked"
                    return await real_owned(
                        db, owner_id, conversation_id, lock=lock, include_deleted=include_deleted, key_share=key_share
                    )

                async def owner_lock_spy(db, owner_id):
                    calls.append(("owner", owner_id))

                monkeypatch.setattr(conversation_delete, "owned_conversation", owned_spy)
                monkeypatch.setattr(conversation_delete, "lock_owner_memories", owner_lock_spy)
                await conversation_delete.delete_conversation(db, owner_id="owner", conversation_id="chat")
                assert calls == [("conversation", True, True), ("owner", "owner")]

                conversation = await db.get(Conversation, "chat")
                assert conversation.deleted_at is not None
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_a_temporary_memory_lineage_is_erased_but_a_global_memory_is_not(tmp_path):
    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lineage.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                db.add(Conversation(id="chat", owner_id="owner", title="Trip", revision=0))
                db.add(
                    Memory(
                        id="temp-old",
                        owner_id="owner",
                        source_conversation_id="chat",
                        content="old temporary",
                        scope="conversation",
                        status="superseded",
                        version=1,
                    )
                )
                db.add(
                    Memory(
                        id="temp-new",
                        owner_id="owner",
                        source_conversation_id="chat",
                        content="new temporary",
                        scope="conversation",
                        status="active",
                        version=2,
                        supersedes_id="temp-old",
                    )
                )
                db.add(
                    Memory(
                        id="global",
                        owner_id="owner",
                        source_conversation_id="chat",
                        content="global fact",
                        scope="global",
                        status="active",
                        version=1,
                        supersedes_id="temp-old",
                    )
                )
                await db.commit()

                from app.services.conversation_delete import delete_conversation

                await delete_conversation(db, owner_id="owner", conversation_id="chat")
                rows = {
                    row.id: (row.status, row.content, row.scope) for row in (await db.scalars(select(Memory))).all()
                }
                assert rows["temp-old"] == ("deleted", "", "conversation")
                assert rows["temp-new"] == ("deleted", "", "conversation")
                assert rows["global"] == ("active", "global fact", "global")
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_a_global_memory_can_still_be_corrected_after_its_source_chat_is_deleted(tmp_path):
    _, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        created = client.post(
            f"/v1/conversations/{chat}/memories", json={"content": "I prefer green tea", "idempotency_key": "g-1"}
        ).json()
        assert client.delete(f"/v1/conversations/{chat}").status_code == 204

        corrected = client.post(
            f"/v1/memories/{created['id']}/correct",
            json={"expected_version": created["version"], "content": "I prefer black tea", "idempotency_key": "g-2"},
        )
        assert corrected.status_code in (200, 201), corrected.text
        assert corrected.json()["content"] == "I prefer black tea"
        # The deleted chat itself stays closed to new writes.
        again = client.post(
            f"/v1/conversations/{chat}/memories", json={"content": "I like rain", "idempotency_key": "g-3"}
        )
        assert again.status_code == 404


def test_the_journal_entry_of_a_surviving_global_memory_stays_undoable_and_a_temporary_one_is_scrubbed(tmp_path):
    from app.domain.memory_changes import AUTO_SAVE
    from app.infrastructure.database import ContextOperation
    from app.services.conversation_delete import delete_conversation
    from app.services.memory_auto import _change_payload

    def operation(key, memory_id, label):
        return ContextOperation(
            id=key,
            owner_id="owner",
            conversation_id="chat",
            command_type=AUTO_SAVE,
            payload={"kind": "memory_saved", "label": label, "memory_id": memory_id, "version": 1},
            idempotency_key=key,
            outcome="applied",
        )

    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/journal.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                db.add(Conversation(id="chat", owner_id="owner", title="Trip", revision=0))
                for name, scope in (("keep", "global"), ("temp", "conversation")):
                    db.add(
                        Memory(
                            id=name,
                            owner_id="owner",
                            source_conversation_id="chat",
                            content=f"{name} fact",
                            scope=scope,
                            status="active",
                            version=1,
                        )
                    )
                db.add_all([operation("op-keep", "keep", "Remembered: keep fact"), operation("op-temp", "temp", "x")])
                await db.commit()

                await delete_conversation(db, owner_id="owner", conversation_id="chat")
                kept = await db.get(ContextOperation, "op-keep")
                gone = await db.get(ContextOperation, "op-temp")
                assert kept.payload["label"] == "Remembered: keep fact" and _change_payload(kept)["undoable"]
                assert gone.payload.get("scrubbed") is True and not _change_payload(gone)["undoable"]
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_finalize_takes_the_shared_conversation_lock(tmp_path, monkeypatch):
    """finalize is accepted for 60 s after a call ends and commits its own write: a delete must wait for it."""
    from app.routes import worker

    seen = []
    original = worker._live_conversation

    async def spy(db, conversation_id, *, share=False):
        seen.append(share)
        return await original(db, conversation_id, share=share)

    monkeypatch.setattr(worker, "_live_conversation", spy)
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        turn_id = turn(client, headers, "hello there", "lock-1").json()["turn_id"]
        seen.clear()
        done = client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": turn_id, "text": "hi", "status": "completed"},
        )
        assert done.status_code == 200 and seen == [True]
