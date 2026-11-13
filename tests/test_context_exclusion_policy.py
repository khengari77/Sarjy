"""Explicit keep/exclude semantics for budget-classified memory (#80).

The policy correction is deliberate: currency words label a record but never
confer permanent immunity.  Explicit keep authority wins, while an unscoped
bulk request preserves and surfaces budget ambiguity instead of guessing.
"""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.domain.context_operations import sign_proposal
from app.main import create_app as real_create_app
from fastapi.testclient import TestClient


class FakeEmbedder:
    async def embed(self, db, text):
        return (1.0,) + (0.0,) * 1535


def create_app(settings):
    return real_create_app(settings, embedding_adapter=FakeEmbedder())


def configured(tmp_path):
    file = tmp_path / "policy.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


ROWS = (
    ("trip", "Trip to Tripoli from 1 to 5 May"),
    ("hotel", "Hotel in Rome, 3 nights"),
    ("budget", "Budget 500 USD for the trip"),
)


def seed(file, chat, rows=ROWS):
    with closing(sqlite3.connect(file)) as db, db:
        for record_id, content in rows:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) "
                "VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', ?, 1, 'active', "
                "'2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
                (record_id, chat, content, chat),
            )
        db.execute("UPDATE memories SET embedding=?", (json.dumps([1.0] + [0.0] * 1535),))
        db.commit()


def counts(file):
    with closing(sqlite3.connect(file)) as db, db:
        revision = db.execute("SELECT revision FROM conversations").fetchone()[0]
        exclusions = db.execute("SELECT count(*) FROM context_exclusions").fetchone()[0]
        active = db.execute("SELECT count(*) FROM context_exclusions WHERE active=1").fetchone()[0]
        operations = db.execute("SELECT count(*) FROM context_operations").fetchone()[0]
        ledger = db.execute("SELECT day, provider, reserved_units FROM development_budgets ORDER BY day, provider")
        ledger = ledger.fetchall()
    return revision, exclusions, active, operations, ledger


def add_turn_session(file, chat):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            (
                "session",
                chat,
                owner,
                "room",
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}


def start_turn(client, headers, request_id="next", text="What do I remember?"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})
    assert response.status_code == 200, response.text
    return response.json()["context"]


def preview(client, chat, *, query, keep=()):
    response = client.post(f"/v1/conversations/{chat}/context/proposals", json={"query": query, "keep": list(keep)})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, chat, body, key):
    return client.post(
        f"/v1/conversations/{chat}/context/confirm",
        json={"proposal": body["proposal"], "expected_revision": body["revision"], "idempotency_key": key},
    )


def test_ambiguous_budget_preview_surfaces_clarification_and_writes_nothing(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)
        before = counts(file)

        body = preview(client, chat, query="forget the trip hotel")

        assert body["requires_clarification"] is True
        assert [item["id"] for item in body["ambiguous"]] == ["budget"]
        assert body["ambiguous"][0]["reason"] == "budget requires clarification"
        assert {item["id"] for item in body["preserved"]} == {"budget"}
        assert set(body["diff"]["removed"]) == {"trip", "hotel"}
        assert counts(file) == before, "preview must not write exclusions, operations or revisions"

        applied = confirm(client, chat, body, "ambiguous-confirm").json()
        assert set(applied["excluded_ids"]) == {"trip", "hotel"}
        assert applied["kept_ids"] == ["budget"]

        headers = add_turn_session(file, chat)
        context = start_turn(client, headers)
        included = set(context["included_record_ids"])
        assert "budget" in included
        assert not included & {"trip", "hotel"}


def test_explicit_budget_keep_survives_preview_confirmation_and_next_request(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat, (("trip", "Trip to Tripoli from 1 to 5 May"), ("budget", "Budget 500 USD for the trip")))

        body = preview(client, chat, query="forget the trip", keep=("budget",))
        assert body["requires_clarification"] is False
        assert body["ambiguous"] == []
        reasons = {item["id"]: item["reason"] for item in body["preserved"]}
        assert reasons == {"budget": "explicit keep id"}
        assert body["diff"]["removed"] == ["trip"]

        applied = confirm(client, chat, body, "keep-confirm").json()
        assert applied["excluded_ids"] == ["trip"]

        headers = add_turn_session(file, chat)
        context = start_turn(client, headers)
        included = set(context["included_record_ids"])
        assert "budget" in included and "trip" not in included


def test_explicit_budget_exclusion_omits_ids_and_derivatives_without_touching_ledgers(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat, (("budget", "Budget 500 USD for the trip"), ("price", "Hotel price is 300 USD")))
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("INSERT INTO development_budgets VALUES ('2026-11-12','openai',7)")
            db.execute("UPDATE memories SET source_turn_id='source-budget' WHERE id='budget'")
            for identifier, role, request, snapshot in (
                ("source-budget", "user", "source-budget", {"included_record_ids": ["budget", "system"]}),
                ("derived-budget", "assistant", "reply:source-budget", None),
                ("tool-source", "user", "tool-source", {"included_record_ids": ["budget", "system"]}),
            ):
                db.execute(
                    "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        chat,
                        request,
                        role,
                        identifier,
                        "completed",
                        "2026-11-12T00:00:01+00:00",
                        json.dumps(snapshot)
                        if snapshot
                        else json.dumps(
                            {"derivation": {"turn_id": "source-budget", "record_ids": ["budget", "system"]}}
                        ),
                    ),
                )
            db.execute(
                "INSERT INTO tool_calls (id,turn_id,request_id,name,state,result) "
                "VALUES ('tool-budget','tool-source','tool-budget','get_current_weather','completed',?)",
                (json.dumps({"city": "Tripoli"}),),
            )
            db.commit()
        ledger_before = counts(file)[-1]

        body = preview(client, chat, query="forget the budget and the hotel price")
        assert body["requires_clarification"] is False
        assert body["ambiguous"] == []
        assert set(body["diff"]["removed"]) == {"budget", "price"}
        assert counts(file)[-1] == ledger_before

        applied = confirm(client, chat, body, "budget-confirm").json()
        assert set(applied["excluded_ids"]) == {"budget", "price"}
        assert counts(file)[-1] == ledger_before, "provider ledger must be untouched"

        headers = add_turn_session(file, chat)
        context = start_turn(client, headers)
        included = set(context["included_record_ids"])
        assert not included & {"budget", "price", "derived-budget", "tool-budget"}
        assert "source-budget" not in included
        assert set(context["excluded_record_ids"]) >= {"budget", "price"}


def test_currency_words_alone_do_not_forbid_exclusion(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat, (("price", "Hotel price is 300 USD"),))
        body = preview(client, chat, query="forget the price")
        assert body["diff"]["removed"] == ["price"]
        assert body["ambiguous"] == []
        assert confirm(client, chat, body, "price-confirm").json()["excluded_ids"] == ["price"]


def test_signed_proposals_reject_tampering_smuggling_and_lost_keep_arguments(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)
        body = preview(client, chat, query="forget the trip")

        with closing(sqlite3.connect(file)) as db:
            owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]

        tampered = body["proposal"][:-1] + ("A" if body["proposal"][-1] != "A" else "B")
        response = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": tampered, "expected_revision": 0, "idempotency_key": "tampered"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "PROPOSAL_INVALID"

        base = {"v": 1, "owner_id": owner, "conversation_id": chat, "revision": 0, "query": "forget"}
        smuggled = sign_proposal(
            base | {"keep": ["trip"], "excluded_ids": ["trip"], "kept_ids": ["trip"]}, "proposal-secret"
        )
        response = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": smuggled, "expected_revision": 0, "idempotency_key": "smuggled"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "PROPOSAL_INVALID"

        lost_keep = sign_proposal(base | {"excluded_ids": ["trip"], "kept_ids": []}, "proposal-secret")
        response = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": lost_keep, "expected_revision": 0, "idempotency_key": "lost-keep"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "PROPOSAL_INVALID"
        assert counts(file)[:4] == (0, 0, 0, 0)


def test_duplicates_and_undo_preserve_exact_semantics_and_ledgers(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("INSERT INTO development_budgets VALUES ('2026-11-12','openai',7)")
            db.commit()
        before = counts(file)

        body = preview(client, chat, query="forget the trip hotel")
        assert counts(file) == before
        applied = confirm(client, chat, body, "duplicate").json()
        duplicate = confirm(client, chat, body, "duplicate").json()
        assert duplicate["idempotent"] is True
        assert duplicate["operation_id"] == applied["operation_id"]
        assert counts(file)[:4] == (1, 2, 2, 1)
        assert counts(file)[-1] == before[-1]

        undo = client.post(
            f"/v1/conversations/{chat}/context/undo",
            json={"operation_id": applied["operation_id"], "expected_revision": 1, "idempotency_key": "undo"},
        ).json()
        assert undo["restored_ids"] == ["hotel", "trip"]
        assert counts(file)[:4] == (2, 2, 0, 2)
        assert counts(file)[-1] == before[-1], "Undo must not touch provider ledgers"

        stale = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": body["proposal"], "expected_revision": 0, "idempotency_key": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
