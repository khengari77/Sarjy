import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.domain.budget import OPENAI_DAILY_MICRO_USD, budget_day
from app.domain.memory_capture import parse_capture
from app.infrastructure.database import Memory
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


def confirm_replace(client, headers, detail):
    assert detail["code"] == "MEMORY_CLARIFICATION_REQUIRED"
    return client.post(
        "/internal/agent/memory/confirm",
        headers=headers,
        json={"token": detail["token"], "decision": "replace"},
    )


def test_parse_explicit_and_ordinary_but_not_negated_or_quoted():
    assert parse_capture("Remember that I prefer tea").text == "I prefer tea"
    assert parse_capture("Don't forget my budget is 300 USD").subject == "attribute:budget"
    assert parse_capture("I live in Tripoli").subject == "location"
    assert parse_capture("My name is Sam").subject == "attribute:name"
    assert parse_capture("I don't remember that") is None
    assert parse_capture("Remember that if I win I prefer tea") is None
    assert parse_capture('"I prefer tea"') is None
    assert parse_capture("Do I prefer tea?") is None
    assert parse_capture("I prefer tea?") is None
    assert parse_capture("I like it") is None
    assert parse_capture("I love this") is None
    assert parse_capture("My point is that we should leave") is None


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
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "saved"
        assert duplicate.json().get("idempotent") is True
        assert len(memory_rows(file)) == 1

        # A different conversation under the same owner recalls the confirmed fact.
        recalled_chat = client.post("/v1/conversations").json()["id"]
        recalled_headers = add_turn_session(file, recalled_chat, owner, identifier="session-2")
        recalled = turn(client, recalled_headers, "What do I prefer?", "recall-1").json()
        messages = recalled["context"]["model_request"]["messages"]
        recalled_memory = [message for message in messages if "tea with mint" in message["content"]]
        assert recalled_memory and all(message["role"] != "system" for message in recalled_memory)

        corrected = turn(client, recalled_headers, "I prefer coffee now", "fact-2").json()["memory_proposal"]
        clarify = client.post(
            "/internal/agent/memory/confirm",
            headers=recalled_headers,
            json={"token": corrected["token"], "decision": "save"},
        )
        assert clarify.status_code == 409, clarify.text
        result = confirm_replace(client, recalled_headers, clarify.json()["detail"])
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


def test_preference_correction_targets_the_same_topic_and_can_be_restored(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        def save(text, request_id):
            proposal = turn(client, headers, text, request_id).json()["memory_proposal"]
            response = client.post(
                "/internal/agent/memory/confirm",
                headers=headers,
                json={"token": proposal["token"], "decision": "save"},
            )
            if response.status_code == 409:
                response = confirm_replace(client, headers, response.json()["detail"])
            assert response.status_code == 200, response.text
            return response.json()

        assert save("I prefer tea", "pref-1")["status"] == "saved"
        assert save("I love jazz", "pref-2")["status"] == "saved"
        changed = save("I prefer coffee now", "pref-3")
        assert changed["status"] == "corrected" and changed["memory"]["version"] == 2
        active = {row[0]: row[1] for row in memory_rows(file) if row[2] == "active"}
        assert active == {"I love jazz": 1, "I prefer coffee now": 2}

        without_marker = turn(client, headers, "I prefer juice", "pref-4").json()["memory_proposal"]
        conflict = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": without_marker["token"], "decision": "save"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "MEMORY_CLARIFICATION_REQUIRED"
        replaced = confirm_replace(client, headers, conflict.json()["detail"])
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["status"] == "corrected" and replaced.json()["memory"]["version"] == 3

        restored = save("I prefer tea", "pref-5")
        assert restored["status"] == "corrected" and restored["memory"]["version"] == 4
        active = {row[0]: row[1] for row in memory_rows(file) if row[2] == "active"}
        assert active == {"I love jazz": 1, "I prefer tea": 4}


def test_replacement_marker_corrects_a_preference_without_a_topic_keyword(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        def save(text, request_id):
            proposal = turn(client, headers, text, request_id).json()["memory_proposal"]
            response = client.post(
                "/internal/agent/memory/confirm",
                headers=headers,
                json={"token": proposal["token"], "decision": "save"},
            )
            if response.status_code == 409:
                response = confirm_replace(client, headers, response.json()["detail"])
            assert response.status_code == 200, response.text
            return response.json()

        assert save("I prefer the blue one", "fallback-1")["status"] == "saved"
        changed = save("I prefer the red one now", "fallback-2")
        assert changed["status"] == "corrected" and changed["memory"]["version"] == 2
        active = {row[0]: row[1] for row in memory_rows(file) if row[2] == "active"}
        assert active == {"I prefer the red one now": 2}


def test_unrelated_preferences_and_allergies_are_not_destroyed(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        for index, text in enumerate(
            [
                "I prefer tea",
                "I love jazz",
                "I love my wife",
                "I love my dog",
                "I am allergic to peanuts",
                "I am allergic to shellfish",
                "I am allergic to tree nuts",
                "I am allergic to tree pollen",
            ]
        ):
            proposal = turn(client, headers, text, f"additive-{index}").json()["memory_proposal"]
            result = client.post(
                "/internal/agent/memory/confirm",
                headers=headers,
                json={"token": proposal["token"], "decision": "save"},
            )
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "saved"
        active = sorted(row[0] for row in memory_rows(file) if row[2] == "active")
        assert active == [
            "I am allergic to peanuts",
            "I am allergic to shellfish",
            "I am allergic to tree nuts",
            "I am allergic to tree pollen",
            "I love jazz",
            "I love my dog",
            "I love my wife",
            "I prefer tea",
        ]


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
            agent.memory_proposal = {
                "token": "signed",
                "text": "I prefer tea",
                "confirmation_id": "cid",
                "confirmation_revision": 1,
            }
            agent.memory_prompted_key = ("cid", 1)
            await agent.compile(SimpleNamespace(id="message", text_content=text))
            return paths, agent.memory_proposal, agent.memory_outcome

    paths, remaining, _ = asyncio.run(exchange("save it"))
    assert remaining is None
    assert paths == [
        ("/internal/agent/turn/start", {"request_id": "message", "text": "save it"}),
        ("/internal/agent/memory/confirm", {"token": "signed", "decision": "save"}),
    ]

    paths, remaining, outcome = asyncio.run(exchange("Yes."))
    assert paths == [("/internal/agent/turn/start", {"request_id": "message", "text": "Yes."})]
    assert remaining is None and outcome == "not_saved"

    paths, remaining, outcome = asyncio.run(exchange("maybe later"))
    assert paths == [("/internal/agent/turn/start", {"request_id": "message", "text": "maybe later"})]
    assert remaining is None and outcome == "not_saved"

    paths, remaining, outcome = asyncio.run(exchange("okay what's the weather"))
    assert paths == [("/internal/agent/turn/start", {"request_id": "message", "text": "okay what's the weather"})]
    assert remaining is None and outcome == "not_saved"

    paths, remaining, _ = asyncio.run(exchange("No thanks."))
    assert remaining is None
    assert paths[-1] == ("/internal/agent/memory/confirm", {"token": "signed", "decision": "discard"})


def test_worker_decides_once_when_a_message_is_compiled_twice():
    import asyncio
    from types import SimpleNamespace

    import httpx
    from main import SarjyAgent

    paths = []

    async def run():
        def backend(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.memory_proposal = {
                "token": "signed",
                "confirmation_id": "cid",
                "confirmation_revision": 1,
            }
            agent.memory_prompted_key = ("cid", 1)
            message = SimpleNamespace(id="message", text_content="save it")
            await agent.compile(message)
            await agent.compile(message)

    asyncio.run(run())
    assert paths.count("/internal/agent/memory/confirm") == 1


def test_concurrent_confirms_never_leave_contradictory_active_rows(tmp_path):
    import asyncio

    from app.domain.context_operations import sign_proposal
    from app.infrastructure.database import Base, Conversation, Identity, Turn
    from app.services.memory import MemoryService
    from app.services.memory_capture import confirm_memory
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    file = tmp_path / "concurrent.db"
    secret = "capture-secret"

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as db:
                await db.run_sync(Base.metadata.create_all)
            owner, chat = "owner", "chat"
            async with sessions() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.commit()
                db.add(Conversation(id=chat, owner_id=owner))
                await db.commit()
                db.add_all(
                    [
                        Turn(id="turn-tea", conversation_id=chat, request_id="tea", role="user", text="tea"),
                        Turn(id="turn-coffee", conversation_id=chat, request_id="coffee", role="user", text="coffee"),
                    ]
                )
                await db.commit()

            def token(content, turn):
                return sign_proposal(
                    {
                        "v": 1,
                        "kind": "memory_capture",
                        "owner_id": owner,
                        "conversation_id": chat,
                        "source_turn_id": turn,
                        "content": content,
                        "subject": parse_capture(content).subject,
                        "value": parse_capture(content).value,
                        "explicit": False,
                        "exp": 9999999999,
                    },
                    secret,
                )

            tea, coffee = token("I prefer tea", "turn-tea"), token("I prefer coffee now", "turn-coffee")

            async def apply(value):
                async with sessions() as db:
                    try:
                        return await confirm_memory(
                            db,
                            MemoryService(db, FakeEmbedder()),
                            token=value,
                            decision="save",
                            owner_id=owner,
                            conversation_id=chat,
                            source_turn_id=None,
                            secret=secret,
                        )
                    except Exception as exc:  # clarification is acceptable; contradiction is not
                        return {"status": type(exc).__name__}

            await asyncio.gather(apply(tea), apply(coffee))
            async with sessions() as db:
                active = list(
                    await db.scalars(select(Memory).where(Memory.owner_id == owner, Memory.status == "active"))
                )
            assert len(active) <= 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_worker_retains_a_clarifying_proposal_and_replaces_on_confirmation():
    import asyncio
    from types import SimpleNamespace

    import httpx
    from main import SarjyAgent

    paths = []

    async def run():
        state = {"clarified": False}

        def backend(request):
            paths.append((request.url.path, json.loads(request.content or b"{}")))
            if request.url.path.endswith("/memory/confirm"):
                if not state["clarified"]:
                    state["clarified"] = True
                    return httpx.Response(
                        409,
                        json={
                            "detail": {
                                "code": "MEMORY_CLARIFICATION_REQUIRED",
                                "token": "clarified",
                                "confirmation_id": "cid",
                                "confirmation_revision": 2,
                                "target_id": "target",
                                "target_version": 1,
                            }
                        },
                    )
                return httpx.Response(200, json={"status": "corrected"})
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.memory_proposal = {
                "token": "signed",
                "confirmation_id": "cid",
                "confirmation_revision": 1,
            }
            agent.memory_prompted_key = ("cid", 1)
            await agent.compile(SimpleNamespace(id="one", text_content="save it"))
            assert agent.memory_proposal is not None and agent.memory_clarify is True
            agent.memory_prompted_key = (
                agent.memory_proposal.get("confirmation_id"),
                agent.memory_proposal.get("confirmation_revision"),
            )
            await agent.compile(SimpleNamespace(id="two", text_content="replace it"))
            assert agent.memory_proposal is None

    asyncio.run(run())
    confirmations = [body for path, body in paths if path.endswith("/memory/confirm")]
    assert [body["decision"] for body in confirmations] == ["save", "replace"]


def test_worker_replies_after_a_successful_replacement(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from livekit.agents import llm
    from main import SarjyAgent
    from test_generation import request

    monkeypatch.setattr(SarjyAgent, "session", property(lambda self: SimpleNamespace(current_speech=None, llm=None)))

    async def run():
        written = request()
        from app.domain.context import prompt_sha256

        messages = [message.model_dump() for message in written.messages]
        context = {
            "revision": 7,
            "messages": messages,
            "canonical_prompt_sha256": prompt_sha256(messages),
            "model_request": written.model_dump(mode="json"),
        }

        async def backend(request):
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.prepared["message"] = {"turn_id": written.turn_id, "context": context}
            agent.current_turn = written.turn_id
            agent.memory_message = "message"
            agent.memory_outcome = "replace"
            seen = []

            async def stream(request, tools, tool_choice):
                from generation import _build_context

                context = _build_context(request)
                seen.append("\n".join(str(item.content or "") for item in context.items if item.role == "system"))
                yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="updated"))

            agent._stream_with_fallback = stream
            chat = llm.ChatContext()
            chat.add_message(role="user", content="yes", id="message")
            chunks = [chunk async for chunk in agent.llm_node(chat, [], None)]
            assert chunks and "replaced" in seen[0]
            assert agent.memory_outcome is None

    asyncio.run(run())


def test_explicit_remember_accepts_non_first_person_content(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        for index, text in enumerate(("Remember to renew the passport", "Remember buy oat milk")):
            started = turn(client, headers, text, f"explicit-{index}").json()
            proposal = started["memory_proposal"]
            assert proposal is not None
            saved = client.post(
                "/internal/agent/memory/confirm",
                headers=headers,
                json={"token": proposal["token"], "decision": "save"},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["status"] == "saved"

    contents = [row[0] for row in memory_rows(file)]
    assert "to renew the passport" in contents
    assert "buy oat milk" in contents


def test_non_preference_conflicts_require_explicit_replacement(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        def propose(text, request_id):
            return turn(client, headers, text, request_id).json()["memory_proposal"]

        first = propose("I live in Riyadh", "loc-1")
        assert (
            client.post(
                "/internal/agent/memory/confirm", headers=headers, json={"token": first["token"], "decision": "save"}
            ).json()["status"]
            == "saved"
        )

        second = propose("I stay in Jeddah", "loc-2")
        blocked = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": second["token"], "decision": "save"}
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "MEMORY_CLARIFICATION_REQUIRED"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I live in Riyadh"]

        replaced = confirm_replace(client, headers, blocked.json()["detail"])
        assert replaced.status_code == 200 and replaced.json()["status"] == "corrected"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I stay in Jeddah"]


def test_negated_corrections_and_vegetarian_are_captured(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        first = turn(client, headers, "I live in Riyadh", "neg-1").json()["memory_proposal"]
        client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": first["token"], "decision": "save"}
        )

        negated = turn(client, headers, "I no longer live in Riyadh", "neg-2").json()["memory_proposal"]
        assert negated is not None
        clarify = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": negated["token"], "decision": "save"}
        )
        assert clarify.status_code == 409
        result = confirm_replace(client, headers, clarify.json()["detail"])
        assert result.status_code == 200 and result.json()["status"] == "retracted"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == []

        vegetarian = turn(client, headers, "I am vegetarian", "neg-3").json()["memory_proposal"]
        assert vegetarian is not None
        assert parse_capture("I am vegetarian").subject == "diet:vegetarian"


def test_discard_is_durable_and_replay_cannot_save(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        proposal = turn(client, headers, "I prefer tea", "discard-1").json()["memory_proposal"]
        discarded = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "discard"}
        )
        assert discarded.status_code == 200 and discarded.json()["status"] == "discarded"
        replay = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": proposal["token"], "decision": "save"}
        )
        assert replay.status_code == 409
        assert memory_rows(file) == []


def test_replacement_marker_must_be_final_not_an_idiom():
    assert parse_capture("I prefer tea now").replaces is True
    assert parse_capture("I prefer tea instead").replaces is True
    assert parse_capture("I prefer tea now and then").replaces is False


def test_old_revision_and_changed_target_cannot_replace(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        first = turn(client, headers, "I prefer tea", "rev-1").json()["memory_proposal"]
        assert (
            client.post(
                "/internal/agent/memory/confirm",
                headers=headers,
                json={"token": first["token"], "decision": "save"},
            ).json()["status"]
            == "saved"
        )

        second = turn(client, headers, "I prefer coffee", "rev-2").json()["memory_proposal"]
        clarify = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": second["token"], "decision": "save"},
        )
        assert clarify.status_code == 409
        detail = clarify.json()["detail"]

        # The pre-clarification token cannot authorize a replacement.
        stale = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": second["token"], "decision": "replace"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "CONFIRMATION_STALE"

        # A changed target version invalidates the clarification too.
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE memories SET version = version + 1 WHERE id = ?", (detail["target_id"],))
            db.commit()
        changed = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "TARGET_CHANGED"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I prefer tea"]


def test_missing_confirmation_state_cannot_authorize_replacement(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)

        first = turn(client, headers, "I prefer tea", "missing-1").json()["memory_proposal"]
        client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": first["token"], "decision": "save"}
        )
        second = turn(client, headers, "I prefer coffee", "missing-2").json()["memory_proposal"]
        clarify = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": second["token"], "decision": "save"}
        )
        detail = clarify.json()["detail"]
        assert detail["code"] == "MEMORY_CLARIFICATION_REQUIRED"

        # A lost awaiting row means the signed token no longer carries consent.
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "DELETE FROM memory_capture_decisions WHERE confirmation_id = ? AND revision = ?",
                (detail["confirmation_id"], detail["confirmation_revision"]),
            )
            db.commit()

        blocked = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "MEMORY_FRESH_CONFIRMATION_REQUIRED"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I prefer tea"]
        assert all(row[0] != "I prefer coffee" for row in memory_rows(file))

        # A fresh save/clarify cycle still works after the rejected replay.
        fresh = turn(client, headers, "I prefer juice", "missing-3").json()["memory_proposal"]
        fresh_clarify = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": fresh["token"], "decision": "save"}
        )
        assert fresh_clarify.status_code == 409
        replaced = confirm_replace(client, headers, fresh_clarify.json()["detail"])
        assert replaced.status_code == 200 and replaced.json()["status"] == "corrected"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I prefer juice"]


def test_discard_clarification_is_terminal(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        first = turn(client, headers, "I prefer tea", "disc-1").json()["memory_proposal"]
        client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": first["token"], "decision": "save"}
        )

        second = turn(client, headers, "I prefer coffee", "disc-2").json()["memory_proposal"]
        clarify = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": second["token"], "decision": "save"}
        )
        detail = clarify.json()["detail"]
        discarded = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "discard"},
        )
        assert discarded.status_code == 200 and discarded.json()["status"] == "discarded"

        replay = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert replay.status_code == 409
        assert replay.json()["detail"]["code"] == "DECISION_ALREADY_FINAL"

        # Even the pre-clarification token cannot reopen a discarded revision.
        reopened = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": second["token"], "decision": "save"},
        )
        assert reopened.status_code == 409
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I prefer tea"]


def test_legacy_contradiction_beyond_one_page_is_found(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        with closing(sqlite3.connect(file)) as db, db:
            # The contradiction sorts behind a full page of unrelated rows, so
            # it is only reachable by exhausting the first legacy page.
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES ('zzzz-old', ?, 'I live in Tripoli', 'text-embedding-3-small', ?, 1, "
                "'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (owner, chat),
            )
            db.executemany(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES (?, ?, ?, 'text-embedding-3-small', ?, 1, 'active', "
                "'2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00')",
                [(f"aa-{index:04d}", owner, f"Unrelated note {index}", chat) for index in range(500)],
            )
            db.commit()

        new_fact = turn(client, headers, "I live in Paris", "legacy-1").json()["memory_proposal"]
        blocked = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": new_fact["token"], "decision": "save"},
        )
        assert blocked.status_code == 409
        active = [row[0] for row in memory_rows(file) if row[2] == "active"]
        assert "I live in Tripoli" in active and "I live in Paris" not in active


def test_legacy_contradiction_scan_cap_fails_closed(tmp_path, monkeypatch):
    from app.services import memory_capture

    monkeypatch.setattr(memory_capture, "LEGACY_SUBJECT_SCAN", 1)
    monkeypatch.setattr(memory_capture, "LEGACY_SUBJECT_SCAN_MAX", 2)
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        with closing(sqlite3.connect(file)) as db, db:
            db.executemany(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES (?, ?, ?, 'text-embedding-3-small', ?, 1, 'active', "
                "'2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00')",
                [(f"aa-{index:04d}", owner, f"Unrelated legacy note {index}", chat) for index in range(2)],
            )
            db.commit()

        new_fact = turn(client, headers, "I live in Paris", "cap-1").json()["memory_proposal"]
        blocked = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": new_fact["token"], "decision": "save"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "MEMORY_CONTRADICTION_SCAN_INCOMPLETE"
        active = [row[0] for row in memory_rows(file) if row[2] == "active"]
        assert "I live in Paris" not in active
        assert len(active) == 2


def test_replacement_recovers_after_a_lost_decision_receipt(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        headers = add_turn_session(file, chat, owner)
        first = turn(client, headers, "I prefer tea", "rec-1").json()["memory_proposal"]
        client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": first["token"], "decision": "save"}
        )
        second = turn(client, headers, "I prefer coffee", "rec-2").json()["memory_proposal"]
        clarify = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": second["token"], "decision": "save"}
        )
        detail = clarify.json()["detail"]
        replaced = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert replaced.status_code == 200

        with closing(sqlite3.connect(file)) as db, db:
            db.execute("DELETE FROM memory_capture_decisions")
            db.commit()

        retried = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "corrected"
        assert [row[0] for row in memory_rows(file) if row[2] == "active"] == ["I prefer coffee"]

        again = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": detail["token"], "decision": "replace"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "corrected"

        with closing(sqlite3.connect(file)) as db, db:
            receipt = db.execute(
                "SELECT target_id,target_version,outcome FROM memory_capture_decisions "
                "WHERE confirmation_id = ? AND revision = ?",
                (detail["confirmation_id"], detail["confirmation_revision"]),
            ).fetchone()
        assert receipt == (detail["target_id"], detail["target_version"], "corrected")


class _FakeSpeech:
    def __init__(self, *, interrupted=False, error=None):
        self.id = "speech"
        self.interrupted = interrupted
        self._error = error
        self.waited = False
        self.chat_items = ()

    async def wait_for_playout(self):
        self.waited = True

    def exception(self):
        return self._error


def _question_context():
    from app.domain.context import prompt_sha256
    from test_generation import request

    written = request()
    messages = [message.model_dump() for message in written.messages]
    return written.turn_id, {
        "revision": 7,
        "messages": messages,
        "canonical_prompt_sha256": prompt_sha256(messages),
        "model_request": written.model_dump(mode="json"),
    }


def _question_proposal():
    return {
        "token": "signed",
        "text": "I prefer tea",
        "confirmation_id": "cid",
        "confirmation_revision": 1,
        "question": "Should I remember this: I prefer tea?",
    }


def _session_with(handle):
    from types import SimpleNamespace

    return SimpleNamespace(current_speech=handle, llm=None)


def test_worker_arms_consent_only_after_completed_playout(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from livekit.agents import llm
    from main import SarjyAgent

    handle = _FakeSpeech()
    monkeypatch.setattr(SarjyAgent, "session", property(lambda self: _session_with(handle)))
    paths = []

    async def run():
        turn_id, context = _question_context()

        def backend(request):
            paths.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.prepared["m"] = {"turn_id": turn_id, "context": context, "memory_proposal": _question_proposal()}
            generated = []

            async def stream(request, tools, tool_choice):
                generated.append(True)
                yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="model"))

            agent._stream_with_fallback = stream
            chat = llm.ChatContext()
            chat.add_message(role="user", content="I prefer tea", id="m")
            chunks = [chunk async for chunk in agent.llm_node(chat, [], None)]
            text = "".join(chunk.delta.content or "" for chunk in chunks)
            assert "Should I remember this" in text
            assert generated == [], "the model must not run before the question is delivered"
            assert agent.memory_prompted_key is None
            assert agent.memory_question_key == ("cid", 1)

            await asyncio.gather(*agent.pending)
            assert handle.waited is True
            assert agent.memory_prompted_key == ("cid", 1)

            # Repeated inference for the same revision must not re-emit or
            # re-arm the question.
            again = [chunk async for chunk in agent.llm_node(chat, [], None)]
            assert "Should I remember" not in "".join(chunk.delta.content or "" for chunk in again)
            assert generated == [True]
            assert agent.memory_question_key == ("cid", 1)
            await asyncio.gather(*agent.pending)

            # The completed playout is now valid consent for exactly this tuple.
            agent.prepared["next"] = {"turn_id": "turn-2", "context": {}, "memory_proposal": None}
            await agent.compile(SimpleNamespace(id="next", text_content="save it"))
            assert agent.memory_proposal is None

    asyncio.run(run())
    confirmations = [body for path, body in paths if path.endswith("/memory/confirm")]
    assert confirmations == [{"token": "signed", "decision": "save"}]


def test_worker_does_not_arm_consent_when_question_generator_is_stopped(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from livekit.agents import llm
    from main import SarjyAgent

    handle = _FakeSpeech()
    monkeypatch.setattr(SarjyAgent, "session", property(lambda self: _session_with(handle)))
    paths = []

    async def run():
        def backend(request):
            paths.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.prepared["m"] = {"turn_id": "turn", "context": {}, "memory_proposal": _question_proposal()}
            chat = llm.ChatContext()
            chat.add_message(role="user", content="I prefer tea", id="m")
            generator = agent.llm_node(chat, [], None)
            first = await anext(generator)
            assert "Should I remember this" in (first.delta.content or "")
            # Stopped before TTS accepted the text: the code after the yield
            # never runs, so no handle is observed and no prompt is armed.
            await generator.aclose()
            assert handle.waited is False
            assert agent.memory_prompted_key is None
            assert not agent.pending

            agent.prepared["next"] = {"turn_id": "turn-2", "context": {}, "memory_proposal": None}
            await agent.compile(SimpleNamespace(id="next", text_content="save it"))
            assert agent.memory_proposal is None and agent.memory_outcome == "not_saved"

    asyncio.run(run())
    assert all(not path.endswith("/memory/confirm") for path, _ in paths)


@pytest.mark.parametrize("handle_kwargs", [{"interrupted": True}, {"error": RuntimeError("tts failed")}])
def test_worker_does_not_arm_consent_when_question_playout_is_not_clean(monkeypatch, handle_kwargs):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from livekit.agents import llm
    from main import SarjyAgent

    handle = _FakeSpeech(**handle_kwargs)
    monkeypatch.setattr(SarjyAgent, "session", property(lambda self: _session_with(handle)))
    paths = []

    async def run():
        def backend(request):
            paths.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.prepared["m"] = {"turn_id": "turn", "context": {}, "memory_proposal": _question_proposal()}
            chat = llm.ChatContext()
            chat.add_message(role="user", content="I prefer tea", id="m")
            _ = [chunk async for chunk in agent.llm_node(chat, [], None)]
            await asyncio.gather(*agent.pending)
            assert handle.waited is True
            assert agent.memory_prompted_key is None

            agent.prepared["next"] = {"turn_id": "turn-2", "context": {}, "memory_proposal": None}
            await agent.compile(SimpleNamespace(id="next", text_content="save it"))
            assert agent.memory_proposal is None and agent.memory_outcome == "not_saved"

    asyncio.run(run())
    assert all(not path.endswith("/memory/confirm") for path, _ in paths)


def test_worker_refuses_consent_for_an_unprompted_revision():
    import asyncio
    from types import SimpleNamespace

    import httpx
    from main import SarjyAgent

    paths = []

    async def run():
        def backend(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.memory_proposal = {"token": "signed", "confirmation_id": "cid", "confirmation_revision": 2}
            agent.memory_clarify = True
            agent.memory_prompted_key = None
            await agent.compile(SimpleNamespace(id="m", text_content="replace it"))
            assert agent.memory_proposal is None
            assert agent.memory_outcome == "not_saved"

    asyncio.run(run())
    assert paths == ["/internal/agent/turn/start"]
