import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.domain.budget import OPENAI_DAILY_MICRO_USD, budget_day
from app.domain.memory_capture import parse_capture
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder


def configured(tmp_path, **overrides):
    file = tmp_path / "capture.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
        **overrides,
    )
    return file, settings


def add_turn_session(file, chat, owner, identifier="session"):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            (
                identifier,
                chat,
                owner,
                f"room-{identifier}",
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": identifier, "x-room-name": f"room-{identifier}"}


def turn(client, headers, text, request_id):
    return client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})


def memory_rows(file):
    with closing(sqlite3.connect(file)) as db, db:
        return db.execute(
            "SELECT content, version, status, source_turn_id FROM memories ORDER BY created_at"
        ).fetchall()


def test_parse_explicit_and_ordinary_but_not_negated_or_quoted():
    assert parse_capture("Remember that I prefer tea").text == "I prefer tea"
    assert parse_capture("Don't forget my budget is 300 USD").subject == "attribute:budget"
    assert parse_capture("I live in Tripoli").subject == "location"
    assert parse_capture("My name is Sam").subject == "attribute:name"
    assert parse_capture("I don't remember that") is None
    assert parse_capture("Remember that if I win I prefer tea") is None
    assert parse_capture('"I prefer tea"') is None
    assert parse_capture("Do I prefer tea?") is None


def test_finalized_fact_requires_confirmation_then_recalls_corrects_and_isolates(tmp_path):
    file, settings = configured(tmp_path)
    embedder = FakeEmbedder()
    with TestClient(create_app(settings, embedding_adapter=embedder)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        started = turn(client, headers, "I prefer tea with mint", "fact-1").json()
        proposal = started["memory_proposal"]
        assert proposal is not None and proposal["text"] == "I prefer tea with mint"
        assert "pending" in started["context"]["model_request"]["instructions"].lower()
        assert memory_rows(file) == []

        saved = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "save"}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        rows = memory_rows(file)
        assert len(rows) == 1 and rows[0][0] == "I prefer tea with mint"
        assert rows[0][3] == started["turn_id"]

        duplicate = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "save"}
        )
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "already_saved"
        assert len(memory_rows(file)) == 1

        # A different conversation under the same owner recalls the confirmed fact.
        recalled_chat = client.post("/v1/conversations").json()["id"]
        recalled_headers = add_turn_session(file, recalled_chat, owner, identifier="session-2")
        recalled = turn(client, recalled_headers, "What do I prefer?", "recall-1").json()
        messages = recalled["context"]["model_request"]["messages"]
        assert any("tea with mint" in message["content"] for message in messages)

        corrected = turn(client, recalled_headers, "I prefer coffee now", "fact-2").json()["memory_proposal"]
        result = client.post(
            "/internal/agent/memory/confirm",
            headers=recalled_headers,
            json={"token": corrected["token"], "decision": "save"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "corrected" and result.json()["memory"]["version"] == 2
        active = [row for row in memory_rows(file) if row[2] == "active"]
        assert [row[0] for row in active] == ["I prefer coffee now"]

        # Another owner cannot consume the signed proposal.
        other = TestClient(client.app)
        other.post("/v1/demo-session")
        other_chat = other.post("/v1/conversations").json()["id"]
        other_headers = add_turn_session(file, other_chat, "someone-else", identifier="session-3")
        forged = other.post(
            "/internal/agent/memory/confirm",
            headers=other_headers,
            json={"token": corrected["token"], "decision": "save"},
        )
        assert forged.status_code == 404


def test_discard_negation_and_quotation_write_nothing(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        for index, text in enumerate(["I don't remember that", '"I prefer tea"', "If I win I would like tea"]):
            response = turn(client, headers, text, f"none-{index}")
            assert response.status_code == 200
            assert response.json()["memory_proposal"] is None
        assert memory_rows(file) == []

        proposal = turn(client, headers, "I like short answers", "discard-1").json()["memory_proposal"]
        discarded = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "discard"}
        )
        assert discarded.status_code == 200 and discarded.json()["status"] == "discarded"
        assert memory_rows(file) == []


def test_budget_denial_writes_nothing_and_reports_429(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        proposal = turn(client, headers, "My budget is 300 USD", "budget-1").json()["memory_proposal"]
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO development_budgets (day,provider,reserved_units) VALUES (?,?,?)",
                (budget_day(datetime.now(UTC)), "openai", OPENAI_DAILY_MICRO_USD),
            )
            db.commit()
        denied = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "save"}
        )
        assert denied.status_code == 429
        assert memory_rows(file) == []


def test_worker_confirms_pending_memory_only_on_explicit_affirmative():
    import asyncio
    from types import SimpleNamespace

    import httpx
    from main import SarjyAgent

    async def exchange(text, new_proposal=None):
        paths = []

        def backend(request):
            paths.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": new_proposal})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.memory_proposal = {"token": "signed", "text": "I prefer tea"}
            await agent.compile(SimpleNamespace(id="message", text_content=text))
            return paths, agent.memory_proposal

    paths, remaining = asyncio.run(exchange("yes"))
    assert remaining is None
    assert paths == [
        ("/internal/agent/turn/start", {"request_id": "message", "text": "yes"}),
        ("/internal/agent/memory/confirm", {"token": "signed", "decision": "save"}),
    ]

    paths, remaining = asyncio.run(exchange("maybe later"))
    assert paths == [("/internal/agent/turn/start", {"request_id": "message", "text": "maybe later"})]
    assert remaining == {"token": "signed", "text": "I prefer tea"}

    paths, remaining = asyncio.run(exchange("no thanks"))
    assert remaining is None
    assert paths[-1] == ("/internal/agent/memory/confirm", {"token": "signed", "decision": "discard"})
