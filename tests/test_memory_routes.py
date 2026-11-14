from contextlib import closing

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder


def test_memory_routes_authenticate_persist_and_reject_forged_ownership(tmp_path):
    embedder = FakeEmbedder()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db", _env_file=None)
    with TestClient(create_app(settings, embedding_adapter=embedder)) as client:
        assert client.post("/v1/memories/search", json={"conversation_id": "x", "query": "city"}).status_code == 401
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        payload = {"content": "Tripoli", "idempotency_key": "once"}
        result = client.post(f"/v1/conversations/{chat}/memories", json=payload)
        assert result.status_code == 201, result.text
        memory = result.json()
        assert client.post(f"/v1/conversations/{chat}/memories", json=payload).json() == memory
        second = client.post("/v1/conversations").json()["id"]
        found = client.post("/v1/memories/search", json={"conversation_id": second, "query": "city"})
        assert found.status_code == 200 and found.json()[0]["id"] == memory["id"]
        corrected = client.post(
            f"/v1/memories/{memory['id']}/correct",
            json={"content": "London", "expected_version": 1, "idempotency_key": "correct"},
        )
        assert corrected.status_code == 200 and corrected.json()["version"] == 2
        assert (
            client.post(f"/v1/conversations/{chat}/memories", json=payload | {"owner_id": "forged"}).status_code == 422
        )
        # A bare client reuses the running lifespan and must still be closed explicitly.
        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            before = len(embedder.calls)
            assert other.post(f"/v1/conversations/{chat}/memories", json=payload).status_code == 404
            assert other.post("/v1/memories/search", json={"conversation_id": chat, "query": "city"}).status_code == 404
            assert (
                other.post(
                    f"/v1/memories/{memory['id']}/correct",
                    json={"content": "Paris", "expected_version": 1, "idempotency_key": "other"},
                ).status_code
                == 404
            )
            assert len(embedder.calls) == before


def test_memory_list_is_owner_scoped_paginated_and_carries_provenance(tmp_path):
    embedder = FakeEmbedder()
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/list.db",
        context_proposal_secret="list-proposal-secret",
        _env_file=None,
    )
    with TestClient(create_app(settings, embedding_adapter=embedder)) as client:
        assert client.get("/v1/memories").status_code == 401
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        created = []
        for index, content in enumerate(("Tripoli", "Budget 500 USD", "Window seat")):
            response = client.post(
                f"/v1/conversations/{chat}/memories",
                json={"content": content, "idempotency_key": f"list-{index}", "source_turn_id": None},
            )
            assert response.status_code == 201, response.text
            created.append(response.json())
        first = client.get("/v1/memories", params={"limit": 2}).json()
        assert first["total"] == 3 and len(first["items"]) == 2 and first["next_offset"] == 2
        assert all(
            {"id", "content", "version", "status", "source_conversation_id", "created_at", "updated_at"} <= set(item)
            for item in first["items"]
        )
        second = client.get("/v1/memories", params={"limit": 2, "offset": 2}).json()
        assert len(second["items"]) == 1 and second["next_offset"] is None
        assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
        scoped = client.get("/v1/memories", params={"conversation_id": chat, "limit": 100}).json()
        assert {item["id"] for item in scoped["items"]} == {item["id"] for item in created}
        assert all(item["included_in_conversation"] is True for item in scoped["items"])
        assert all(item["excluded_in_conversation"] is False for item in scoped["items"])

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            assert other.get("/v1/memories").json()["items"] == []
            assert other.get("/v1/memories", params={"conversation_id": chat}).status_code == 404
            other_chat = other.post("/v1/conversations").json()["id"]
            cross_search = other.post("/v1/memories/search", json={"conversation_id": other_chat, "query": "Tripoli"})
            assert cross_search.status_code == 200 and cross_search.json() == []
            foreign_exclusion = other.post(
                f"/v1/conversations/{other_chat}/context/exclusions", json={"memory_ids": [created[0]["id"]]}
            )
            assert foreign_exclusion.status_code == 404
