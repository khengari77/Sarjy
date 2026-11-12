import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import app.routes.worker as worker_module
import pytest
from app.config import Settings
from app.domain.context import (
    ContextCandidate,
    compile_turn_context,
    validate_assembly,
)
from app.main import create_app
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st


def memory(record_id, text, status="active"):
    return ContextCandidate(id=record_id, role="memory", text=text, origin="memory", status=status)


def history(record_id, text, role="user"):
    return ContextCandidate(id=record_id, role=role, text=text, origin="history")


def tool_result(record_id, text, source="get_current_weather"):
    return ContextCandidate(id=record_id, role="tool", text=text, origin="tool", source=source)


def test_compile_filters_exclusions_superseded_and_unknown_tools():
    compiled = compile_turn_context(
        revision=7,
        instructions="Rules",
        history=(history("h1", "Earlier turn"),),
        memories=(
            memory("m1", "Tripoli trip"),
            memory("m2", "Old hotel", status="superseded"),
            memory("m3", "Budget 500 USD"),
        ),
        tool_results=(
            tool_result("t1", '{"temp": 31}', "get_current_weather"),
            tool_result("t2", '{"secret": true}', "unknown_tool"),
        ),
        current_turn=history("u1", "What is the weather?"),
        excluded_ids={"m1"},
        pipeline={"llm": {"provider": "openai_direct", "model": "gpt-6-luna", "language": "en"}},
    )

    assert compiled.included_ids == ("system", "m3", "h1", "t1", "u1")
    assert compiled.excluded_ids == frozenset({"m1"})
    assert "Tripoli trip" not in compiled.canonical_prompt
    assert "Old hotel" not in compiled.canonical_prompt
    assert "unknown_tool" not in compiled.canonical_prompt
    assert compiled.manifest["context_revision"] == 7
    assert compiled.manifest["turn_id"] == "u1"
    assert compiled.selection.revision == 7
    snapshot = compiled.to_snapshot()
    validate_assembly(7, compiled.manifest, snapshot)
    assert "excluded_record_ids" not in snapshot or snapshot["excluded_record_ids"] == ["m1"]


def test_stale_revision_or_hash_fails_closed():
    compiled = compile_turn_context(revision=2, instructions="Rules")
    snapshot = compiled.to_snapshot()
    with pytest.raises(ValueError, match="REVISION_MISMATCH_MANIFEST"):
        validate_assembly(3, compiled.manifest, snapshot)
    with pytest.raises(ValueError, match="REVISION_MISMATCH_SNAPSHOT"):
        validate_assembly(2, compiled.manifest, snapshot | {"revision": 1})
    with pytest.raises(ValueError, match="HASH_MISMATCH_SNAPSHOT"):
        validate_assembly(2, compiled.manifest, snapshot | {"canonical_prompt_sha256": "bad"})


@given(st.lists(st.tuples(st.uuids().map(str), st.text(min_size=1)), unique_by=lambda pair: pair[0], max_size=15))
def test_memory_and_tool_order_never_changes_the_hash(entries):
    memories = tuple(memory(record_id, text) for record_id, text in entries)
    tools = tuple(tool_result(record_id, text) for record_id, text in entries)
    forward = compile_turn_context(revision=4, instructions="Rules", memories=memories, tool_results=tools)
    backward = compile_turn_context(
        revision=4, instructions="Rules", memories=tuple(reversed(memories)), tool_results=tuple(reversed(tools))
    )
    assert forward.canonical_prompt_sha256 == backward.canonical_prompt_sha256
    assert forward.to_snapshot() == backward.to_snapshot()


def test_system_instructions_survive_small_budgets():
    compiled = compile_turn_context(
        revision=0,
        instructions="Protected rules",
        history=(history("h1", "x" * 500),),
        max_chars=20,
    )
    assert compiled.included_ids == ("system",)
    assert "Protected rules" in compiled.canonical_prompt


def configured(tmp_path):
    file = tmp_path / "context.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        _env_file=None,
    )
    return file, settings


def seed_memories(file, chat, memories, exclusions=()):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("INSERT OR IGNORE INTO development_budgets VALUES ('2026-11-12','openai',0)")
        for record_id, content, status in memories:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) "
                "VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', ?, 1, ?, ?, ?)",
                (record_id, chat, content, chat, status, "2026-11-12T00:00:00+00:00", "2026-11-12T00:00:00+00:00"),
            )
        for memory_id in exclusions:
            db.execute(
                "INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,revision_before,revision_after,idempotency_key,outcome,created_at) "
                "VALUES ('op-seed', (SELECT owner_id FROM conversations WHERE id=?), ?, 'seed', '{}', 0, 0, 'seed-key', 'applied', '2026-11-12T00:00:00+00:00')",
                (chat, chat),
            )
            db.execute(
                "INSERT INTO context_exclusions (id,owner_id,conversation_id,memory_id,operation_id,scope,active,created_at) "
                "VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, ?, 'op-seed', 'conversation', 1, '2026-11-12T00:00:00+00:00')",
                (f"ex-{memory_id}", chat, chat, memory_id),
            )
        db.commit()


def start_turn(client, chat, owner, file, memories, exclusions=(), text="What is the weather?"):
    with closing(sqlite3.connect(file)) as db, db:
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
    seed_memories(file, chat, memories, exclusions)
    headers = {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}
    response = client.post(
        "/internal/agent/turn/start", headers=headers, json={"request_id": "request-1", "text": text}
    )
    assert response.status_code == 200, response.text
    return headers, response.json()


def test_turn_snapshot_is_one_revision_with_exclusions_and_weather_policy(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers, result = start_turn(
            client,
            chat,
            owner,
            file,
            memories=(
                ("trip", "Trip to Tripoli", "active"),
                ("hotel", "Hotel in Rome", "active"),
                ("budget", "Budget 500 USD", "active"),
            ),
            exclusions=("trip",),
        )
        context = result["context"]
        assert context["revision"] == 0
        assert context["manifest"]["context_revision"] == context["revision"]
        assert "trip" not in context["included_record_ids"]
        assert "hotel" in context["included_record_ids"]
        assert context["excluded_record_ids"] == ["trip"]
        policy = context["weather_policy"]
        assert policy["excluded_memory_ids"] == ["trip"]
        assert policy["included_memory_ids"] == ["budget", "hotel"]
        assert context["canonical_prompt_sha256"] == context["manifest"]["canonical_prompt_sha256"]
        assert "Trip to Tripoli" not in json.dumps(context["messages"], ensure_ascii=False)


def test_weather_boundary_is_denied_for_excluded_and_allowed_for_explicit_or_included(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    calls = []

    async def fake_weather(city, country=None):
        calls.append((city, country))
        return {"city": city, "temperature": 31}

    monkeypatch.setattr(worker_module, "get_current_weather", fake_weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers, result = start_turn(
            client,
            chat,
            owner,
            file,
            memories=(("trip", "Trip to Tripoli", "active"), ("home", "Lives in Paris", "active")),
            exclusions=("trip",),
            text="What is the weather?",
        )
        turn_id = result["turn_id"]

        def weather(city, request_id, text):
            return client.post(
                "/internal/agent/tool/weather",
                headers=headers,
                json={"turn_id": turn_id, "request_id": request_id, "city": city},
            )

        denied = weather("Tripoli", "w1", "")
        assert denied.json() == {"error": "LOCATION_EXCLUDED"}
        assert calls == []

        allowed_memory = weather("Paris", "w2", "")
        assert allowed_memory.json()["city"] == "Paris"
        assert calls == [("Paris", None)]


def test_explicit_current_turn_city_overrides_an_excluded_memory(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    calls = []

    async def fake_weather(city, country=None):
        calls.append(city)
        return {"city": city}

    monkeypatch.setattr(worker_module, "get_current_weather", fake_weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers, result = start_turn(
            client,
            chat,
            owner,
            file,
            memories=(("trip", "Trip to Tripoli", "active"),),
            exclusions=("trip",),
            text="What is the weather in Tripoli?",
        )
        response = client.post(
            "/internal/agent/tool/weather",
            headers=headers,
            json={"turn_id": result["turn_id"], "request_id": "explicit-1", "city": "Tripoli"},
        )
        assert response.json()["city"] == "Tripoli"
        assert calls == ["Tripoli"]


def test_city_in_both_included_and_excluded_allowed_by_inclusion(tmp_path, monkeypatch):
    """A city appearing in both included and excluded sets is allowed (inclusion wins)."""
    file, settings = configured(tmp_path)
    calls = []

    async def fake_weather(city, country=None):
        calls.append((city, country))
        return {"city": city, "temperature": 31}

    monkeypatch.setattr(worker_module, "get_current_weather", fake_weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        # Add a memory, then exclude it, but also keep it in included via a bug/edge case
        # This simulates the case where a city ends up in both sets
        headers, result = start_turn(
            client,
            chat,
            owner,
            file,
            memories=(("trip", "Trip to Tripoli", "active"), ("home", "Lives in Paris", "active")),
            exclusions=("trip",),
            text="What is the weather?",
        )
        turn_id = result["turn_id"]

        # Manually add the excluded city to the included set in the weather_policy
        # to simulate the overlap edge case (this is a unit test of the decision logic)
        turn_snapshot = result["context"]
        if "weather_policy" in turn_snapshot:
            # We can't easily inject both via the API, so test the pure function directly
            from app.domain.weather_permissions import LocationEvidence, decide_weather_request

            trip_evidence = LocationEvidence("trip", "Trip to Tripoli")
            decision = decide_weather_request(
                city="Tripoli",
                country="LY",
                user_text="What is the weather?",
                included=(trip_evidence,),
                excluded=(trip_evidence,),
            )
            assert decision.allowed is True
            assert decision.reason is None
            assert decision.source == "memory"
            assert decision.memory_id == "trip"

        def weather(city, request_id, text):
            return client.post(
                "/internal/agent/tool/weather",
                headers=headers,
                json={"turn_id": turn_id, "request_id": request_id, "city": city},
            )

        # Normal excluded case still works
        denied = weather("Tripoli", "w1", "")
        assert denied.json() == {"error": "LOCATION_EXCLUDED"}
        assert calls == []
