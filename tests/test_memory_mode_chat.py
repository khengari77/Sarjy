"""Per-chat memory mode: the override, its write-time checks and its HTTP surface."""

import sqlite3
from contextlib import closing

from app.main import create_app
from app.services.memory_mode import effective_memory_mode
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, configured, memory_rows, turn
from test_memory_mode_routes import started

AUTO = "/internal/agent/memory/auto"
PROPOSE = "/internal/agent/tool/memory-propose"
CONFIRM = "/internal/agent/memory/confirm"


def auto(client, headers, token):
    return client.post(AUTO, headers=headers, json={"token": token})


def only_chat(client):
    return client.get("/v1/conversations").json()[0]["id"]


def set_chat_mode(client, conversation_id, mode):
    response = client.put(f"/v1/conversations/{conversation_id}/memory-mode", json={"mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def proposal_of(client, headers, text, request_id):
    body = turn(client, headers, text, request_id).json()
    proposal = body["memory_proposal"]
    assert proposal is not None, body
    return proposal


def account_mode(file):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT memory_mode FROM user_settings LIMIT 1").fetchone()[0]


def test_effective_mode_matrix_prefers_the_chat_then_falls_back_and_fails_closed():
    assert effective_memory_mode("off", "auto") == "off"
    assert effective_memory_mode("auto", "off") == "auto"
    assert effective_memory_mode(None, "off") == "off"
    assert effective_memory_mode(None, "ask") == "ask"
    assert effective_memory_mode(None, None) == "auto"
    assert effective_memory_mode("garbage", "auto") == "off"
    assert effective_memory_mode(None, "garbage") == "off"
    assert effective_memory_mode("garbage", "garbage") == "off"


def test_chat_off_blocks_propose_auto_and_confirm_but_leaves_the_account_auto(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        chat = only_chat(client)
        armed = proposal_of(client, headers, "My name is Sam", "fact-1")
        set_chat_mode(client, chat, "off")

        refused = auto(client, headers, armed["token"])
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "MEMORY_OFF"
        confirmed = client.post(CONFIRM, headers=headers, json={"token": armed["token"], "decision": "save"})
        assert confirmed.status_code == 409 and confirmed.json()["detail"]["code"] == "MEMORY_OFF"
        assert memory_rows(file) == []

        body = turn(client, headers, "I live in Benghazi", "fact-2").json()
        assert body["memory_proposal"] is None
        assert "pending" not in body["context"]["model_request"]["instructions"].lower()
        proposed = client.post(
            PROPOSE,
            headers=headers,
            json={"turn_id": body["turn_id"], "request_id": "fact-2", "fact": "I live in Benghazi"},
        ).json()
        assert proposed["status"] == "rejected" and proposed["error"] == "MEMORY_OFF"
        assert memory_rows(file) == []
        assert account_mode(file) == "auto"
    finally:
        cm.__exit__(None, None, None)


def test_a_sibling_chat_of_the_same_owner_still_auto_saves(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        owner = client.post("/v1/demo-session").json()["id"]
        first = only_chat(client)
        set_chat_mode(client, first, "off")
        second = client.post("/v1/conversations").json()["id"]
        sibling_headers = add_turn_session(file, second, owner, identifier="sibling")

        proposal = proposal_of(client, sibling_headers, "My name is Alex", "fact-1")
        saved = auto(client, sibling_headers, proposal["token"])
        assert saved.status_code == 200, saved.text
        assert memory_rows(file)[0][0] == "My name is Alex"
        assert account_mode(file) == "auto"
    finally:
        cm.__exit__(None, None, None)


def test_chat_auto_overrides_an_account_off_default(tmp_path):
    cm, client, file, headers = started(tmp_path, "off")
    try:
        chat = only_chat(client)
        set_chat_mode(client, chat, "auto")
        proposal = proposal_of(client, headers, "My name is Sam", "fact-1")
        saved = auto(client, headers, proposal["token"])
        assert saved.status_code == 200, saved.text
        assert memory_rows(file)[0][0] == "My name is Sam"
        assert account_mode(file) == "off"
    finally:
        cm.__exit__(None, None, None)


def test_mid_chat_switch_blocks_the_armed_proposal_and_keeps_earlier_memory_undoable(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        chat = only_chat(client)
        earlier = proposal_of(client, headers, "My name is Sam", "fact-1")
        first = auto(client, headers, earlier["token"])
        assert first.status_code == 200, first.text
        change = first.json()["change"]

        armed = proposal_of(client, headers, "I live in Benghazi", "fact-2")
        set_chat_mode(client, chat, "off")

        saved = auto(client, headers, armed["token"])
        assert saved.status_code == 409 and saved.json()["detail"]["code"] == "MEMORY_OFF"
        assert [content for content, *_ in memory_rows(file)] == ["My name is Sam"]

        undone = client.post(f"/v1/changes/{change['id']}/undo")
        assert undone.status_code == 200 and undone.json()["changed"] is True
        assert [(content, status) for content, _, status, _ in memory_rows(file)] == [("", "deleted")]
        assert account_mode(file) == "auto"
    finally:
        cm.__exit__(None, None, None)


def test_turn_start_prompt_is_off_for_the_off_chat_and_on_for_its_sibling(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        owner = client.post("/v1/demo-session").json()["id"]
        off_chat = only_chat(client)
        set_chat_mode(client, off_chat, "off")
        sibling = client.post("/v1/conversations").json()["id"]
        sibling_headers = add_turn_session(file, sibling, owner, identifier="sibling")

        off_body = turn(client, headers, "I prefer tea", "off-1").json()
        assert off_body["memory_proposal"] is None
        assert "pending" not in off_body["context"]["model_request"]["instructions"].lower()

        on_body = turn(client, sibling_headers, "I prefer tea", "on-1").json()
        assert on_body["memory_proposal"] is not None
        assert "pending" in on_body["context"]["model_request"]["instructions"].lower()
    finally:
        cm.__exit__(None, None, None)


def test_http_get_and_put_expose_the_override_and_effective_mode(tmp_path):
    file, settings = configured(tmp_path)
    app = create_app(settings, embedding_adapter=FakeEmbedder())
    with TestClient(app) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        listed = client.get("/v1/conversations").json()[0]
        assert listed["memory_mode"] is None
        assert listed["effective_memory_mode"] == "auto"

        updated = set_chat_mode(client, chat, "off")
        assert updated["memory_mode"] == "off" and updated["effective_memory_mode"] == "off"
        assert client.get("/v1/conversations").json()[0]["memory_mode"] == "off"

        account = client.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": "auto"}).json()
        assert account["memory_mode"] == "auto" and account["effective_memory_mode"] == "auto"

        cleared = client.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": None}).json()
        assert cleared["memory_mode"] is None and cleared["effective_memory_mode"] == "auto"

        assert client.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": "garbage"}).status_code == 422


def test_http_put_is_owner_scoped_and_deleted_conversations_are_gone(tmp_path):
    file, settings = configured(tmp_path)
    app = create_app(settings, embedding_adapter=FakeEmbedder())
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.post("/v1/demo-session")
        chat = alice.post("/v1/conversations").json()["id"]

        bob.post("/v1/demo-session")
        assert bob.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": "off"}).status_code == 404

        assert alice.delete(f"/v1/conversations/{chat}").status_code == 204
        assert alice.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": "off"}).status_code == 404


def test_clearing_a_chat_override_returns_to_the_account_default(tmp_path):
    cm, client, file, headers = started(tmp_path, "ask")
    try:
        chat = only_chat(client)
        set_chat_mode(client, chat, "off")
        assert client.get("/v1/conversations").json()[0]["effective_memory_mode"] == "off"
        cleared = set_chat_mode(client, chat, None)
        assert cleared["memory_mode"] is None and cleared["effective_memory_mode"] == "ask"
        assert account_mode(file) == "ask"
    finally:
        cm.__exit__(None, None, None)


def propose_tool(client, headers, fact, request_id, turn_id):
    return client.post(
        PROPOSE, headers=headers, json={"turn_id": turn_id, "request_id": request_id, "fact": fact}
    ).json()


def test_the_propose_tool_proposes_nothing_in_a_chat_that_is_off_while_the_account_asks(tmp_path):
    # Account "ask" would arm a spoken question; the chat override must stop it before anything is proposed.
    cm, client, file, headers = started(tmp_path, "ask")
    try:
        chat = only_chat(client)
        turn_id = turn(client, headers, "hello there", "t1").json()["turn_id"]
        set_chat_mode(client, chat, "off")
        body = propose_tool(client, headers, "I live in Benghazi", "p1", turn_id)
        assert body["status"] == "rejected" and body["error"] == "MEMORY_OFF", body
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_the_propose_tool_re_reads_the_chat_mode_just_before_it_saves(tmp_path, monkeypatch):
    from app.routes import worker as worker_module

    cm, client, file, headers = started(tmp_path, "auto")
    try:
        turn_id = turn(client, headers, "hello there", "t1").json()["turn_id"]
        original = worker_module.proposal_conflicts

        async def conflicts_then_chat_off(*args, **kwargs):
            result = await original(*args, **kwargs)
            with closing(sqlite3.connect(file)) as db, db:
                db.execute("UPDATE conversations SET memory_mode = 'off'")
            return result

        monkeypatch.setattr(worker_module, "proposal_conflicts", conflicts_then_chat_off)
        body = propose_tool(client, headers, "I live in Benghazi", "p1", turn_id)
        assert body["status"] == "rejected" and body["error"] == "MEMORY_OFF", body
        assert memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)
