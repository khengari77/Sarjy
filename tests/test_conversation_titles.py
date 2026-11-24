import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import Settings
from app.infrastructure.database import Base, Conversation, Identity, Turn
from app.main import create_app
from app.services import titles
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _reset_title_attempts():
    titles._ATTEMPTED.clear()
    yield
    titles._ATTEMPTED.clear()


def _seed(
    tmp_path, *, title=titles.SENTINEL_TITLE, turns=(("user", "Plan a trip to Paris"), ("assistant", "Four days"))
):
    file = tmp_path / "titles.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            db.add(Identity(id="owner", token_hash="hash", expires_at="2999-01-01T00:00:00+00:00"))
            db.add(Conversation(id="chat", owner_id="owner", title=title, revision=0))
            for index, (role, text) in enumerate(turns):
                db.add(
                    Turn(
                        id=f"turn-{index}",
                        conversation_id="chat",
                        request_id=f"request-{index}",
                        role=role,
                        text=text,
                        status="completed",
                        created_at=f"2026-01-01T00:00:0{index}+00:00",
                    )
                )
            await db.commit()

    asyncio.run(seed())
    return file, engine, sessions


def _stored_title(file) -> str:
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT title FROM conversations WHERE id = 'chat'").fetchone()[0]


def _recording_transport(content: str, calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def test_generated_title_is_persisted_once(tmp_path):
    file, engine, sessions = _seed(tmp_path)
    calls: list[dict] = []
    transport = _recording_transport('"Paris Trip Planning"', calls)

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
        repeated = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result == "Paris Trip Planning"
    assert repeated is None
    assert _stored_title(file) == "Paris Trip Planning"
    assert len(calls) == 1
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["temperature"] == 0
    assert calls[0]["max_tokens"] == 24
    assert calls[0]["thinking"] == {"type": "disabled"}


def test_renamed_conversation_is_never_overwritten(tmp_path):
    file, engine, sessions = _seed(tmp_path, title="Trip planning")
    calls: list[dict] = []
    transport = _recording_transport("Provider title", calls)

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert calls == []
    assert _stored_title(file) == "Trip planning"


def test_rename_during_provider_call_wins(tmp_path):
    file, engine, sessions = _seed(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Generated title"}}]})

    async def run():
        task = asyncio.create_task(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=httpx.MockTransport(handler),
                reserve_budget=False,
            )
        )
        await started.wait()
        async with sessions() as db:
            conversation = await db.get(Conversation, "chat")
            conversation.title = "Trip planning"
            await db.commit()
        release.set()
        return await task

    try:
        result = asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert _stored_title(file) == "Trip planning"


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(200, json={"choices": []}),
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        lambda request: httpx.Response(200, text="not json"),
    ],
    ids=["timeout", "server-error", "empty-choices", "non-text", "malformed"],
)
def test_provider_failure_keeps_sentinel_without_raising(tmp_path, handler):
    file, engine, sessions = _seed(tmp_path)

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=httpx.MockTransport(handler),
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert _stored_title(file) == titles.SENTINEL_TITLE


def test_provider_timeout_keeps_sentinel(tmp_path):
    file, engine, sessions = _seed(tmp_path)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Too late"}}]})

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=httpx.MockTransport(slow),
                reserve_budget=False,
                timeout=0.01,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert _stored_title(file) == titles.SENTINEL_TITLE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Trip planning"', "Trip planning"),
        ("`Trip planning`", "Trip planning"),
        ("**Trip planning**", "Trip planning"),
        ("Trip\nplanning", "Trip planning"),
        ("Trip planning.", "Trip planning"),
        ("", None),
        ("   ", None),
        ("New conversation", None),
        ("New conversation.", None),
    ],
)
def test_sanitize_title(raw, expected):
    assert titles.sanitize_title(raw) == expected


def test_sanitize_title_caps_length():
    result = titles.sanitize_title("a" * 100)

    assert result == "a" * 60


def test_arabic_first_message_gets_an_arabic_title(tmp_path):
    file, engine, sessions = _seed(tmp_path, turns=(("user", "خطط لرحلة إلى باريس"), ("assistant", "أربعة أيام")))
    calls: list[dict] = []
    transport = _recording_transport("تخطيط رحلة باريس", calls)

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result == "تخطيط رحلة باريس"
    assert _stored_title(file) == "تخطيط رحلة باريس"
    assert "Arabic" in calls[0]["messages"][0]["content"]


def test_first_turn_source_is_truncated(tmp_path):
    file, engine, sessions = _seed(
        tmp_path,
        turns=(("user", "u" * 900), ("assistant", "a" * 900)),
    )
    calls: list[dict] = []
    transport = _recording_transport("Long exchange", calls)

    try:
        asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    content = calls[0]["messages"][1]["content"]
    assert "u" * 600 in content
    assert "u" * 601 not in content
    assert "a" * 600 in content
    assert "a" * 601 not in content


def test_finalize_schedules_title_generation_after_commit(tmp_path, monkeypatch):
    file = tmp_path / "worker.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        deepseek_api_key="test-key",
        _env_file=None,
    )
    calls: list[tuple[str, dict]] = []

    async def fake_generate(sessions, conversation_id, **kwargs):
        calls.append((conversation_id, kwargs))
        return "Generated"

    monkeypatch.setattr("app.routes.worker.generate_conversation_title", fake_generate)

    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
                "VALUES (?,?,?,?,?,?,?)",
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
        headers = {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}
        started = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "one", "text": "Hello"}
        ).json()
        assert client.get("/v1/conversations").json()[0]["title"] == titles.SENTINEL_TITLE
        client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": started["turn_id"], "text": "Hi", "status": "completed"},
        )

    assert len(calls) == 1
    assert calls[0][0] == chat
    assert calls[0][1]["api_key"] == "test-key"


def _hosts_transport(content: str, hosts: list[str], calls: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if calls is not None:
            calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _reserved(file, provider: str) -> int:
    with closing(sqlite3.connect(file)) as db:
        row = db.execute(
            "SELECT SUM(reserved_units) FROM development_budgets WHERE provider = ?", (provider,)
        ).fetchone()
    return row[0] or 0


def test_openai_provider_uses_guarded_openai_and_never_deepseek(tmp_path):
    file, engine, sessions = _seed(tmp_path)
    hosts: list[str] = []
    calls: list[dict] = []

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="openai-key",
                provider="openai_direct",
                transport=_hosts_transport("Paris Trip Planning", hosts, calls),
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result == "Paris Trip Planning"
    assert hosts == ["api.openai.com"]
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning_effort"] == "none"
    assert calls[0]["max_completion_tokens"] == 24
    assert "thinking" not in calls[0]
    assert _reserved(file, "openai") > 0
    assert _reserved(file, "deepseek") == 0


def test_deepseek_provider_uses_deepseek_path(tmp_path):
    file, engine, sessions = _seed(tmp_path)
    hosts: list[str] = []

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="deepseek-key",
                provider="deepseek_direct",
                transport=_hosts_transport("Paris Trip Planning", hosts),
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result == "Paris Trip Planning"
    assert hosts == ["api.deepseek.com"]
    assert _reserved(file, "deepseek") > 0
    assert _reserved(file, "openai") == 0


def test_failed_attempt_is_not_retried_in_this_process(tmp_path):
    file, engine, sessions = _seed(tmp_path)
    calls: list[dict] = []
    transport = _recording_transport("", calls)

    try:
        first = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
            )
        )
        reserved = _reserved(file, "deepseek")
        second = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert first is None
    assert second is None
    assert len(calls) == 1
    assert reserved > 0
    assert _reserved(file, "deepseek") == reserved
    assert _stored_title(file) == titles.SENTINEL_TITLE


def test_conversation_without_assistant_turn_is_not_marked_attempted(tmp_path):
    file, engine, sessions = _seed(tmp_path, turns=(("user", "Plan a trip to Paris"),))
    calls: list[dict] = []
    transport = _recording_transport("Paris Trip Planning", calls)

    async def add_assistant_turn():
        async with sessions() as db:
            db.add(
                Turn(
                    id="assistant-turn",
                    conversation_id="chat",
                    request_id="assistant-request",
                    role="assistant",
                    text="Four days",
                    status="completed",
                    created_at="2026-01-01T00:00:05+00:00",
                )
            )
            await db.commit()

    try:
        first = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
        asyncio.run(add_assistant_turn())
        second = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="test-key",
                transport=transport,
                reserve_budget=False,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert first is None
    assert second == "Paris Trip Planning"
    assert len(calls) == 1
    assert _stored_title(file) == "Paris Trip Planning"


def test_denied_openai_reservation_makes_no_request(tmp_path, monkeypatch):
    file, engine, sessions = _seed(tmp_path)
    hosts: list[str] = []

    async def denied(db, provider, *args, **kwargs):
        raise ValueError("DAILY_BUDGET_EXHAUSTED")

    monkeypatch.setattr("app.services.budget.reserve_in_transaction", denied)

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="openai-key",
                provider="openai_direct",
                transport=_hosts_transport("Never", hosts),
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert hosts == []
    assert _stored_title(file) == titles.SENTINEL_TITLE


@pytest.mark.parametrize("provider", ["openai_direct", "deepseek_direct"])
def test_missing_key_means_no_title_and_no_request(tmp_path, provider):
    file, engine, sessions = _seed(tmp_path)
    hosts: list[str] = []

    try:
        result = asyncio.run(
            titles.generate_conversation_title(
                sessions,
                "chat",
                api_key="  ",
                provider=provider,
                transport=_hosts_transport("Never", hosts),
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result is None
    assert hosts == []
    assert _stored_title(file) == titles.SENTINEL_TITLE


def test_finalize_passes_the_active_provider_and_its_key(tmp_path, monkeypatch):
    file = tmp_path / "provider.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        default_llm_provider="openai_direct",
        openai_api_key="openai-key",
        deepseek_api_key="deepseek-key",
        _env_file=None,
    )
    calls: list[dict] = []

    async def fake_generate(sessions, conversation_id, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.routes.worker.generate_conversation_title", fake_generate)

    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        with closing(sqlite3.connect(file)) as db, db:
            db.execute(
                "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
                "VALUES (?,?,?,?,?,?,?)",
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
        headers = {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}
        started = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "one", "text": "Hello"}
        ).json()
        client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": started["turn_id"], "text": "Hi", "status": "completed"},
        )

    assert calls[0]["provider"] == "openai_direct"
    assert calls[0]["api_key"] == "openai-key"


def test_a_settled_title_is_not_read_again_on_later_turns(tmp_path):
    """After the title is generated (or renamed) no later turn may touch the database for it."""
    file, engine, sessions = _seed(tmp_path, title="My own name")
    opened = 0

    def counting():
        nonlocal opened
        opened += 1
        return sessions()

    try:
        for _ in range(3):
            assert asyncio.run(titles.generate_conversation_title(counting, "chat", api_key="test-key")) is None
    finally:
        asyncio.run(engine.dispose())
    assert opened == 1
