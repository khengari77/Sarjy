import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

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
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "already_saved"
        assert len(memory_rows(file)) == 1

        # A different conversation under the same owner recalls the confirmed fact.
        recalled_chat = client.post("/v1/conversations").json()["id"]
        recalled_headers = add_turn_session(file, recalled_chat, owner, identifier="session-2")
        recalled = turn(client, recalled_headers, "What do I prefer?", "recall-1").json()
        messages = recalled["context"]["model_request"]["messages"]
        recalled_memory = [message for message in messages if "tea with mint" in message["content"]]
        assert recalled_memory and all(message["role"] != "system" for message in recalled_memory)

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
        replaced = client.post(
            "/internal/agent/memory/confirm",
            headers=headers,
            json={"token": without_marker["token"], "decision": "replace"},
        )
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
            agent.memory_proposal = {"token": "signed", "text": "I prefer tea"}
            await agent.compile(SimpleNamespace(id="message", text_content=text))
            return paths, agent.memory_proposal, agent.memory_outcome

    paths, remaining, _ = asyncio.run(exchange("Yes."))
    assert remaining is None
    assert paths == [
        ("/internal/agent/turn/start", {"request_id": "message", "text": "Yes."}),
        ("/internal/agent/memory/confirm", {"token": "signed", "decision": "save"}),
    ]

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
            agent.memory_proposal = {"token": "signed"}
            message = SimpleNamespace(id="message", text_content="Yes, please.")
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
                    return httpx.Response(409, json={"detail": {"code": "MEMORY_CLARIFICATION_REQUIRED"}})
                return httpx.Response(200, json={"status": "corrected"})
            return httpx.Response(200, json={"turn_id": "turn", "context": {}, "memory_proposal": None})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "deepseek_direct"}}, "Rules")
            agent.memory_proposal = {"token": "signed"}
            await agent.compile(SimpleNamespace(id="one", text_content="yes"))
            assert agent.memory_proposal is not None and agent.memory_clarify is True
            await agent.compile(SimpleNamespace(id="two", text_content="yes"))
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
                seen.append(request.instructions)
                yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="updated"))

            agent._stream_with_fallback = stream
            chat = llm.ChatContext()
            chat.add_message(role="user", content="yes", id="message")
            chunks = [chunk async for chunk in agent.llm_node(chat, [], None)]
            assert chunks and "replaced" in seen[0]
            assert agent.memory_outcome is None

    asyncio.run(run())
