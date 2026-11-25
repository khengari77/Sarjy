"""Auto-save without a question, the undo journal, and the server-side policy re-check."""

import sqlite3
from contextlib import closing

import pytest
from app.domain.memory_capture import parse_capture
from app.services.memory_capture import proposal_for_capture
from fastapi.testclient import TestClient
from test_memory_capture import memory_rows, turn
from test_memory_mode_routes import set_mode, started

AUTO = "/internal/agent/memory/auto"
PROPOSE = "/internal/agent/tool/memory-propose"
PENDING = "memory candidate is pending"


def auto(client, headers, token):
    return client.post(AUTO, headers=headers, json={"token": token})


def operations(file):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT command_type, outcome FROM context_operations").fetchall()


def memory_by_id(file, memory_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute(
            "SELECT status, content, subject, version FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()


def proposal_of(client, headers, text, request_id):
    body = turn(client, headers, text, request_id).json()
    proposal = body["memory_proposal"]
    assert proposal is not None, body
    return proposal


def save_via_auto(client, headers, text, request_id):
    proposal = proposal_of(client, headers, text, request_id)
    response = auto(client, headers, proposal["token"])
    assert response.status_code == 200, response.text
    return response.json()["change"]


def active_contents(file):
    with closing(sqlite3.connect(file)) as db:
        return [row[0] for row in db.execute("SELECT content FROM memories WHERE status = 'active'")]


def operation_payloads(file):
    with closing(sqlite3.connect(file)) as db:
        return [row[0] for row in db.execute("SELECT payload FROM context_operations")]


def session_identity(file):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT owner_id, conversation_id FROM voice_sessions LIMIT 1").fetchone()


@pytest.mark.parametrize("mode", ["auto", None])
def test_auto_saves_an_ordinary_fact_without_a_question(tmp_path, mode):
    sub = tmp_path / (mode or "default")
    sub.mkdir()
    cm, client, file, headers = started(sub, mode)
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        assert proposal["plan"] == "save"

        response = auto(client, headers, proposal["token"])
        assert response.status_code == 200, response.text
        change = response.json()["change"]
        assert change["id"]
        assert change["kind"] == "memory_saved"
        assert change["label"].startswith("Remembered:")
        assert change["undoable"] is True
        assert change["memory_id"]

        rows = memory_rows(file)
        assert [(content, status) for content, _, status, _ in rows] == [("My name is Sam", "active")]
        assert operations(file) == [("memory_auto_save", "applied")]
    finally:
        cm.__exit__(None, None, None)


def test_replaying_the_same_token_returns_the_same_change_and_writes_once(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        first = auto(client, headers, proposal["token"])
        second = auto(client, headers, proposal["token"])
        assert first.status_code == 200 and second.status_code == 200, second.text
        assert second.json()["change"]["id"] == first.json()["change"]["id"]
        assert second.json()["change"]["memory_id"] == first.json()["change"]["memory_id"]
        assert len(memory_rows(file)) == 1
        assert operations(file) == [("memory_auto_save", "applied")]
    finally:
        cm.__exit__(None, None, None)


def test_a_conflicting_fact_replaces_the_earlier_one_in_auto(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        first = save_via_auto(client, headers, "My name is Sam", "fact-1")
        second = save_via_auto(client, headers, "My name is Alex", "fact-2")

        assert second["kind"] == "memory_updated"
        assert second["label"].startswith("Updated:")
        assert second["memory_id"] != first["memory_id"]
        statuses = {content: status for content, _, status, _ in memory_rows(file)}
        assert statuses == {"My name is Sam": "superseded", "My name is Alex": "active"}
    finally:
        cm.__exit__(None, None, None)


def test_undoing_a_plain_save_tombstones_the_memory_and_is_idempotent(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        change = save_via_auto(client, headers, "My name is Sam", "fact-1")

        response = client.post(f"/v1/changes/{change['id']}/undo")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changed"] is True
        assert body["undoable"] is False

        row = memory_by_id(file, change["memory_id"])
        assert row[0] == "deleted" and row[1] == ""
        assert row[2] is None
        assert operations(file) == [("memory_auto_save", "undone")]

        again = client.post(f"/v1/changes/{change['id']}/undo")
        assert again.status_code == 200, again.text
        assert again.json()["changed"] is False
        assert again.json()["undoable"] is False
    finally:
        cm.__exit__(None, None, None)


def test_undoing_an_update_restores_the_earlier_fact(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        first = save_via_auto(client, headers, "My name is Sam", "fact-1")
        second = save_via_auto(client, headers, "My name is Alex", "fact-2")

        response = client.post(f"/v1/changes/{second['id']}/undo")
        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True

        assert memory_by_id(file, second["memory_id"])[0] == "deleted"
        restored = memory_by_id(file, first["memory_id"])
        assert restored[0] == "active" and restored[1] == "My name is Sam"
        assert [content for content, _, status, _ in memory_rows(file) if status == "active"] == ["My name is Sam"]
    finally:
        cm.__exit__(None, None, None)


def test_undo_refuses_when_the_memory_moved_on(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        change = save_via_auto(client, headers, "My name is Sam", "fact-1")
        hidden = client.post(f"/v1/memories/{change['memory_id']}/hide")
        assert hidden.status_code == 200 and hidden.json()["status"] == "hidden"

        response = client.post(f"/v1/changes/{change['id']}/undo")
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "CHANGE_SUPERSEDED"

        row = memory_by_id(file, change["memory_id"])
        assert row[0] == "hidden" and row[1] == "My name is Sam"
        assert operations(file) == [("memory_auto_save", "applied")]
    finally:
        cm.__exit__(None, None, None)


def test_another_owner_cannot_undo_the_change(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        change = save_via_auto(client, headers, "My name is Sam", "fact-1")

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            response = other.post(f"/v1/changes/{change['id']}/undo")
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "CHANGE_NOT_FOUND"

        assert memory_by_id(file, change["memory_id"])[0] == "active"
    finally:
        cm.__exit__(None, None, None)


def test_the_auto_route_refuses_a_plan_that_still_needs_consent(tmp_path):
    cm, client, file, headers = started(tmp_path, "ask")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        assert proposal["plan"] == "ask"

        response = auto(client, headers, proposal["token"])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "MEMORY_NEEDS_CONSENT"
        assert memory_rows(file) == []
        assert operations(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_the_auto_route_refuses_work_while_memory_is_off(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        with closing(sqlite3.connect(file)) as db:
            owner = db.execute("SELECT owner_id FROM voice_sessions LIMIT 1").fetchone()[0]
        set_mode(file, owner, "off")

        response = auto(client, headers, proposal["token"])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "MEMORY_OFF"
        assert memory_rows(file) == []
        assert operations(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_a_credential_is_never_auto_saved(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "my password is hunter2", "secret-1")
        assert proposal["plan"] == "skip"

        response = auto(client, headers, proposal["token"])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "MEMORY_NEEDS_CONSENT"
        assert memory_rows(file) == []
        assert operations(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_an_ordinary_fact_plan_is_save_in_auto(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        assert proposal["plan"] == "save"
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_a_health_fact_proposed_by_the_tool_plans_to_ask_in_auto(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        started_turn = turn(client, headers, "hello there", "t1").json()
        response = client.post(
            PROPOSE,
            headers=headers,
            json={
                "turn_id": started_turn["turn_id"],
                "request_id": "propose-1",
                "fact": "my doctor put me on metformin",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "proposed"
        assert body["proposal"]["plan"] == "ask"
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_an_ordinary_fact_plan_is_ask_in_ask_mode(tmp_path):
    cm, client, file, headers = started(tmp_path, "ask")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        assert proposal["plan"] == "ask"
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_only_the_ask_plan_appends_the_pending_question_instruction(tmp_path):
    auto_dir = tmp_path / "auto"
    auto_dir.mkdir()
    cm, client, file, headers = started(auto_dir, "auto")
    try:
        saved = turn(client, headers, "My name is Sam", "fact-1").json()
        assert saved["memory_proposal"]["plan"] == "save"
        assert PENDING not in saved["context"]["model_request"]["instructions"].lower()
    finally:
        cm.__exit__(None, None, None)

    ask_dir = tmp_path / "ask"
    ask_dir.mkdir()
    cm, client, file, headers = started(ask_dir, "ask")
    try:
        asked = turn(client, headers, "My name is Sam", "fact-1").json()
        assert asked["memory_proposal"]["plan"] == "ask"
        assert PENDING in asked["context"]["model_request"]["instructions"].lower()
    finally:
        cm.__exit__(None, None, None)


def test_undo_is_refused_when_a_newer_auto_save_replaced_the_row(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        save_via_auto(client, headers, "My name is Sam", "fact-1")
        alex = save_via_auto(client, headers, "My name is Alex", "fact-2")
        adam = save_via_auto(client, headers, "My name is Adam", "fact-3")
        assert alex["memory_id"] != adam["memory_id"]

        refused = client.post(f"/v1/changes/{alex['id']}/undo")
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "CHANGE_SUPERSEDED"
        assert active_contents(file) == ["My name is Adam"]
        assert {content: status for content, _, status, _ in memory_rows(file)} == {
            "My name is Sam": "superseded",
            "My name is Alex": "superseded",
            "My name is Adam": "active",
        }

        undone = client.post(f"/v1/changes/{adam['id']}/undo")
        assert undone.status_code == 200, undone.text
        assert undone.json()["changed"] is True
        assert active_contents(file) == ["My name is Alex"]
    finally:
        cm.__exit__(None, None, None)


def test_undo_is_refused_after_the_memory_was_permanently_deleted(tmp_path):
    # SOURCE BUG: delete_memory scrubs the journal payload down to {"memory_id", "scrubbed"} and marks the
    # operation "deleted", but undo_change still reads payload["version"] before checking "undoable"/"scrubbed".
    # The request therefore raises KeyError (HTTP 500) instead of the specified 409 CHANGE_SUPERSEDED.
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        change = save_via_auto(client, headers, "My name is Sam", "fact-1")
        deleted = client.delete(f"/v1/memories/{change['memory_id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "deleted"

        response = client.post(f"/v1/changes/{change['id']}/undo")
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "CHANGE_SUPERSEDED"

        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0] == 0
    finally:
        cm.__exit__(None, None, None)


def test_deleting_a_memory_scrubs_the_auto_save_journal_label(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        saved = auto(client, headers, proposal["token"])
        assert saved.status_code == 200, saved.text
        memory_id = saved.json()["change"]["memory_id"]
        assert "Sam" in saved.json()["change"]["label"]
        assert "Sam" in operation_payloads(file)[0]

        deleted = client.delete(f"/v1/memories/{memory_id}")
        assert deleted.status_code == 200, deleted.text

        replay = auto(client, headers, proposal["token"])
        assert replay.status_code == 200, replay.text
        change = replay.json()["change"]
        assert change is not None
        assert "Sam" not in change["label"]
        assert change["undoable"] is False

        payloads = operation_payloads(file)
        assert len(payloads) == 1
        assert all("Sam" not in (payload or "") for payload in payloads)
    finally:
        cm.__exit__(None, None, None)


def test_negated_fact_is_never_auto_saved(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        saved = save_via_auto(client, headers, "I live in Benghazi", "fact-1")
        assert saved["kind"] == "memory_saved"

        proposal = proposal_of(client, headers, "I no longer live in Benghazi", "fact-2")
        assert proposal["negated"] is True
        assert proposal["plan"] == "ask"

        refused = auto(client, headers, proposal["token"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "MEMORY_NEEDS_CONSENT"

        assert active_contents(file) == ["I live in Benghazi"]
        assert operations(file) == [("memory_auto_save", "applied")]
    finally:
        cm.__exit__(None, None, None)


def test_a_credential_cannot_be_confirmed_even_in_ask_mode(tmp_path):
    cm, client, file, headers = started(tmp_path, "ask")
    try:
        started_turn = turn(client, headers, "hello there", "t0").json()
        owner_id, conversation_id = session_identity(file)
        capture = parse_capture("my password is hunter2")
        assert capture is not None
        assert capture.subject == "attribute:password"
        proposal = proposal_for_capture(
            capture,
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=started_turn["turn_id"],
            secret="proposal-secret",
        )

        refused = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": proposal["token"], "decision": "save"},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "MEMORY_SENSITIVE"
        assert memory_rows(file) == []
        assert operations(file) == []

        discarded = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": proposal["token"], "decision": "discard"},
        )
        assert discarded.status_code == 200, discarded.text
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)
