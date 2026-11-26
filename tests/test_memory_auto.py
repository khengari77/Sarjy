"""Auto-save without a question, the undo journal, and the server-side policy re-check."""

import base64
import json
import sqlite3
from contextlib import closing

import pytest
from app.domain.context_operations import sign_proposal
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


def test_a_racing_replay_of_one_token_returns_the_same_change_instead_of_a_500(tmp_path, monkeypatch):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")

        from app.services import memory_auto

        real_journalled = memory_auto._journalled
        calls = 0

        async def blind_for_both_checks(db, owner_id, key):
            # Both requests passed the early check before either committed its journal row. The first four checks
            # are the two early checks and the two re-checks under the owner lock; only the last re-read sees the row.
            nonlocal calls
            calls += 1
            if calls <= 4:
                return None
            return await real_journalled(db, owner_id, key)

        monkeypatch.setattr(memory_auto, "_journalled", blind_for_both_checks)
        first = auto(client, headers, proposal["token"])
        second = auto(client, headers, proposal["token"])
        assert first.status_code == 200 and second.status_code == 200, second.text
        assert second.json()["change"]["id"] == first.json()["change"]["id"]
        assert second.json()["change"]["memory_id"] == first.json()["change"]["memory_id"]
        assert count_rows(file, "context_operations") == 1
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


def forged_proposal(client, headers, file, text, request_id="forge-0"):
    """A validly signed proposal for ``text``, minted directly (a credential never yields one through the app)."""
    started_turn = turn(client, headers, "hello there", request_id).json()
    owner_id, conversation_id = session_identity(file)
    capture = parse_capture(text)
    assert capture is not None, text
    return proposal_for_capture(
        capture,
        owner_id=owner_id,
        conversation_id=conversation_id,
        source_turn_id=started_turn["turn_id"],
        secret="proposal-secret",
    )


def without_negated_claim(token, secret="proposal-secret"):
    """Re-sign ``token`` without its ``negated`` claim, as a token minted before the claim existed."""
    body = token.rsplit(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert "negated" in payload
    del payload["negated"]
    return sign_proposal(payload, secret)


def count_rows(file, table):
    with closing(sqlite3.connect(file)) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_credential_creates_no_proposal_and_is_never_recorded(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        body = turn(client, headers, "remember that my password is hunter2", "secret-1").json()
        assert body["memory_proposal"] is None
        assert "sensitive" in body["context"]["model_request"]["instructions"].lower()
        assert count_rows(file, "memory_write_requests") == 0
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_the_propose_tool_rejects_a_credential_before_recording_anything(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        started_turn = turn(client, headers, "hello there", "t1").json()
        body = client.post(
            PROPOSE,
            headers=headers,
            json={"turn_id": started_turn["turn_id"], "request_id": "p1", "fact": "my password is hunter2"},
        ).json()
        assert body["status"] == "rejected" and body["error"] == "MEMORY_SENSITIVE"
        assert "proposal" not in body
        assert count_rows(file, "memory_write_requests") == 0
        assert count_rows(file, "tool_calls") <= 1  # at most the rejected call itself, never the secret as a proposal
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_a_forged_credential_token_is_refused_by_the_auto_route(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        proposal = forged_proposal(client, headers, file, "my password is hunter2")
        response = auto(client, headers, proposal["token"])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "MEMORY_NEEDS_CONSENT"
        assert memory_rows(file) == []
        assert operations(file) == []
    finally:
        cm.__exit__(None, None, None)


CREDENTIALS = [
    "my password is hunter2",
    "my wifi password is x",
    "my pin code is 1234",
    "my ssh key is AAAAB3Nza",
    "my api key is abc",
    "my api secret is xyz",
    "my private key is ---",
    "my social security number is 123-45-6789",
    "my backup code is 99887766",
    "my recovery phrase is apple banana",
    "my pin is 1234",
    "my passport number is X1234567",
    "the safe combination is 12-34-56",
    "my 2fa code is 445566",
    "my security codes are 778 and 991",
    "my passwords are hunter2 and swordfish",
]
# Out of the scoped floor on purpose (judged by the background observer, not by a growing regex):
# "my github token is ...", "my login is ...", "the code to my safe is ...".
ORDINARY = [
    "my secret to good coffee is fresh beans",
    "my favourite book is The Secret",
    "I collect pin badges",
    "I have a token for the bus",
    "I forgot my login page url",
    "my name is Sam",
    "I live in Tripoli",
    "I like green tea",
    "my login screen is blue",
    "I have a secret garden",
    "I pin my notes to the board",
    "my passport is blue",
    "I use 2fa on every account",
]


def test_the_credential_net_catches_credentials_and_leaves_ordinary_facts_alone(tmp_path):
    from app.domain.memory_policy import Decision, plan_for_proposal

    for text in CREDENTIALS:
        assert plan_for_proposal("auto", text, explicit=True) is Decision.SKIP, text
    for text in ORDINARY:
        assert plan_for_proposal("auto", text, explicit=True) is Decision.SAVE, text


def test_a_parser_negation_asks_and_free_form_negation_is_left_to_the_observer():
    from app.domain.memory_policy import Decision, plan_for_proposal

    assert plan_for_proposal("auto", "I no longer live in Benghazi", explicit=True, negated=True) is Decision.ASK
    # No word list guesses at negation any more ("I'm not vegetarian" is the observer's call).
    assert plan_for_proposal("auto", "I live in Benghazi", explicit=True) is Decision.SAVE


def test_a_token_without_the_negated_claim_cannot_auto_save_a_negation(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        save_via_auto(client, headers, "I live in Benghazi", "fact-0")
        proposal = forged_proposal(client, headers, file, "I no longer live in Benghazi", "forge-1")
        response = auto(client, headers, without_negated_claim(proposal["token"]))
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "MEMORY_NEEDS_CONSENT"
    finally:
        cm.__exit__(None, None, None)


def test_undo_is_refused_when_another_fact_for_the_subject_is_now_active(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        save_via_auto(client, headers, "I like coffee", "fact-1")
        second = save_via_auto(client, headers, "I like tea", "fact-2")
        # Same parsed value as the second fact: saved beside it instead of replacing it.
        save_via_auto(client, headers, "I love tea", "fact-3")
        active_before = active_contents(file)
        undone = client.post(f"/v1/changes/{second['id']}/undo")
        assert undone.status_code == 409, undone.text
        assert undone.json()["detail"]["code"] == "CHANGE_SUPERSEDED"
        assert active_contents(file) == active_before
    finally:
        cm.__exit__(None, None, None)


def test_policy_plans_for_ordinary_health_and_ask_mode_facts():
    from app.domain.memory_policy import Decision, plan_for_proposal

    assert plan_for_proposal("auto", "My name is Sam", explicit=True) is Decision.SAVE
    assert plan_for_proposal("auto", "my doctor put me on metformin", explicit=False) is Decision.ASK
    assert plan_for_proposal("ask", "My name is Sam", explicit=True) is Decision.ASK


def test_turn_start_carries_no_plan_and_keeps_the_pending_question_as_on_main(tmp_path):
    # The save decision moves to the background observer; turn start is unchanged from main.
    for mode in ("auto", "ask"):
        sub = tmp_path / mode
        sub.mkdir()
        cm, client, file, headers = started(sub, mode)
        try:
            body = turn(client, headers, "My name is Sam", "fact-1").json()
            assert "plan" not in body["memory_proposal"]
            assert PENDING in body["context"]["model_request"]["instructions"].lower()
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
    # delete_memory scrubs the journal payload down to {"memory_id", "scrubbed"}; undo must answer 409
    # CHANGE_SUPERSEDED, not crash reading the missing version.
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


def turn_snapshots(file):
    with closing(sqlite3.connect(file)) as db:
        return [row[0] for row in db.execute("SELECT snapshot FROM turns") if row[0]]


def _copies(file):
    """Where each table that can carry a copy of a fact still mentions it."""
    with closing(sqlite3.connect(file)) as db:
        copies = {
            table: any("Sam" in str(value) for row in db.execute(f"SELECT * FROM {table}") for value in row)
            for table in ("memory_write_requests", "memory_capture_decisions", "context_operations")
        }
    copies["turns"] = any("Sam" in snapshot for snapshot in turn_snapshots(file))
    return copies


def test_undo_scrubs_the_proposal_decision_and_prompt_copies_so_a_later_delete_has_nothing_left(tmp_path):
    # Undo tombstones the row, so a later permanent delete returns early; the copies must already be gone.
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        # Propose from a turn that does not itself say the fact, so the transcript holds no "Sam" and the only
        # snapshot copy is the one a later turn compiles from the saved memory.
        proposal = forged_proposal(client, headers, file, "My name is Sam", "fact-1")
        saved = auto(client, headers, proposal["token"])
        assert saved.status_code == 200, saved.text
        change = saved.json()["change"]
        turn(client, headers, "what do you know about me", "fact-2")

        assert _copies(file)["memory_write_requests"]
        assert any("Sam" in snapshot for snapshot in turn_snapshots(file))

        assert client.post(f"/v1/changes/{change['id']}/undo").status_code == 200
        assert client.delete(f"/v1/memories/{change['memory_id']}").status_code == 200

        assert all("Sam" not in snapshot for snapshot in turn_snapshots(file))
        assert _copies(file) == {
            "memory_write_requests": False,
            "memory_capture_decisions": False,
            "context_operations": False,
            "turns": False,
        }
    finally:
        cm.__exit__(None, None, None)
