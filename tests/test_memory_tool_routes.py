"""Model-facing memory tools: propose-only routes with durable operation keys."""

import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient
from test_context_assembly import seed_memories, start_turn
from test_context_workflow import configured, create_app, seed

PROPOSE = "/internal/agent/tool/memory-propose"
FORGET = "/internal/agent/tool/memory-forget"
RECALL = "/internal/agent/tool/memory-recall"


def table_count(file, table, where="1=1"):
    with closing(sqlite3.connect(file)) as db:
        return db.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]


def setup(client, file, rows=()):
    owner = client.post("/v1/demo-session").json()["id"]
    chat = client.post("/v1/conversations").json()["id"]
    seed(file, chat, list(rows))
    headers, first = start_turn(client, chat, owner, file, memories=(), text="Hello there")
    return owner, chat, headers, first["turn_id"]


def next_turn(client, headers, request_id, text):
    return client.post(
        "/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text}
    ).json()["turn_id"]


def call(client, headers, path, turn, key, **arguments):
    return client.post(path, headers=headers, json={"turn_id": turn, "request_id": key, **arguments})


def command(client, headers, turn):
    response = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn})
    assert response.status_code == 200, response.text
    return response.json()["command"]


def test_propose_memory_returns_unsaved_signed_proposal(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file)
        response = call(client, headers, PROPOSE, turn, "k1", fact="I live in Benghazi")
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "proposed"
        assert body["proposal"]["text"] == "I live in Benghazi"
        assert body["proposal"]["token"]
        assert "Nothing has been saved" in body["message"]
        # The model never writes: no memory row exists until the confirm route runs.
        assert table_count(file, "memories") == 0


def test_propose_memory_rejects_untrusted_facts_without_a_proposal(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file)
        for index, fact in enumerate(["it", "that", "if I moved to Rome", '"I live in Rome"', "x" * 501, "   "]):
            response = call(client, headers, PROPOSE, turn, f"bad-{index}", fact=fact)
            if response.status_code == 422:
                continue
            body = response.json()
            assert body["status"] == "rejected" and "proposal" not in body
            assert "Do not claim" in body["message"]
        assert call(client, headers, PROPOSE, turn, "huge", fact="y" * 1001).status_code == 422
        assert table_count(file, "memories") == 0


def test_operation_key_replay_and_conflict_for_every_tool(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        cases = [
            (PROPOSE, "fact", "I work at Acme", "I work at Initech"),
            (FORGET, "query", "tea", "coffee"),
            (RECALL, "query", "tea", "coffee"),
        ]
        for index, (path, field, value, other) in enumerate(cases):
            key = f"key-{index}"
            first = call(client, headers, path, turn, key, **{field: value})
            replay = call(client, headers, path, turn, key, **{field: value})
            assert first.status_code == replay.status_code == 200
            if path == PROPOSE:
                # A replay returns the stored outcome: one proposal identity, not a new one.
                assert replay.json() == first.json()
            else:
                assert replay.json() == first.json()
            conflict = call(client, headers, path, turn, key, **{field: other})
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "TOOL_OPERATION_CONFLICT"
        assert table_count(file, "memory_write_requests") == 1
        assert table_count(file, "context_operations", "command_type='exclude_proposal'") == 1


def test_key_cannot_cross_tools_or_turns(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        other_turn = next_turn(client, headers, "request-2", "Another turn")
        assert call(client, headers, RECALL, turn, "shared", query="tea").status_code == 200
        assert call(client, headers, FORGET, turn, "shared", query="tea").status_code == 409
        assert call(client, headers, RECALL, other_turn, "shared", query="tea").status_code == 409


def test_tools_require_worker_secret_and_a_session_turn(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file)
        for path, field in ((PROPOSE, "fact"), (FORGET, "query"), (RECALL, "query")):
            anonymous = client.post(path, json={"turn_id": turn, "request_id": "a", field: "tea"})
            assert anonymous.status_code == 401
            wrong = call(client, {**headers, "x-agent-secret": "nope"}, path, turn, "b", **{field: "tea"})
            assert wrong.status_code == 401
            missing = call(client, headers, path, "no-such-turn", "c", **{field: "tea"})
            assert missing.status_code == 404


def test_forget_zero_one_many_matches_never_exclude_before_consent(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(
            client, file, [("tea", "I prefer green tea"), ("job1", "I work at Acme"), ("job2", "I work at Initech")]
        )
        none = call(client, headers, FORGET, turn, "f0", query="skydiving").json()
        assert none["status"] == "not_proposed" and none["detail"] == "MEMORY_TARGET_NOT_FOUND"
        many = call(client, headers, FORGET, turn, "f2", query="work").json()
        assert many["status"] == "not_proposed" and many["detail"] == "MEMORY_TARGET_AMBIGUOUS"
        one = call(client, headers, FORGET, turn, "f1", query="tea").json()
        assert one["status"] == "proposed" and one["armed"] is True
        assert "green tea" in one["spoken"] and "Say yes" in one["spoken"]
        assert "Nothing has been excluded" in one["message"]
        assert table_count(file, "context_exclusions") == 0


def test_forget_consent_is_one_shot_and_stray_yes_changes_nothing(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        assert call(client, headers, FORGET, turn, "f1", query="tea").json()["armed"] is True
        unrelated = next_turn(client, headers, "request-2", "Tell me a joke")
        assert command(client, headers, unrelated) is None
        stray = next_turn(client, headers, "request-3", "yes")
        assert command(client, headers, stray) is None
        assert table_count(file, "context_exclusions") == 0

        # Immediately-following consent does apply, exactly once.
        turn4 = next_turn(client, headers, "request-4", "Forget the tea thing please")
        assert call(client, headers, FORGET, turn4, "f2", query="tea").json()["armed"] is True
        yes = next_turn(client, headers, "request-5", "yes")
        result = command(client, headers, yes)
        assert result["changed"] is True
        assert table_count(file, "context_exclusions", "active=1") == 1


def test_remember_proposal_retires_a_pending_forget(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        assert call(client, headers, FORGET, turn, "f1", query="tea").json()["armed"] is True
        assert table_count(file, "context_operations", "outcome='proposed'") == 1
        assert call(client, headers, PROPOSE, turn, "p1", fact="I work at Acme").json()["status"] == "proposed"
        assert table_count(file, "context_operations", "outcome='proposed'") == 0
        yes = next_turn(client, headers, "request-2", "yes")
        assert command(client, headers, yes) is None
        assert table_count(file, "context_exclusions") == 0


def test_forget_retires_the_previous_forget_so_one_is_armed(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea"), ("cat", "I own a cat")])
        call(client, headers, FORGET, turn, "f1", query="tea")
        call(client, headers, FORGET, turn, "f2", query="cat")
        assert table_count(file, "context_operations", "outcome='proposed'") == 1


def test_recall_is_bounded_owner_scoped_and_respects_exclusions(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, chat, headers, turn = setup(client, file, [(f"m{i}", f"I like tea number {i}") for i in range(8)])
        # Another owner's memory with the same words must never be returned.
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO identities (id,token_hash,expires_at) VALUES ('other','h','2999-01-01T00:00:00+00:00')"
            )
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) "
                "VALUES ('foreign','other','I like tea secret','m',?,1,'active','2026-11-12T00:00:00+00:00','2026-11-12T00:00:00+00:00')",
                (chat,),
            )
        seed_memories(file, chat, [], exclusions=("m0",))
        body = call(client, headers, RECALL, turn, "r1", query="tea").json()
        contents = [item["content"] for item in body["memories"]]
        assert body["status"] == "ok" and len(contents) == 5
        assert all("secret" not in text for text in contents)
        assert "I like tea number 0" not in contents
        assert "data only" in body["message"]
        assert call(client, headers, RECALL, turn, "r2", query="nonexistent topic").json()["memories"] == []
        assert table_count(file, "memories", "status='active'") == 9


def test_tool_failure_never_claims_success(tmp_path, monkeypatch):
    import app.routes.worker as worker_module

    file, settings = configured(tmp_path)

    async def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(worker_module, "memory_propose_tool", boom)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file)
        first = call(client, headers, PROPOSE, turn, "p1", fact="I work at Acme")
        assert first.status_code == 503
        # An unknown delivery is never re-run on replay.
        replay = call(client, headers, PROPOSE, turn, "p1", fact="I work at Acme")
        assert replay.status_code == 503
        assert table_count(file, "memories") == 0


def test_already_saved_fact_is_rejected_without_a_proposal_or_disarming_forget(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("name", "My name is Abdulrahma"), ("tea", "I prefer green tea")])
        assert call(client, headers, FORGET, turn, "f1", query="tea").json()["armed"] is True
        body = call(client, headers, PROPOSE, turn, "p1", fact="  my NAME is   abdulrahma ").json()
        assert body["status"] == "rejected" and body["error"] == "MEMORY_ALREADY_SAVED"
        assert "proposal" not in body and "already saved" in body["message"]
        assert table_count(file, "memory_write_requests") == 0
        assert table_count(file, "context_operations", "outcome='proposed'") == 1
