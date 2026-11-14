"""Reviewer invitation admission, authorization, limits and operator CLI."""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings
from app.dependencies import COOKIE
from app.infrastructure.database import Base
from app.main import create_app
from app.services.reviewer_access import (
    CREATION_LIMIT,
    DEFAULT_MAX_IDENTITIES,
    INVITATION_TTL,
    SOURCE_ATTEMPT_LIMIT,
    ReviewerAccessDenied,
    admit,
    authorize,
    create_invitation,
    revoke_invitation,
    token_hash,
)
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

WEB_SECRET = "reviewer-test-web-secret"
HEADERS = {"x-web-service-secret": WEB_SECRET}
ROOT = Path(__file__).resolve().parents[1]
START = datetime(2031, 3, 4, 10, 15, tzinfo=UTC)


class Clock:
    def __init__(self, value=START):
        self.value = value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def settings(tmp_path, name="reviewer.db", **overrides):
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/{name}",
        "reviewer_access_enabled": True,
        "cookie_secure": True,
        "development_protected": True,
        "web_service_secret": WEB_SECRET,
        "openai_api_key": "",
        "deepseek_api_key": "",
        "deepgram_api_key": "",
        "eleven_api_key": "",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def app_for(tmp_path, clock, **overrides):
    config = settings(tmp_path, **overrides)
    return create_app(config, clock=lambda: clock.value), config


def client_for(app):
    client = TestClient(app, base_url="https://testserver")
    client.headers.update(HEADERS)
    return client


def run(coroutine):
    return asyncio.run(coroutine)


def seed_invitation(config, clock, *, expires_at=None, max_identities=DEFAULT_MAX_IDENTITIES, max_calls=24):
    async def execute():
        engine = create_async_engine(config.database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                invitation, token = await create_invitation(
                    db,
                    expires_at=None if expires_at is not None and expires_at <= clock.value else expires_at,
                    now=clock.value,
                    max_identities=max_identities,
                    max_calls=max_calls,
                )
                if expires_at is not None and expires_at <= clock.value:
                    # The service refuses to create an expired credential; forge
                    # the stored value to exercise a link that has since expired.
                    invitation.expires_at = expires_at.isoformat()
                    await db.commit()
                return invitation.id, token
        finally:
            await engine.dispose()

    return run(execute())


def revoke(config, invitation_id, clock):
    async def execute():
        engine = create_async_engine(config.database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                return await revoke_invitation(db, invitation_id, clock.value)
        finally:
            await engine.dispose()

    return run(execute())


def rows(config, sql):
    path = config.database_url.removeprefix("sqlite+aiosqlite:///")

    with closing(sqlite3.connect(path)) as connection, connection:
        return connection.execute(sql).fetchall()


def test_disabled_mode_keeps_development_behavior(tmp_path):
    config = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/disabled.db",
        reviewer_access_enabled=False,
        _env_file=None,
    )
    with TestClient(create_app(config)) as client:
        assert client.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}).status_code == 404
        owner = client.post("/v1/demo-session").json()["id"]
        assert client.post("/v1/demo-session").json()["id"] == owner
        assert client.get("/v1/conversations").json() == []


def test_reviewer_mode_requires_secure_configuration_before_engine(tmp_path, monkeypatch):
    base = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/config.db",
        "_env_file": None,
        "reviewer_access_enabled": True,
    }
    with pytest.raises(ValueError, match="REVIEWER_ACCESS_REQUIRES_SECURE_COOKIES"):
        Settings(**base, cookie_secure=False, development_protected=True, web_service_secret=WEB_SECRET)
    with pytest.raises(ValueError, match="REVIEWER_ACCESS_REQUIRES_API_PROTECTION"):
        Settings(**base, cookie_secure=True, development_protected=False, web_service_secret=WEB_SECRET)
    with pytest.raises(ValueError, match="REVIEWER_ACCESS_REQUIRES_WEB_SERVICE_SECRET"):
        Settings(**base, cookie_secure=True, development_protected=True, web_service_secret="")

    # A pre-validated settings object must still be rejected before the engine
    # is allocated.
    import app.main as main

    def explode(*args, **kwargs):
        raise AssertionError("engine allocated before reviewer validation")

    monkeypatch.setattr(main, "create_async_engine", explode)
    unsafe = Settings.model_construct(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/config.db",
        reviewer_access_enabled=True,
        cookie_secure=False,
        development_protected=True,
        web_service_secret=WEB_SECRET,
        deepseek_budget_enabled=False,
    )
    with pytest.raises(ValueError, match="REVIEWER_ACCESS_CONFIGURATION_INVALID"):
        create_app(unsafe)


def test_two_browsers_with_one_invitation_get_isolated_owners(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as alice, closing(client_for(app)) as bob:
        invitation_id, token = seed_invitation(config, clock)
        first = alice.post("/v1/reviewer/session", json={"invitation_token": token})
        second = bob.post("/v1/reviewer/session", json={"invitation_token": token})
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] != second.json()["id"]
        assert first.headers["cache-control"] == "no-store"
        assert "httponly" in first.headers["set-cookie"].lower()
        assert "secure" in first.headers["set-cookie"].lower()
        assert "samesite=lax" in first.headers["set-cookie"].lower()
        assert "domain=" not in first.headers["set-cookie"].lower()
        conversation = alice.post("/v1/conversations").json()["id"]
        assert bob.get(f"/v1/conversations/{conversation}/turns").status_code == 404
        assert bob.get("/v1/conversations").json() == []
        assert alice.get("/v1/conversations").json()[0]["id"] == conversation
        assert len(rows(config, "SELECT id FROM identities")) == 2
        assert {
            row[0]
            for row in rows(config, f"SELECT invitation_id FROM identities WHERE invitation_id='{invitation_id}'")
        } == {invitation_id}


def test_reentry_preserves_identity_content_and_creation_allowance(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        _, token = seed_invitation(config, clock)
        other_id, other_token = seed_invitation(config, clock)
        admitted = client.post("/v1/reviewer/session", json={"invitation_token": token}).json()
        conversation = client.post("/v1/conversations").json()["id"]
        before = rows(config, "SELECT id, expires_at, invitation_id FROM identities")
        created_before = rows(config, "SELECT created FROM reviewer_creation_windows")

        again = client.post("/v1/reviewer/session", json={"invitation_token": token})
        switched = client.post("/v1/reviewer/session", json={"invitation_token": other_token})
        assert again.json() == admitted
        assert switched.json() == admitted
        assert again.headers.get("set-cookie") is None
        assert switched.headers.get("set-cookie") is None
        invalid = client.post("/v1/reviewer/session", json={"invitation_token": "Z" * 43})
        assert invalid.status_code == 403
        assert invalid.json() == {"detail": {"code": "REVIEWER_ACCESS_DENIED"}}
        assert rows(config, "SELECT id, expires_at, invitation_id FROM identities") == before
        assert client.get(f"/v1/conversations/{conversation}/turns").status_code == 200
        assert rows(config, "SELECT created FROM reviewer_creation_windows") == created_before
        assert rows(config, f"SELECT created FROM reviewer_creation_windows WHERE invitation_id='{other_id}'") == []
        revoke(config, before[0][2], clock)
        rebound = client.post("/v1/reviewer/session", json={"invitation_token": other_token})
        assert rebound.status_code == 403
        assert rows(config, "SELECT id, expires_at, invitation_id FROM identities") == before


def test_invalid_credentials_fail_generically_without_reflection(tmp_path, caplog):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    sentinel = "A" * 43
    with caplog.at_level(logging.DEBUG), client_for(app) as client:
        seed_invitation(config, clock)
        _, expired_token = seed_invitation(config, clock, expires_at=clock.value - timedelta(minutes=1))
        revoked_id, revoked_token = seed_invitation(config, clock)
        revoke(config, revoked_id, clock)

        cases = [
            ({"invitation_token": sentinel}, 403),
            ({"invitation_token": "short"}, 403),
            ({"invitation_token": sentinel[::-1], "extra": True}, 403),
            ({"wrong": sentinel}, 403),
            ({"invitation_token": expired_token}, 403),
            ({"invitation_token": revoked_token}, 403),
        ]
        for payload, status in cases:
            response = client.post("/v1/reviewer/session", json=payload)
            assert response.status_code == status
            assert response.json() == {"detail": {"code": "REVIEWER_ACCESS_DENIED"}}
            assert "set-cookie" not in response.headers
            assert sentinel not in response.text

        oversized = client.post("/v1/reviewer/session", content=b"x" * 1025)
        assert oversized.status_code == 413
        assert oversized.json() == {"detail": {"code": "REVIEWER_REQUEST_TOO_LARGE"}}
        assert rows(config, "SELECT id FROM identities") == []
        for value in (sentinel, expired_token, revoked_token):
            assert value not in caplog.text
        assert sentinel not in json.dumps(app.openapi())


def test_legacy_cookie_demo_session_and_metadata_cannot_bypass(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    legacy = "legacy-browser-token"
    with client_for(app) as client:
        path = config.database_url.removeprefix("sqlite+aiosqlite:///")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "INSERT INTO identities (id, token_hash, expires_at) VALUES (?, ?, ?)",
                ("legacy-owner", token_hash(legacy), (clock.value + timedelta(days=1)).isoformat()),
            )
        client.cookies.set(COOKIE, legacy)
        for method, endpoint, body in (
            ("get", "/v1/conversations", None),
            ("get", "/v1/pipelines/providers", None),
            ("post", "/v1/pipelines/validate", {"stt": {}, "llm": {}, "tts": {}}),
            ("post", "/v1/demo-session", None),
            ("get", "/v1/voices", None),
        ):
            response = (
                getattr(client, method)(endpoint) if body is None else getattr(client, method)(endpoint, json=body)
            )
            assert response.status_code == 401, endpoint
            assert response.json()["detail"]["code"] == "REVIEWER_SESSION_REQUIRED"
        client.cookies.clear()
        assert client.get("/v1/pipelines/providers").status_code == 401
        assert client.get("/healthz").status_code == 200
        assert client.post("/internal/agent/bootstrap").status_code == 401


def test_revocation_denies_access_and_new_work_but_allows_cleanup(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        invitation_id, token = seed_invitation(config, clock)
        owner = client.post("/v1/reviewer/session", json={"invitation_token": token}).json()["id"]
        conversation = client.post("/v1/conversations").json()["id"]
        path = config.database_url.removeprefix("sqlite+aiosqlite:///")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "INSERT INTO voice_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "voice-1",
                    conversation,
                    owner,
                    "room-1",
                    "ended",
                    (clock.value + timedelta(minutes=5)).isoformat(),
                    "{}",
                    clock.value.isoformat(),
                ),
            )
            connection.execute("INSERT INTO development_budgets VALUES ('2031-03-04','openai',7)")
        revoke(config, invitation_id, clock)

        for method, endpoint, body in (
            ("get", "/v1/conversations", None),
            ("post", "/v1/conversations", None),
            ("post", "/v1/voice/sessions", {"conversation_id": conversation}),
            ("get", "/v1/settings", None),
        ):
            response = (
                getattr(client, method)(endpoint) if body is None else getattr(client, method)(endpoint, json=body)
            )
            assert response.status_code == 403, endpoint
            assert response.json()["detail"]["code"] == "REVIEWER_ACCESS_DENIED"

        cleanup = client.post("/v1/voice/sessions/voice-1/end")
        assert cleanup.status_code == 200
        assert cleanup.json() == {"status": "ended", "cleanup": "already_complete"}
        assert "set-cookie" not in cleanup.headers
        # Revocation preserves identities, content and ledger history.
        assert rows(config, "SELECT id FROM conversations") == [(conversation,)]
        assert rows(config, "SELECT id FROM identities") == [(owner,)]
        assert rows(config, "SELECT reserved_units FROM development_budgets") == [(7,)]


def test_get_session_creates_and_renews_nothing(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        _, token = seed_invitation(config, clock)
        admitted = client.post("/v1/reviewer/session", json={"invitation_token": token}).json()
        identity_before = rows(config, "SELECT id, expires_at, token_hash FROM identities")
        windows_before = rows(config, "SELECT minute, attempts FROM reviewer_attempt_windows")
        creation_before = rows(config, "SELECT invitation_id, hour, created FROM reviewer_creation_windows")
        clock.advance(hours=1)
        response = client.get("/v1/reviewer/session")
        assert response.status_code == 200
        assert response.json() == admitted
        assert response.headers["cache-control"] == "no-store"
        assert response.headers.get("set-cookie") is None
        assert rows(config, "SELECT id, expires_at, token_hash FROM identities") == identity_before
        assert rows(config, "SELECT minute, attempts FROM reviewer_attempt_windows") == windows_before
        assert rows(config, "SELECT invitation_id, hour, created FROM reviewer_creation_windows") == creation_before
        client.cookies.clear()
        assert client.get("/v1/reviewer/session").status_code == 401
        assert rows(config, "SELECT id, expires_at, token_hash FROM identities") == identity_before


def test_per_source_attempt_limit_is_exact_and_survives_restart(tmp_path):
    clock = Clock()
    clock.advance(seconds=15)
    app, config = app_for(tmp_path, clock)
    source = {"x-reviewer-source": "203.0.113.10"}
    with client_for(app) as client:
        for _ in range(SOURCE_ATTEMPT_LIMIT):
            assert (
                client.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=source).status_code
                == 403
            )
        denied = client.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=source)
        assert denied.status_code == 429
        assert denied.json() == {"detail": {"code": "REVIEWER_ADMISSION_LIMIT"}}
        assert denied.headers["retry-after"] == "45"
        assert rows(config, "SELECT attempts FROM reviewer_source_attempt_windows") == [(SOURCE_ATTEMPT_LIMIT,)]
        # The global row is only a backstop and is far from exhausted.
        assert rows(config, "SELECT attempts FROM reviewer_attempt_windows") == [(SOURCE_ATTEMPT_LIMIT,)]

    with client_for(app) as restarted:
        denied = restarted.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=source)
        assert denied.status_code == 429
        assert denied.headers["retry-after"] == "45"
        clock.advance(seconds=45)
        assert (
            restarted.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=source).status_code
            == 403
        )


def test_one_noisy_source_cannot_lock_out_another_source(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    noisy = {"x-reviewer-source": "203.0.113.66"}
    quiet = {"x-reviewer-source": "198.51.100.7"}
    with client_for(app) as client:
        for _ in range(SOURCE_ATTEMPT_LIMIT):
            client.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=noisy)
        assert (
            client.post("/v1/reviewer/session", json={"invitation_token": "A" * 43}, headers=noisy).status_code == 429
        )
        _, token = seed_invitation(config, clock)
        admitted = client.post("/v1/reviewer/session", json={"invitation_token": token}, headers=quiet)
        assert admitted.status_code == 200
        assert client.get("/v1/conversations", headers=quiet).status_code == 200


def test_creation_limit_is_exact_per_invitation_and_window(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        _, token = seed_invitation(config, clock, max_identities=50)
        owners = set()
        for _ in range(CREATION_LIMIT):
            client.cookies.clear()
            response = client.post("/v1/reviewer/session", json={"invitation_token": token})
            assert response.status_code == 200
            owners.add(response.json()["id"])
        assert len(owners) == CREATION_LIMIT
        assert rows(config, "SELECT created FROM reviewer_creation_windows") == [(CREATION_LIMIT,)]
        # An existing browser reuses its identity without consuming creation allowance.
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 200
        assert rows(config, "SELECT created FROM reviewer_creation_windows") == [(CREATION_LIMIT,)]
        client.cookies.clear()
        denied = client.post("/v1/reviewer/session", json={"invitation_token": token})
        assert denied.status_code == 429
        assert denied.headers["retry-after"] == "2700"
        clock.advance(hours=1)
        client.cookies.clear()
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 200


def test_denied_attempts_have_no_side_effects_and_db_failure_admits_nobody(tmp_path, monkeypatch):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        for _ in range(3):
            assert client.post("/v1/reviewer/session", json={"invitation_token": "B" * 43}).status_code == 403
        assert rows(config, "SELECT id FROM identities") == []
        assert rows(config, "SELECT id FROM voice_sessions") == []
        assert rows(config, "SELECT day FROM development_budgets") == []

        import app.routes.reviewer as reviewer_routes
        from sqlalchemy.exc import SQLAlchemyError

        async def unavailable(*args, **kwargs):
            raise SQLAlchemyError("storage unavailable")

        monkeypatch.setattr(reviewer_routes, "record_attempt", unavailable)
        failed = client.post("/v1/reviewer/session", json={"invitation_token": "C" * 43})
        assert failed.status_code == 503
        assert failed.json() == {"detail": {"code": "REVIEWER_UNAVAILABLE"}}
        assert rows(config, "SELECT id FROM identities") == []

        monkeypatch.undo()
        _, token = seed_invitation(config, clock)
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 200
        import app.main as main

        async def broken_authorize(*args, **kwargs):
            raise SQLAlchemyError("storage unavailable")

        monkeypatch.setattr(main, "authorize", broken_authorize)
        failed = client.get("/v1/conversations")
        assert failed.status_code == 503
        assert failed.json() == {"detail": {"code": "REVIEWER_UNAVAILABLE"}}


def test_concurrent_admission_and_revocation_converges_to_denied(tmp_path):
    clock = Clock()
    _, config = app_for(tmp_path, clock)

    async def run_case():
        engine = create_async_engine(config.database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            for _ in range(10):
                async with factory() as db:
                    invitation, token = await create_invitation(db, expires_at=None, now=clock.value)
                async with factory() as admission_db, factory() as revoke_db:
                    results = await asyncio.gather(
                        admit(admission_db, "", token, clock.value),
                        revoke_invitation(revoke_db, invitation.id, clock.value),
                        return_exceptions=True,
                    )
                admitted = results[0]
                assert results[1] is True
                if not isinstance(admitted, Exception):
                    async with factory() as db:
                        with pytest.raises(ReviewerAccessDenied):
                            await authorize(db, admitted.cookie_token, clock.value)
        finally:
            await engine.dispose()

    run(run_case())


def test_invitation_lifetime_identity_cap_is_permanent(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        _, token = seed_invitation(config, clock, max_identities=2)
        for _ in range(2):
            client.cookies.clear()
            assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 200
        client.cookies.clear()
        exhausted = client.post("/v1/reviewer/session", json={"invitation_token": token})
        assert exhausted.status_code == 403
        assert exhausted.json() == {"detail": {"code": "REVIEWER_ACCESS_DENIED"}}
        # Waiting past the hourly window cannot reopen a lifetime-exhausted link.
        clock.advance(hours=2)
        client.cookies.clear()
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 403
        assert rows(config, "SELECT identities_created, max_identities FROM reviewer_invitations") == [(2, 2)]
        assert len(rows(config, "SELECT id FROM identities")) == 2


def test_invitation_expiry_is_required_and_defaults_to_72_hours(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with TestClient(app):
        pass

    async def execute():
        engine = create_async_engine(config.database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                invitation, _ = await create_invitation(db, expires_at=None, now=clock.value)
                assert invitation.expires_at == (clock.value + INVITATION_TTL).isoformat()
                with pytest.raises(ValueError, match="INVITATION_EXPIRY_IN_PAST"):
                    await create_invitation(db, expires_at=clock.value - timedelta(seconds=1), now=clock.value)
                with pytest.raises(ValueError, match="INVITATION_MAX_IDENTITIES_INVALID"):
                    await create_invitation(db, expires_at=None, now=clock.value, max_identities=0)
        finally:
            await engine.dispose()

    run(execute())


def test_reviewer_session_and_app_routes_need_no_authorization_header(tmp_path):
    """B9: reviewer mode keeps API protection but not the shared web password."""
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with TestClient(app, base_url="https://testserver") as client:  # no Authorization header
        client.headers.update(HEADERS)
        _, token = seed_invitation(config, clock)
        response = client.post("/v1/reviewer/session", json={"invitation_token": token})
        assert response.status_code == 200
        assert "authorization" not in {name.lower() for name in response.request.headers}
        assert client.get("/v1/conversations").status_code == 200
        assert client.post("/v1/conversations").status_code == 201


def test_demo_session_cannot_mint_in_reviewer_mode(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with client_for(app) as client:
        anonymous = client.post("/v1/demo-session")
        assert anonymous.status_code == 401
        assert anonymous.json()["detail"]["code"] == "REVIEWER_SESSION_REQUIRED"
        assert rows(config, "SELECT id FROM identities") == []

        _, token = seed_invitation(config, clock)
        owner = client.post("/v1/reviewer/session", json={"invitation_token": token}).json()["id"]
        echoed = client.post("/v1/demo-session")
        assert echoed.status_code == 200 and echoed.json() == {"id": owner}
        assert rows(config, "SELECT id FROM identities") == [(owner,)]


def test_invitation_call_cap_denies_new_voice_sessions(tmp_path):
    clock = Clock()
    config = settings(
        tmp_path,
        openai_api_key="test-openai",
        deepseek_api_key="test-deepseek",
        deepgram_api_key="test-deepgram",
        eleven_api_key="test-eleven",
        default_llm_provider="openai_direct",
        livekit_url="https://fake.livekit",
        livekit_api_key="key" * 8,
        livekit_api_secret="secret" * 8,
        agent_service_secret="agent-secret" * 4,
    )

    class FakeCatalog:
        async def require(self, voice_id):
            return type("Voice", (), {"id": voice_id, "name": "Test Voice"})()

    app = create_app(config, voice_catalog=FakeCatalog(), clock=lambda: clock.value)
    with client_for(app) as client:
        _, token = seed_invitation(config, clock, max_calls=1)
        owner = client.post("/v1/reviewer/session", json={"invitation_token": token}).json()["id"]
        conversation = client.post("/v1/conversations").json()["id"]
        path = config.database_url.removeprefix("sqlite+aiosqlite:///")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "INSERT INTO voice_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "voice-1",
                    conversation,
                    owner,
                    "room-1",
                    "ended",
                    (clock.value + timedelta(minutes=5)).isoformat(),
                    "{}",
                    clock.value.isoformat(),
                ),
            )
        denied = client.post("/v1/voice/sessions", json={"conversation_id": conversation, "voice_id": "test-voice"})
        assert denied.status_code == 429
        assert denied.json()["detail"]["code"] == "REVIEWER_INVITATION_BUDGET_EXHAUSTED"


def test_cli_create_list_revoke_permissions_and_output(tmp_path):
    path = tmp_path / "cli.db"
    database_url = f"sqlite+aiosqlite:///{path}"
    clock = Clock()

    async def bootstrap():
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    run(bootstrap())
    env = os.environ | {"DATABASE_URL": database_url}
    link = tmp_path / "invite.link"
    command = [sys.executable, str(ROOT / "scripts/reviewer_invitations.py")]

    created = subprocess.run(
        command
        + [
            "create",
            "--base-url",
            "https://reviewer.example.test",
            "--expires-at",
            "2031-04-01T00:00:00+00:00",
            "--link-file",
            str(link),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    assert link.stat().st_mode & 0o777 == 0o600
    content = link.read_text().strip()
    assert content.startswith("https://reviewer.example.test/review#invitation=")
    token = content.rsplit("=", 1)[1]
    assert len(token) == 43
    assert token not in created.stdout + created.stderr
    assert str(link) in created.stdout

    duplicate = subprocess.run(
        command + ["create", "--base-url", "https://reviewer.example.test", "--link-file", str(link)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert duplicate.returncode == 1
    assert token not in duplicate.stdout + duplicate.stderr

    listed = subprocess.run(command + ["list"], env=env, capture_output=True, text=True, timeout=60)
    assert listed.returncode == 0
    metadata = json.loads(listed.stdout)
    assert metadata[0]["id"]
    assert set(metadata[0]) == {
        "id",
        "created_at",
        "expires_at",
        "revoked_at",
        "max_identities",
        "identities_created",
        "remaining_identities",
        "max_calls",
    }
    assert metadata[0]["remaining_identities"] == DEFAULT_MAX_IDENTITIES
    assert token not in listed.stdout
    assert token_hash(token) not in listed.stdout

    invitation_id = metadata[0]["id"]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO identities (id, token_hash, expires_at, invitation_id) VALUES (?, ?, ?, ?)",
            ("owner-1", token_hash("cookie"), clock.value.isoformat(), invitation_id),
        )
    for _ in range(2):
        revoked = subprocess.run(
            command + ["revoke", "--invitation-id", invitation_id], env=env, capture_output=True, text=True, timeout=60
        )
        assert revoked.returncode == 0
        assert token not in revoked.stdout + revoked.stderr
    config = Settings(database_url=database_url, reviewer_access_enabled=False, _env_file=None)
    assert rows(config, "SELECT id FROM identities") == [("owner-1",)]
    assert rows(config, "SELECT revoked_at FROM reviewer_invitations")[0][0] is not None


def test_proxy_secret_boundary_remains_outermost(tmp_path):
    clock = Clock()
    app, config = app_for(tmp_path, clock)
    with TestClient(app, base_url="https://testserver") as client:
        _, token = seed_invitation(config, clock)
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}).status_code == 401
        assert client.get("/v1/conversations").status_code == 401
        assert client.post("/v1/reviewer/session", json={"invitation_token": token}, headers=HEADERS).status_code == 200
        assert client.get("/healthz").status_code == 200


def test_stored_invitation_keeps_only_hash(tmp_path):
    clock = Clock()
    _, config = app_for(tmp_path, clock)

    async def make():
        engine = create_async_engine(config.database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    run(make())
    _, token = seed_invitation(config, clock)
    assert token_hash(token) != token
    assert rows(config, "SELECT token_hash FROM reviewer_invitations") == [(token_hash(token),)]


def test_root_path_prefix_does_not_bypass_the_reviewer_gate(tmp_path):
    import app.main as main
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    clock = Clock()
    config = settings(tmp_path)

    class Unavailable:
        async def __aenter__(self):
            raise RuntimeError("storage unavailable")

        async def __aexit__(self, *exc):
            return False

    calls = []

    async def call_next(request):
        calls.append(request.url.path)
        return JSONResponse({"ok": True})

    gate = main._reviewer_gate(config, lambda: Unavailable(), lambda: clock.value)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/proxy/v1/conversations",
        "root_path": "/proxy",
        "headers": [],
        "query_string": b"",
    }
    assert main._route_path(scope) == "/v1/conversations"
    response = run(gate(Request(scope), call_next))
    assert response.status_code == 503
    assert calls == []
