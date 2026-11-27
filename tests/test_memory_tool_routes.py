"""Model-facing memory tools: propose-only routes with durable operation keys."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from test_context_assembly import seed_memories, start_turn
from test_context_workflow import configured, create_app, seed
from test_memory_capture import set_mode

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
        owner, _, headers, turn = setup(client, file)
        set_mode(file, owner, "ask")  # this covers the ask-first flow; auto saves without a question
        response = call(client, headers, PROPOSE, turn, "k1", fact="I live in Benghazi")
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "proposed"
        assert "plan" not in body["proposal"]
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
        owner, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        set_mode(file, owner, "ask")  # these cover the ask-first flow; auto saves a proposed fact without a question
        cases = [
            (PROPOSE, "fact", "I work at Acme", "I work at Initech"),
            (FORGET, "topic", "tea", "coffee"),
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
        assert table_count(file, "context_operations", "command_type='memory_hide'") == 1


def test_key_cannot_cross_tools_or_turns(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        other_turn = next_turn(client, headers, "request-2", "Another turn")
        assert call(client, headers, RECALL, turn, "shared", query="tea").status_code == 200
        assert call(client, headers, FORGET, turn, "shared", topic="tea").status_code == 409
        assert call(client, headers, RECALL, other_turn, "shared", query="tea").status_code == 409


def test_tools_require_worker_secret_and_a_session_turn(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file)
        for path, field in ((PROPOSE, "fact"), (FORGET, "topic"), (RECALL, "query")):
            anonymous = client.post(path, json={"turn_id": turn, "request_id": "a", field: "tea"})
            assert anonymous.status_code == 401
            wrong = call(client, {**headers, "x-agent-secret": "nope"}, path, turn, "b", **{field: "tea"})
            assert wrong.status_code == 401
            missing = call(client, headers, path, "no-such-turn", "c", **{field: "tea"})
            assert missing.status_code == 404


def memory_status(file, memory_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()[0]


def test_forget_previews_zero_one_or_many_then_hides_all_on_one_yes(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(
            client, file, [("tea", "I prefer green tea"), ("job1", "I work at Acme"), ("job2", "I work at Initech")]
        )
        none = call(client, headers, FORGET, turn, "f0", topic="skydiving").json()
        assert none["status"] == "not_hidden" and none["detail"] == "MEMORY_TARGET_NOT_FOUND"
        many = call(client, headers, FORGET, turn, "f2", topic="work").json()
        assert many["status"] == "proposed" and many["detail"] == "MEMORY_HIDE_PROPOSED"
        assert many["spoken"] == "I found 2 memories about work. Hide all of them?"
        assert memory_status(file, "job1") == memory_status(file, "job2") == "active"
        applied_many = command(client, headers, next_turn(client, headers, "yes-many", "yes"))
        assert applied_many["detail"] == "MEMORY_HIDDEN"
        assert applied_many["spoken"] == "Hidden 2 memories about work. Say undo to bring them back."
        assert memory_status(file, "job1") == memory_status(file, "job2") == "hidden"

        one = call(client, headers, FORGET, turn, "f1", topic="tea").json()
        assert one["status"] == "proposed" and one["detail"] == "MEMORY_HIDE_PROPOSED"
        assert one["spoken"] == "I found 1 memory about tea. Hide it?"
        assert "do not add" in one["message"].lower()
        assert memory_status(file, "tea") == "active"
        applied_one = command(client, headers, next_turn(client, headers, "yes-one", "yes"))
        assert applied_one["detail"] == "MEMORY_HIDDEN"
        assert applied_one["spoken"] == "Hidden 1 memory about tea. Say undo to bring it back."
        assert memory_status(file, "tea") == "hidden"
        assert table_count(file, "context_exclusions") == 0


def test_forget_tool_everything_scope_refuses_and_discards_a_pending_hide(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea"), ("paris", "I proposed in Paris")])

        preview = call(client, headers, FORGET, turn, "paris", topic="Paris").json()
        assert preview["status"] == "proposed"
        assert memory_status(file, "paris") == "active"

        refused = call(client, headers, FORGET, turn, "everything", scope="everything").json()
        assert refused["status"] == "not_hidden"
        assert refused["detail"] == "MEMORY_GLOBAL_RETRACTION_UNAVAILABLE"
        assert refused["spoken"] == (
            "I can't clear all your memories by voice. You can review and delete them in Settings, under Memory. "
            "Nothing was changed."
        )
        yes = next_turn(client, headers, "yes-after-everything", "yes")
        assert command(client, headers, yes) is None
        assert memory_status(file, "paris") == memory_status(file, "tea") == "active"


@pytest.mark.parametrize(
    "topic",
    ["", "everything", "ve", "don't forget everything", "I forgot my keys", "forget everything about Paris"],
)
def test_forget_tool_rejects_unsearchable_or_sentential_topics(tmp_path, topic):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])

        body = call(client, headers, FORGET, turn, "bad-topic", topic=topic).json()
        assert body["status"] == "not_hidden"
        assert body["detail"] == "MEMORY_TARGET_UNSPECIFIED"
        assert "Ask the user what they want forgotten" in body["message"]
        assert memory_status(file, "tea") == "active"


def test_an_invalid_topic_refusal_discards_a_pending_hide(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("paris", "I proposed in Paris")])
        assert call(client, headers, FORGET, turn, "paris-preview", topic="Paris").json()["status"] == "proposed"

        refused = call(client, headers, FORGET, turn, "bad-topic", topic="everything").json()
        assert refused["status"] == "not_hidden"
        assert "Ask the user what they want forgotten" in refused["message"]

        yes = next_turn(client, headers, "yes-after-invalid", "yes")
        assert command(client, headers, yes) is None
        assert memory_status(file, "paris") == "active"


def test_forget_tool_topic_ignores_contraction_fragments(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(
            client, file, [("doctor", "My doctor said I need rest"), ("tea", "I prefer green tea")]
        )

        body = call(client, headers, FORGET, turn, "contraction", topic="tea").json()
        assert body["status"] == "proposed"
        assert body["spoken"] == "I found 1 memory about tea. Hide it?"
        assert "said" not in body["spoken"]


def test_forget_tool_chat_scope_clears_only_this_chats_temporary_memories(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, chat, headers, turn = setup(client, file, [("global", "I live in Tripoli")])
        other = client.post("/v1/conversations").json()["id"]
        with closing(sqlite3.connect(file)) as db, db:
            for memory_id, content, source in (
                ("temp-here", "A temporary note here", chat),
                ("temp-other", "Another chat's secret", other),
            ):
                db.execute(
                    "INSERT INTO memories (id,owner_id,content,scope,embedding_model,source_conversation_id,"
                    "version,status,created_at,updated_at) VALUES (?,?,?,'conversation','m',?,1,'active',"
                    "'2026-11-12T00:00:00+00:00','2026-11-12T00:00:00+00:00')",
                    (memory_id, owner, content, source),
                )
            db.commit()

        preview = call(client, headers, FORGET, turn, "chat-scope", scope="this_chat").json()
        assert preview["status"] == "proposed"
        assert preview["detail"] == "MEMORY_HIDE_PROPOSED"
        assert preview["spoken"] == "I found 1 temporary memory from this chat. Clear it?"
        assert memory_status(file, "temp-here") == "active"

        applied = command(client, headers, next_turn(client, headers, "chat-yes", "yes"))
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert applied["spoken"] == "Cleared 1 temporary memory from this chat. Say undo to bring it back."
        assert memory_status(file, "temp-here") == "hidden"
        assert memory_status(file, "global") == memory_status(file, "temp-other") == "active"


def test_forget_tool_keeps_a_budget_note_out_of_the_topic_hide(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(
            client,
            file,
            [("paris", "I travelled to Paris"), ("budget", "Budget 500 USD for the Paris trip")],
        )

        preview = call(client, headers, FORGET, turn, "budget-mix", topic="Paris").json()
        assert preview["status"] == "proposed"
        assert preview["spoken"] == (
            "I found 2 memories about Paris; I'll keep 1 budget note unless you name it. Hide the other one?"
        )
        assert memory_status(file, "budget") == "active"

        applied = command(client, headers, next_turn(client, headers, "budget-yes", "yes"))
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert memory_status(file, "paris") == "hidden"
        assert memory_status(file, "budget") == "active"


def test_forget_tool_refuses_when_only_a_budget_note_matches(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("budget", "Budget 500 USD for the Paris trip")])

        body = call(client, headers, FORGET, turn, "only-budget", topic="Paris").json()
        assert body["status"] == "not_hidden"
        assert body["spoken"] == "The only match is a budget note; name it exactly to forget it. Nothing was changed."
        assert memory_status(file, "budget") == "active"


def test_forget_tool_accepts_the_legacy_query_field_as_a_topic(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])

        body = call(client, headers, FORGET, turn, "legacy-query", query="tea").json()
        assert body["status"] == "proposed"
        assert body["detail"] == "MEMORY_HIDE_PROPOSED"
        assert body["spoken"] == "I found 1 memory about tea. Hide it?"
        assert memory_status(file, "tea") == "active"
        applied = command(client, headers, next_turn(client, headers, "legacy-yes", "yes"))
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert memory_status(file, "tea") == "hidden"


def test_undo_unhides_the_latest_hide_and_a_stray_yes_changes_nothing(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [("tea", "I prefer green tea"), ("cat", "I own a cat")])
        preview = call(client, headers, FORGET, turn, "f1", topic="tea").json()
        assert preview["status"] == "proposed" and preview["detail"] == "MEMORY_HIDE_PROPOSED"
        applied = command(client, headers, next_turn(client, headers, "confirm-1", "yes"))
        assert applied["detail"] == "MEMORY_HIDDEN"
        stray = next_turn(client, headers, "request-2", "yes")
        assert command(client, headers, stray) is None
        assert memory_status(file, "tea") == "hidden" and memory_status(file, "cat") == "active"
        undo = command(client, headers, next_turn(client, headers, "request-3", "Undo"))
        assert undo["detail"] == "MEMORY_UNHIDDEN" and undo["spoken"] == "Brought back 'I prefer green tea'."
        assert memory_status(file, "tea") == "active"
        again = command(client, headers, next_turn(client, headers, "request-4", "Undo"))
        assert again["detail"] == "NO_HIDDEN_MEMORY"


def test_a_forget_preview_supersedes_a_pending_remember_and_needs_one_yes(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, _, headers, turn = setup(client, file, [("tea", "I prefer green tea")])
        set_mode(file, owner, "ask")  # these cover the ask-first flow; auto saves a proposed fact without a question
        assert call(client, headers, PROPOSE, turn, "p1", fact="I work at Acme").json()["status"] == "proposed"
        preview = call(client, headers, FORGET, turn, "f1", topic="tea").json()
        assert preview["status"] == "proposed" and preview["detail"] == "MEMORY_HIDE_PROPOSED"
        # The hide has not happened yet, and the earlier remember question is no longer armed.
        assert table_count(file, "memories", "status='active'") == 1
        applied = command(client, headers, next_turn(client, headers, "confirm-1", "yes"))
        assert applied["detail"] == "MEMORY_HIDDEN"
        assert table_count(file, "memories", "status='active'") == 0
        assert table_count(file, "memory_write_requests", "state='proposed'") == 1


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
            db.execute("INSERT INTO memory_terms (memory_id,owner_id,term) VALUES ('foreign','other','tea')")
        seed_memories(file, chat, [], exclusions=("m0",))
        body = call(client, headers, RECALL, turn, "r1", query="tea").json()
        contents = [item["content"] for item in body["memories"]]
        assert body["status"] == "ok" and len(contents) == 7
        assert body["total"] == 7 and body["truncated"] is False
        assert all("secret" not in text for text in contents)
        assert "I like tea number 0" not in contents
        assert "data only" in body["message"]
        # A named topic with no match stays empty; a question that names no topic lists the newest visible
        # memories, still bounded, owner-scoped and respecting exclusions.
        assert call(client, headers, RECALL, turn, "r2", query="nonexistent topic").json()["memories"] == []
        listed = [item["content"] for item in call(client, headers, RECALL, turn, "r3", query="me").json()["memories"]]
        assert len(listed) == 5
        assert all("secret" not in text for text in listed)
        assert "I like tea number 0" not in listed
        assert table_count(file, "memories", "status='active'") == 9


def test_recall_route_returns_every_match_with_total_and_truncation(tmp_path, monkeypatch):
    from app.services import memory_tools

    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        _, _, headers, turn = setup(client, file, [(f"m{i}", f"I like Paris note {i}") for i in range(40)])
        body = call(client, headers, RECALL, turn, "r1", query="Paris").json()
        assert len(body["memories"]) == 40
        assert body["total"] == 40 and body["truncated"] is False

        monkeypatch.setattr(memory_tools, "RECALL_CHAR_BUDGET", 30)
        tiny = call(client, headers, RECALL, turn, "r2", query="Paris").json()
        assert 0 < len(tiny["memories"]) < 40
        assert tiny["total"] == 40 and tiny["truncated"] is True


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


def test_already_saved_fact_is_rejected_but_a_hidden_one_is_not_counted(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, _, headers, turn = setup(client, file, [("name", "My name is Abdulrahma")])
        set_mode(file, owner, "ask")  # these cover the ask-first flow; auto saves a proposed fact without a question
        body = call(client, headers, PROPOSE, turn, "p1", fact="  my NAME is   abdulrahma ").json()
        assert body["status"] == "rejected" and body["error"] == "MEMORY_ALREADY_SAVED"
        assert "proposal" not in body and "already saved" in body["message"]
        assert table_count(file, "memory_write_requests") == 0
        assert call(client, headers, FORGET, turn, "f1", topic="name").json()["status"] == "proposed"
        assert command(client, headers, next_turn(client, headers, "confirm-1", "yes"))["detail"] == "MEMORY_HIDDEN"
        again = call(client, headers, PROPOSE, turn, "p2", fact="My name is Abdulrahma").json()
        assert again["status"] == "proposed"


def test_already_saved_ignores_a_temporary_memory_in_another_chat(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, chat, headers, turn = setup(client, file)
        set_mode(file, owner, "ask")  # without the auto-save path, this exercises _already_saved directly
        other = client.post("/v1/conversations").json()["id"]
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,scope,"
                "created_at,updated_at) VALUES ('temp-other', (SELECT owner_id FROM conversations WHERE id=?), "
                "'My name is Abdulrahma', 'text-embedding-3-small', ?, 1, 'active', 'conversation', "
                "'2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
                (other, other),
            )
            db.commit()

        body = call(client, headers, PROPOSE, turn, "p1", fact="My name is Abdulrahma").json()
        assert body["status"] == "proposed"

        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,scope,"
                "created_at,updated_at) VALUES ('temp-chat', (SELECT owner_id FROM conversations WHERE id=?), "
                "'My name is Abdulrahma', 'text-embedding-3-small', ?, 1, 'active', 'conversation', "
                "'2026-11-12T00:00:01+00:00', '2026-11-12T00:00:01+00:00')",
                (chat, chat),
            )
            db.commit()

        again = call(client, headers, PROPOSE, turn, "p2", fact="My name is Abdulrahma").json()
        assert again["status"] == "rejected" and again["error"] == "MEMORY_ALREADY_SAVED"


def test_forgetting_and_recalling_keep_working_when_memory_is_off(tmp_path):
    """Memory off stops SAVING. It must never stop the user forgetting or hearing what is stored."""
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner, _, headers, turn = setup(client, file, [("tea", "I prefer green tea"), ("cat", "I own a cat")])
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO user_settings (owner_id,speaking_speed,captions_visible,response_preference,"
                "memory_mode,revision,updated_at) VALUES (?,?,?,?,?,?,?)",
                (owner, 1.0, 1, "automatic", "off", 1, "2026-11-25T00:00:00+00:00"),
            )

        proposed = call(client, headers, PROPOSE, turn, "p1", fact="I live in Benghazi").json()
        assert proposed["status"] == "rejected" and proposed["error"] == "MEMORY_OFF"

        recalled = call(client, headers, RECALL, turn, "r1", query="tea").json()
        assert [item["content"] for item in recalled["memories"]] == ["I prefer green tea"]

        forgotten = call(client, headers, FORGET, turn, "f1", topic="tea").json()
        assert forgotten["status"] == "proposed"
        assert command(client, headers, next_turn(client, headers, "confirm-1", "yes"))["detail"] == "MEMORY_HIDDEN"
        assert table_count(file, "memories", "status='hidden'") == 1
        assert table_count(file, "memories", "status='active'") == 1
        assert table_count(file, "memory_write_requests") == 0
