"""Provider spending boundaries for context preview, confirmation and Undo (#80).

Preview is allowed to reserve one conservative embedding request through the
shared ledger. Confirmation and Undo must make no provider calls and add no
reservations. These tests use the real ``OpenAIEmbedder`` reservation adapter
with a fake transport, so a ``FakeEmbedder`` cannot hide the spending path.
"""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import httpx
from app.config import Settings
from app.domain.budget import OPENAI_DAILY_MICRO_USD, budget_day
from app.main import create_app
from app.services.memory import OpenAIEmbedder
from fastapi.testclient import TestClient

EMBEDDING = [1.0] + [0.0] * 1535


def configured(tmp_path):
    file = tmp_path / "provider-budget.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


def seed(file, chat):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
            "created_at,updated_at,embedding) VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), "
            "'Trip to Rome in May', 'text-embedding-3-small', ?, 1, 'active', "
            "'2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00', ?)",
            ("trip", chat, chat, json.dumps(EMBEDDING)),
        )
        db.commit()


def ledger(file):
    with closing(sqlite3.connect(file)) as db, db:
        row = db.execute("SELECT reserved_units FROM development_budgets WHERE provider='openai'").fetchone()
        return row[0] if row else 0


def exhausted(file):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT OR REPLACE INTO development_budgets (day, provider, reserved_units) VALUES (?, 'openai', ?)",
            (budget_day(datetime.now(UTC)), OPENAI_DAILY_MICRO_USD),
        )
        db.commit()


def embedding_transport(calls, *, on_request=None):
    def handler(request):
        calls.append(request)
        if on_request is not None:
            on_request()
        return httpx.Response(200, json={"data": [{"embedding": EMBEDDING}]})

    return httpx.MockTransport(handler)


def test_preview_reserves_before_delivery_and_confirmation_and_undo_add_nothing(tmp_path):
    file, settings = configured(tmp_path)
    calls = []
    reserved_before_delivery = []

    def observe():
        reserved_before_delivery.append(ledger(file))

    embedder = OpenAIEmbedder("test-key", transport=embedding_transport(calls, on_request=observe))
    with TestClient(create_app(settings, embedding_adapter=embedder)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)

        preview = client.post(f"/v1/conversations/{chat}/context/proposals", json={"query": "trip"})
        assert preview.status_code == 200, preview.text
        assert len(calls) == 1
        assert reserved_before_delivery and reserved_before_delivery[0] > 0, "reserve before delivery"
        after_preview = ledger(file)
        assert after_preview > 0

        body = preview.json()
        applied = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": body["proposal"], "expected_revision": body["revision"], "idempotency_key": "confirm"},
        )
        assert applied.status_code == 200, applied.text
        assert ledger(file) == after_preview, "confirmation must not spend"

        undo = client.post(
            f"/v1/conversations/{chat}/context/undo",
            json={
                "operation_id": applied.json()["operation_id"],
                "expected_revision": applied.json()["revision_after"],
                "idempotency_key": "undo",
            },
        )
        assert undo.status_code == 200, undo.text
        assert ledger(file) == after_preview, "Undo must not spend"
        assert len(calls) == 1, "confirmation and Undo make no provider calls"


def test_denied_embedding_reservation_sends_zero_requests(tmp_path):
    file, settings = configured(tmp_path)
    calls = []
    embedder = OpenAIEmbedder("test-key", transport=embedding_transport(calls))
    with TestClient(create_app(settings, embedding_adapter=embedder)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)
        exhausted(file)

        preview = client.post(f"/v1/conversations/{chat}/context/proposals", json={"query": "trip"})
        assert preview.status_code == 429, preview.text
        assert preview.json()["detail"]["code"] == "DAILY_BUDGET_EXHAUSTED"
        assert calls == []
