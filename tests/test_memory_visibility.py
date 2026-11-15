"""Hide / unhide / delete: global, reversible hide; tombstone delete; nothing hidden resurfaces."""

import asyncio
import sqlite3
from contextlib import closing

import pytest
from app.config import Settings
from app.main import create_app
from app.services.context_operations import keyword_search
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_context_assembly import start_turn
from test_memory import FakeEmbedder

PROPOSE = "/internal/agent/tool/memory-propose"
RECALL = "/internal/agent/tool/memory-recall"


def configured(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/vis.db",
        agent_service_secret="test-service-secret",
        context_proposal_secret="vis-secret",
        _env_file=None,
    )
    return tmp_path / "vis.db", settings


def create(client, chat, content, key):
    response = client.post(f"/v1/conversations/{chat}/memories", json={"content": content, "idempotency_key": key})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def row(file, memory_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute(
            "SELECT status, content, subject, embedding FROM memories WHERE id=?", (memory_id,)
        ).fetchone()


def search_ids(client, chat, query="tea"):
    response = client.post("/v1/memories/search", json={"conversation_id": chat, "query": query})
    return [item["id"] for item in response.json()]


def test_hide_is_idempotent_reversible_and_visible_in_the_list(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        memory = create(client, chat, "I prefer green tea", "k1")
        assert memory in search_ids(client, chat)

        assert client.post(f"/v1/memories/{memory}/hide").json() == {"id": memory, "status": "hidden", "changed": True}
        assert client.post(f"/v1/memories/{memory}/hide").json()["changed"] is False
        assert search_ids(client, chat) == []
        hidden = client.get("/v1/memories", params={"status": "hidden"}).json()
        assert [item["id"] for item in hidden["items"]] == [memory] and hidden["total"] == 1
        assert client.get("/v1/memories", params={"status": "active"}).json()["items"] == []

        assert client.post(f"/v1/memories/{memory}/unhide").json() == {
            "id": memory,
            "status": "active",
            "changed": True,
        }
        assert client.post(f"/v1/memories/{memory}/unhide").json()["changed"] is False
        assert memory in search_ids(client, chat)


def test_hide_applies_in_every_conversation(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        first = client.post("/v1/conversations").json()["id"]
        second = client.post("/v1/conversations").json()["id"]
        memory = create(client, first, "I prefer green tea", "k1")
        client.post(f"/v1/memories/{memory}/hide")
        assert search_ids(client, second) == []
        item = client.get("/v1/memories", params={"conversation_id": second}).json()["items"][0]
        assert item["candidate_eligible"] is False


def test_delete_is_a_permanent_tombstone_that_wipes_content_and_embedding(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        memory = create(client, chat, "I prefer green tea", "k1")
        corrected = client.post(
            f"/v1/memories/{memory}/correct",
            json={"content": "I prefer black tea", "expected_version": 1, "idempotency_key": "c1"},
        ).json()["id"]
        assert client.delete(f"/v1/memories/{corrected}").json() == {
            "id": corrected,
            "status": "deleted",
            "changed": True,
        }
        assert client.delete(f"/v1/memories/{corrected}").json()["changed"] is False
        for identifier in (memory, corrected):
            status, content, subject, embedding = row(file, identifier)
            assert (status, content, subject) == ("deleted", "", None) and embedding in (None, "null")
        with closing(sqlite3.connect(file)) as db:
            ledger = db.execute("SELECT result, embedding FROM memory_write_requests").fetchall()
        assert all(result in (None, "null") and emb in (None, "null") for result, emb in ledger)
        assert client.get("/v1/memories").json() == {"items": [], "total": 0, "next_offset": None}
        assert search_ids(client, chat) == []
        assert client.post(f"/v1/memories/{corrected}/unhide").status_code == 409
        assert client.post(f"/v1/memories/{corrected}/hide").status_code == 409


def test_visibility_routes_are_owner_scoped_and_authenticated(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        assert client.post("/v1/memories/x/hide").status_code == 401
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        memory = create(client, chat, "I prefer green tea", "k1")
        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            assert other.post(f"/v1/memories/{memory}/hide").status_code == 404
            assert other.post(f"/v1/memories/{memory}/unhide").status_code == 404
            assert other.delete(f"/v1/memories/{memory}").status_code == 404
        assert row(file, memory)[0] == "active"


@pytest.mark.parametrize("how", ["hidden", "deleted"])
def test_a_hidden_or_deleted_memory_surfaces_nowhere(tmp_path, how):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        memory = create(client, chat, "I prefer green tea", "k1")
        keep = create(client, chat, "I own a cat named Momo", "k2")
        if how == "hidden":
            client.post(f"/v1/memories/{memory}/hide")
        else:
            client.delete(f"/v1/memories/{memory}")

        assert memory not in search_ids(client, chat)
        assert keep in search_ids(client, chat, "cat")
        correction = client.post(
            f"/v1/memories/{memory}/correct", json={"content": "x", "expected_version": 1, "idempotency_key": "c"}
        )
        assert correction.status_code == 409

        headers, started = start_turn(client, chat, owner, file, memories=(), text="Tell me about tea")
        context = started["context"]
        assert "green tea" not in str(context["model_request"]["memories"])
        # Hidden text may appear only inside the quoted retraction block (it tells the model to forget it).
        leaking = [m for m in context["messages"] if "green tea" in m["content"]]
        if how == "hidden":
            assert [m["content"].split(":")[0] for m in leaking] == ["Memory (untrusted memory data, id=retraction"]
            assert all("do not use, repeat or confirm it" in m["content"] for m in leaking)
        else:
            assert leaking == []

        recall = client.post(
            RECALL, headers=headers, json={"turn_id": started["turn_id"], "request_id": "r", "query": "tea"}
        ).json()
        assert recall["memories"] == []
        if how == "hidden":
            payload = {"turn_id": started["turn_id"], "request_id": "p", "fact": "I prefer green tea"}
            assert client.post(PROPOSE, headers=headers, json=payload).json()["status"] == "proposed"

    async def keyword():
        engine = create_async_engine(settings.database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                return await keyword_search(db, owner_id=owner, conversation_id=chat, query="tea", limit=10)
        finally:
            await engine.dispose()

    assert asyncio.run(keyword()) == ()


def test_voice_forget_hides_everywhere_and_delete_by_voice_is_refused(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        other_chat = client.post("/v1/conversations").json()["id"]
        memory = create(client, chat, "I prefer green tea", "k1")

        def say(text, request):
            return client.post(
                "/v1/actions",
                json={
                    "conversation_id": chat,
                    "request_id": request,
                    "expected_revision": 0,
                    "source": "transcript",
                    "transcript": text,
                },
            ).json()

        refusal = say("Delete the memory of the green tea.", "d1")["result"]
        assert refusal["kind"] == "unsupported" and "trash icon" in refusal["spoken"]
        assert row(file, memory)[0] == "active"

        hidden = say("Forget the green tea.", "f1")["result"]
        assert hidden["detail"] == "MEMORY_HIDDEN" and hidden["changed"] is True
        assert hidden["spoken"] == "Hidden 'I prefer green tea'. Say undo to bring it back."
        assert say("Forget the green tea.", "f1")["result"] == hidden
        assert row(file, memory)[0] == "hidden" and search_ids(client, other_chat) == []

        assert say("Yes.", "y1") == {"handled": False}
        undone = say("Bring it back.", "u1")["result"]
        assert undone["detail"] == "MEMORY_UNHIDDEN" and row(file, memory)[0] == "active"
        assert say("Undo", "u2")["result"]["detail"] == "NO_HIDDEN_MEMORY"


def test_no_model_tool_can_delete():
    import main

    names = {name for name in dir(main.SarjyAgent) if not name.startswith("_")}
    assert not {name for name in names if "delete" in name or "hide" in name}
