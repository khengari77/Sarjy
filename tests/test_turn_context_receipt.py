"""User-safe per-turn context receipt: snapshot projection, exclusions and diff."""

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.domain.context import untrusted_data_prefix
from app.main import create_app as real_create_app
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st


class FakeEmbedder:
    async def embed(self, db, text):
        return (1.0,) + (0.0,) * 1535


def create_app(settings):
    return real_create_app(settings, embedding_adapter=FakeEmbedder())


def configured(tmp_path):
    file = tmp_path / "receipt.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


def sql(file, statement, params=()):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(statement, params)
        db.commit()


def add_memory(file, chat, memory_id, content, status="active", source_turn=None):
    sql(
        file,
        "INSERT INTO memories (id,owner_id,content,embedding_model,source_turn_id,source_conversation_id,version,status,"
        "created_at,updated_at) VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', "
        "?, ?, 1, ?, '2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
        (memory_id, chat, content, source_turn, chat, status),
    )


def add_session(file, chat, session_id="session", room="room"):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                chat,
                owner,
                room,
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                "{}",
            ),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": session_id, "x-room-name": room}


def start_turn(client, headers, request_id, text="How old am I?"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["turn_id"], payload["context"]


def receipt(client, chat, turn_id):
    return client.get(f"/v1/conversations/{chat}/turns/{turn_id}/context")


def setup(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def snapshot_of(file, turn_id):
    with closing(sqlite3.connect(file)) as db:
        raw = db.execute("SELECT snapshot FROM turns WHERE id=?", (turn_id,)).fetchone()[0]
    return json.loads(raw)


def test_receipt_shape_truncation_and_prompt_hash(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        long_text = "L" * 1000
        add_memory(file, chat, "long", long_text)
        headers = add_session(file, chat)
        turn_id, context = start_turn(client, headers, "r1")

        response = receipt(client, chat, turn_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_id"] == turn_id
        assert body["revision"] == context["revision"]
        # The digest covers only the receipt's own content, never the compiled prompt.
        assert body["prompt_sha256"] != context["canonical_prompt_sha256"][:12]
        assert len(body["prompt_sha256"]) == 12
        assert body["history_count"] == 0
        assert body["previous_turn_id"] is None
        assert body["budget"]["limit_chars"] == 16000
        assert body["budget"]["used_chars"] == sum(len(entry["text"]) for entry in body["included"])
        assert body["diff"] == {"added": sorted(context["included_record_ids"]), "removed": []}
        assert all(entry["id"] != "system" for entry in body["included"])

        memory = next(entry for entry in body["included"] if entry["id"] == "long")
        assert memory["kind"] == "memory" and memory["trust"] == "data"
        assert memory["text"] == long_text[:400]


def test_included_excluded_split_never_leaks_excluded_text(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "visible", "I like jazz")
        add_memory(file, chat, "hidden", "hidden-secret-123", status="hidden")
        add_memory(file, chat, "deleted", "deleted-secret-456", status="deleted")
        add_memory(file, chat, "excluded", "excluded-secret-789", status="hidden")
        # Make the excluded record active again, then exclude it through the real route.
        sql(file, "UPDATE memories SET status='active' WHERE id='excluded'")
        proposal = client.post(f"/v1/conversations/{chat}/context/exclusions", json={"memory_ids": ["excluded"]})
        assert proposal.status_code == 200, proposal.text
        applied = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={
                "proposal": proposal.json()["proposal"],
                "expected_revision": proposal.json()["revision"],
                "idempotency_key": "exclude-1",
            },
        )
        assert applied.status_code == 200, applied.text

        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")
        body = receipt(client, chat, turn_id).json()
        included_ids = {entry["id"] for entry in body["included"]}
        assert "visible" in included_ids
        assert not included_ids & {"hidden", "deleted", "excluded"}
        assert not any(entry_id.startswith("retraction:") for entry_id in included_ids)
        reasons = {entry["id"]: entry["reason"] for entry in body["excluded"]}
        assert reasons["hidden"] == "hidden"
        assert reasons["deleted"] == "deleted"
        assert reasons["excluded"] == "excluded_in_conversation"
        assert all(set(entry) == {"id", "reason"} for entry in body["excluded"])

        serialised = json.dumps(body, ensure_ascii=False)
        for secret in ("hidden-secret-123", "deleted-secret-456", "excluded-secret-789"):
            assert secret not in serialised

        # Hiding an already-included memory must redact its old snapshot text too.
        sql(file, "UPDATE memories SET status='hidden' WHERE id='visible'")
        later = json.dumps(receipt(client, chat, turn_id).json(), ensure_ascii=False)
        assert "I like jazz" not in later


def test_receipt_never_contains_instructions_or_provider_configuration(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "age", "I am 26 years old")
        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")
        body = receipt(client, chat, turn_id).json()
        serialised = json.dumps(body, ensure_ascii=False)

        snapshot = snapshot_of(file, turn_id)
        instructions = snapshot["messages"][0]["content"]
        assert instructions
        assert instructions not in serialised
        assert "pipeline_config" not in serialised
        assert "model_request" not in serialised
        llm = snapshot["pipeline_config"].get("llm", {})
        for value in (llm.get("provider"), llm.get("model")):
            if value:
                assert value not in serialised


def test_receipt_is_owner_and_conversation_isolated(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")

        other_chat = client.post("/v1/conversations").json()["id"]
        other_headers = add_session(file, other_chat, session_id="session-2", room="room-2")
        other_turn, _ = start_turn(client, other_headers, "r2", text="Another conversation")
        assert receipt(client, chat, other_turn).status_code == 404

        no_context_chat = client.post("/v1/conversations").json()["id"]
        sql(
            file,
            "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
            "VALUES ('bare',?,'bare','assistant','No snapshot','completed','2026-11-12T00:00:00+00:00',NULL)",
            (no_context_chat,),
        )
        bare = receipt(client, no_context_chat, "bare")
        assert bare.status_code == 404
        assert bare.json()["detail"]["code"] == "TURN_HAS_NO_CONTEXT"

    with TestClient(create_app(settings)) as other:
        other.post("/v1/demo-session")
        assert receipt(other, chat, turn_id).status_code == 404


def test_diff_against_previous_user_turn_after_save_and_hide(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "base", "I like tea")
        headers = add_session(file, chat)
        first, _ = start_turn(client, headers, "r1")

        add_memory(file, chat, "new", "I live in Benghazi")
        second, _ = start_turn(client, headers, "r2", text="Where do I live?")
        second_body = receipt(client, chat, second).json()
        assert second_body["previous_turn_id"] == first
        assert "new" in second_body["diff"]["added"]
        assert second in second_body["diff"]["added"]
        assert "base" not in second_body["diff"]["added"]
        assert "base" not in second_body["diff"]["removed"]
        by_id = {entry["id"]: entry for entry in second_body["included"]}
        assert by_id[first]["kind"] == "history" and by_id[first]["trust"] == "conversation"
        assert second_body["history_count"] == 1

        sql(file, "UPDATE memories SET status='hidden' WHERE id='new'")
        third, _ = start_turn(client, headers, "r3", text="Still?")
        third_body = receipt(client, chat, third).json()
        assert third_body["previous_turn_id"] == second
        assert "new" in third_body["diff"]["removed"]
        assert third in third_body["diff"]["added"]
        assert {"id": "new", "reason": "hidden"} in third_body["excluded"]


def test_assistant_turn_falls_back_to_the_preceding_user_snapshot(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "age", "I am 26 years old")
        headers = add_session(file, chat)
        user_turn, context = start_turn(client, headers, "r1")
        reply = client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": user_turn, "text": "You are 26.", "status": "completed"},
        )
        assert reply.status_code == 200, reply.text
        assistant_turn = reply.json()["id"]

        response = receipt(client, chat, assistant_turn)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_id"] == assistant_turn
        assert body["prompt_sha256"] != context["canonical_prompt_sha256"][:12] and len(body["prompt_sha256"]) == 12
        included_ids = {entry["id"] for entry in body["included"]}
        assert included_ids == {value for value in context["included_record_ids"] if value not in ("system", user_turn)}
        assert body["previous_turn_id"] is None


@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=40).map(
            lambda value: f"hidden{value}"
        ),
        min_size=1,
        max_size=3,
        unique=True,
    )
)
@hypothesis_settings(max_examples=8, deadline=None)
def test_any_hidden_memory_content_never_appears(contents):
    with tempfile.TemporaryDirectory() as directory:
        file, settings = configured(Path(directory))
        with TestClient(create_app(settings)) as client:
            chat = setup(client)
            for index, content in enumerate(contents):
                add_memory(file, chat, f"hidden-{index}", content)
            headers = add_session(file, chat)
            turn_id, _ = start_turn(client, headers, "r1", text="What do you know?")
            # The memories are in the compiled snapshot; hiding them afterwards must blank them in the receipt.
            assert any(content in json.dumps(snapshot_of(file, turn_id)) for content in contents)
            sql(file, "UPDATE memories SET status='hidden'")
            body = receipt(client, chat, turn_id).json()
            serialised = json.dumps(body, ensure_ascii=False)
            for content in contents:
                assert content not in serialised
            assert {entry["id"] for entry in body["included"]} >= {f"hidden-{i}" for i in range(len(contents))}


def test_a_turn_dropped_for_restating_a_forgotten_fact_says_so(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        restating, _ = start_turn(client, headers, "r0", text="Benghazi is where I live")
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        reasons = {entry["id"]: entry["reason"] for entry in receipt(client, chat, later).json()["excluded"]}
        assert reasons["home"] == "hidden"
        assert reasons[restating] == "restates_forgotten_fact"
        assert "Benghazi" not in json.dumps(receipt(client, chat, later).json())


def test_a_memory_hidden_after_the_turn_is_blanked_and_its_restating_turn_too(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        restating, _ = start_turn(client, headers, "r0", text="Benghazi is where I live")
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        before = receipt(client, chat, later).json()
        assert {"home", restating} <= {entry["id"] for entry in before["included"]}
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        body = receipt(client, chat, later).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries["home"]["text"] == "" and entries[restating]["text"] == ""
        assert "Benghazi" not in json.dumps(body)
        # Restoring the memory restores what the receipt may show.
        sql(file, "UPDATE memories SET status='active' WHERE id='home'")
        restored = {entry["id"]: entry for entry in receipt(client, chat, later).json()["included"]}
        assert restored["home"]["text"] == "I live in Benghazi"
        assert restored[restating]["text"] == "Benghazi is where I live"


def test_the_receipt_carries_no_digest_or_length_of_the_hidden_prompt(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        turn_id, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        snapshot = snapshot_of(file, turn_id)
        body = receipt(client, chat, turn_id).json()
        assert body["prompt_sha256"] != str(snapshot.get("canonical_prompt_sha256", ""))[:12]
        # The digest and the character count describe only what the receipt returns.
        assert body["budget"]["used_chars"] == sum(len(entry["text"]) for entry in body["included"])
        assert receipt(client, chat, turn_id).json()["prompt_sha256"] == body["prompt_sha256"]
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        assert receipt(client, chat, turn_id).json()["prompt_sha256"] != body["prompt_sha256"]


def test_an_unknown_turn_is_404_and_a_signed_out_caller_is_401(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")
        missing = receipt(client, chat, "no-such-turn")
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "TURN_NOT_FOUND"
    with TestClient(create_app(settings)) as stranger:
        assert receipt(stranger, chat, turn_id).status_code == 401


def test_a_restored_forgotten_fact_stops_being_reported_as_restating(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        restating, _ = start_turn(client, headers, "r0", text="Benghazi is where I live")
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        reasons = {entry["id"]: entry["reason"] for entry in receipt(client, chat, later).json()["excluded"]}
        assert reasons[restating] == "restates_forgotten_fact"
        sql(file, "UPDATE memories SET status='active' WHERE id='home'")
        reasons = {entry["id"]: entry["reason"] for entry in receipt(client, chat, later).json()["excluded"]}
        assert reasons[restating] == "derived"


def test_a_memory_derived_from_a_hidden_one_is_blanked_too(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "old", "I live in Benghazi")
        add_memory(file, chat, "new", "I live in Benghazi near the sea")
        turn_id, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        before = {entry["id"]: entry for entry in receipt(client, chat, turn_id).json()["included"]}
        assert before["new"]["text"] and before["old"]["text"]
        sql(file, "UPDATE memories SET supersedes_id='old' WHERE id='new'")
        sql(file, "UPDATE memories SET status='hidden' WHERE id='old'")
        body = receipt(client, chat, turn_id).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries["old"]["text"] == "" and entries["new"]["text"] == ""
        assert "Benghazi" not in json.dumps(body)


def test_blocked_records_of_every_kind_are_blanked(tmp_path):
    from app.services.turn_context_receipt import _included_entries

    # A tool result the live policy blocks must lose its text just like a memory or a turn.
    snapshot = {
        "included_record_ids": ["system", "call-1", "turn-0"],
        "messages": [
            {"role": "system", "content": "rules"},
            {
                "role": "user",
                "content": untrusted_data_prefix("tool", "call-1") + json.dumps({"text": "secret result"}),
            },
            {"role": "user", "content": "an old turn"},
        ],
        "manifest": {"turn_id": "now"},
    }
    entries = _included_entries(snapshot, {"call-1"}, turn_ids={"turn-0"})
    by_id = {entry["id"]: entry for entry in entries}
    assert by_id["call-1"]["kind"] == "tool" and by_id["call-1"]["text"] == ""
    assert by_id["turn-0"]["text"] == "an old turn"


def test_a_tool_result_quoting_a_forgotten_fact_is_blanked(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        # The tool result's own turn predates the memory, so only the text can connect them.
        first, _ = start_turn(client, headers, "r0", text="What is the weather like?")
        add_memory(file, chat, "home", "I live in Benghazi")
        sql(
            file,
            "INSERT INTO tool_calls (id,turn_id,request_id,name,state,result) "
            "VALUES ('call1',?,'call1','get_current_weather','completed',?)",
            (first, json.dumps({"city": "Benghazi", "temp_c": 21})),
        )
        later, _ = start_turn(client, headers, "r1", text="And tomorrow?")
        before = {entry["id"]: entry for entry in receipt(client, chat, later).json()["included"]}
        assert "Benghazi" in before["call1"]["text"] and before["call1"]["withheld"] is False

        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        body = receipt(client, chat, later).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries["call1"]["text"] == "" and entries["call1"]["withheld"] is True
        assert "Benghazi" not in json.dumps(body)


def test_a_restating_turn_beyond_the_lens_window_is_still_blanked(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        restating, _ = start_turn(client, headers, "r0", text="Benghazi is where I live")
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        # More than the lens window (100 turns) now sits after this snapshot.
        for index in range(110):
            sql(
                file,
                "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    f"x{index}",
                    chat,
                    f"x{index}",
                    "user",
                    "Nothing to see",
                    "completed",
                    f"2099-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
                ),
            )
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        body = receipt(client, chat, later).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries[restating]["text"] == "" and entries[restating]["withheld"] is True
        assert "Benghazi" not in json.dumps(body)


def _seed_old_exchange(file, chat, *, user_text, reply_text, reply_snapshot, user_snapshot=None):
    for turn_id, role, text, at, request, snapshot in (
        ("old-user", "user", user_text, "2026-11-12T00:00:01+00:00", "old-user", user_snapshot),
        ("old-reply", "assistant", reply_text, "2026-11-12T00:00:02+00:00", "reply:old-user", reply_snapshot),
    ):
        sql(
            file,
            "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (turn_id, chat, request, role, text, "completed", at, json.dumps(snapshot) if snapshot else None),
        )


def _push_out_of_the_lens_window(file, chat):
    for index in range(110):
        sql(
            file,
            "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                f"x{index}",
                chat,
                f"x{index}",
                "user",
                "Nothing to see",
                "completed",
                f"2099-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            ),
        )


def test_a_reply_derived_from_a_hidden_memory_is_blanked_beyond_the_lens_window(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "color", "My favorite color is blue")
        # The production shape: the parent prompt carried the memory, so the reply's derivation lists its id, while
        # neither turn shares enough words with the fact to count as a restatement.
        _seed_old_exchange(
            file,
            chat,
            user_text="What shade do I like most?",
            reply_text="You like cerulean best.",
            reply_snapshot={"derivation": {"turn_id": "old-user", "record_ids": ["system", "color", "old-user"]}},
            # Known provenance for the parent that does NOT list the memory: only the derivation can connect them.
            user_snapshot={"included_record_ids": ["system", "old-user"]},
        )
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        _push_out_of_the_lens_window(file, chat)
        before = {entry["id"]: entry for entry in receipt(client, chat, later).json()["included"]}
        assert before["old-reply"]["text"] == "You like cerulean best."

        sql(file, "UPDATE memories SET status='hidden' WHERE id='color'")
        body = receipt(client, chat, later).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries["old-reply"]["text"] == "" and entries["old-reply"]["withheld"] is True
        assert "cerulean" not in json.dumps(body)


def test_a_reply_of_unknown_provenance_fails_closed_beyond_the_lens_window(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "color", "My favorite color is blue")
        # No derivation and a parent with no snapshot: nothing says what the reply was built from.
        _seed_old_exchange(
            file,
            chat,
            user_text="What shade do I like most?",
            reply_text="You like cerulean best.",
            reply_snapshot=None,
        )
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        _push_out_of_the_lens_window(file, chat)
        sql(file, "UPDATE memories SET status='hidden' WHERE id='color'")
        entries = {entry["id"]: entry for entry in receipt(client, chat, later).json()["included"]}
        assert entries["old-reply"]["text"] == "" and entries["old-reply"]["withheld"] is True


def test_a_reply_derived_through_an_ancestor_outside_the_snapshot_is_blanked(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "color", "My favorite color is blue")
        # R was built from the memory; A was built from a prompt containing R (not the memory). The later turn's
        # snapshot contains A but not R, so only following A -> R -> memory reaches the forgotten fact.
        for turn_id, role, text, at, request, snapshot in (
            (
                "q1",
                "user",
                "What shade do I like most?",
                "2026-11-12T00:00:01+00:00",
                "q1",
                {"included_record_ids": ["system", "color", "q1"]},
            ),
            (
                "r1",
                "assistant",
                "You like cerulean best.",
                "2026-11-12T00:00:02+00:00",
                "reply:q1",
                {"derivation": {"turn_id": "q1", "record_ids": ["system", "color", "q1"]}},
            ),
            (
                "q2",
                "user",
                "And what else is there?",
                "2026-11-12T00:00:03+00:00",
                "q2",
                {"included_record_ids": ["system", "r1", "q2"]},
            ),
            (
                "a1",
                "assistant",
                "Mostly sea shades, as you said.",
                "2026-11-12T00:00:04+00:00",
                "reply:q2",
                {"derivation": {"turn_id": "q2", "record_ids": ["system", "r1", "q2"]}},
            ),
        ):
            sql(
                file,
                "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (turn_id, chat, request, role, text, "completed", at, json.dumps(snapshot)),
            )
        later, _ = start_turn(client, headers, "r9", text="What is the weather like?")
        # Make the compiled snapshot of the later turn carry A without R, as an older conversation would.
        snap = snapshot_of(file, later)
        keep = [
            (i, m)
            for i, m in zip(snap["included_record_ids"], snap["messages"], strict=False)
            if i not in ("r1", "q1", "q2")
        ]
        snap["included_record_ids"] = [i for i, _ in keep]
        snap["messages"] = [m for _, m in keep]
        sql(file, "UPDATE turns SET snapshot=? WHERE id=?", (json.dumps(snap), later))
        _push_out_of_the_lens_window(file, chat)
        before = {entry["id"]: entry for entry in receipt(client, chat, later).json()["included"]}
        assert before["a1"]["text"] == "Mostly sea shades, as you said."

        sql(file, "UPDATE memories SET status='hidden' WHERE id='color'")
        body = receipt(client, chat, later).json()
        entries = {entry["id"]: entry for entry in body["included"]}
        assert entries["a1"]["text"] == "" and entries["a1"]["withheld"] is True
