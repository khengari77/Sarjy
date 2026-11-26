"""Account sign-up, sign-in, isolation, revocation and limits."""

import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import Settings
from app.dependencies import ACCOUNT_COOKIE, COOKIE
from app.domain.accounts import (
    EMAIL_SOURCE_LIMIT,
    PASSWORD_MAX_BYTES,
    SESSION_TTL,
    SIGN_UP_LIMIT,
    SOURCE_LIMIT,
    AttemptLimit,
    acceptable_password,
    normalized_email,
    retry_after_seconds,
    window_start,
)
from app.main import create_app
from app.services.account_auth import CredentialServiceUnavailable, CredentialsRejected, SupabaseCredentials
from app.services.accounts import open_session
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

WEB = {"x-web-service-secret": "account-test-web-secret"}
START = datetime(2031, 3, 4, 10, 15, tzinfo=UTC)
PASSWORD = "correct horse battery"


class Clock:
    def __init__(self):
        self.value = START

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class FakeCredentials:
    def __init__(self):
        self.users = {}
        self.calls = 0
        self.fail = None

    async def sign_up(self, email, password):
        self.calls += 1
        if self.fail:
            raise self.fail
        if email in self.users:
            raise CredentialsRejected("ACCOUNT_EXISTS")
        self.users[email] = (f"auth-{len(self.users)}", password)
        return self.users[email][0]

    async def sign_in(self, email, password):
        self.calls += 1
        if self.fail:
            raise self.fail
        user = self.users.get(email)
        if user is None or user[1] != password:
            raise CredentialsRejected("ACCOUNT_CREDENTIALS_INVALID")
        return user[0]

    async def exchange_code(self, code, verifier):
        self.calls += 1
        if self.fail:
            raise self.fail
        if code != CODE or verifier != VERIFIER:
            raise CredentialsRejected("ACCOUNT_OAUTH_FAILED")
        return self.users.setdefault("via-provider", ("auth-provider", None))[0]


CODE = "c" * 36
VERIFIER = "v" * 43


def settings(tmp_path, **overrides):
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/accounts.db",
        "account_access_enabled": True,
        "cookie_secure": True,
        "development_protected": True,
        "web_service_secret": WEB["x-web-service-secret"],
        "supabase_url": "https://project.supabase.test",
        "supabase_publishable_key": "sb_publishable_test",
        "openai_api_key": "",
        "deepseek_api_key": "",
        "deepgram_api_key": "",
        "_env_file": None,
    }
    return Settings(**(values | overrides))


class World:
    def __init__(self, tmp_path, credentials=None, **overrides):
        self.path = tmp_path / "accounts.db"
        self.clock = Clock()
        self.credentials = credentials or FakeCredentials()
        self.app = create_app(settings(tmp_path, **overrides), clock=self.clock, credentials=self.credentials)

    def browser(self):
        return TestClient(self.app, base_url="https://testserver", headers=WEB)

    def rows(self, sql, *args):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(sql, args).fetchall()


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    with world.browser():
        yield world


def post(client, action, email="ada@example.test", password=PASSWORD, **kwargs):
    return client.post(f"/v1/auth/{action}", json={"email": email, "password": password}, **kwargs)


def code(response):
    return response.json()["detail"]["code"]


def test_sign_up_reload_second_browser_and_sign_out_follow_one_identity(world):
    first, second = world.browser(), world.browser()
    created = post(first, "sign-up")
    assert created.status_code == 200
    cookie = created.headers["set-cookie"]
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"))
    assert created.headers["cache-control"] == "no-store"
    conversation = first.post("/v1/conversations").json()["id"]
    assert first.post("/v1/demo-session").json()["id"] == created.json()["id"]

    assert post(second, "sign-in", email="  ADA@example.test ").json() == created.json()
    assert [row["id"] for row in second.get("/v1/conversations").json()] == [conversation]

    assert first.post("/v1/auth/sign-out").status_code == 200
    assert first.get("/v1/auth/session").status_code == 401
    assert second.get("/v1/auth/session").status_code == 200


def test_signing_out_revokes_the_token_itself(world):
    client = world.browser()
    post(client, "sign-up")
    stolen = client.cookies[ACCOUNT_COOKIE]
    client.post("/v1/auth/sign-out")
    replay = world.browser().get("/v1/conversations", headers={"cookie": f"{ACCOUNT_COOKIE}={stolen}"})
    assert replay.status_code == 401 and code(replay) == "ACCOUNT_SESSION_REQUIRED"


def test_sign_in_replaces_the_presented_session(world):
    client = world.browser()
    post(client, "sign-up")
    old = client.cookies[ACCOUNT_COOKIE]
    post(client, "sign-in")
    assert client.cookies[ACCOUNT_COOKIE] != old
    assert world.browser().get("/v1/auth/session", headers={"cookie": f"{ACCOUNT_COOKIE}={old}"}).status_code == 401


def test_accounts_never_see_each_other(world):
    ada, bob = world.browser(), world.browser()
    post(ada, "sign-up")
    post(bob, "sign-up", email="bob@example.test")
    conversation = ada.post("/v1/conversations").json()["id"]
    assert bob.get("/v1/conversations").json() == []
    assert bob.get(f"/v1/conversations/{conversation}/turns").status_code == 404


def test_sessions_expire_and_disabled_accounts_are_closed(world):
    client = world.browser()
    post(client, "sign-up")
    world.clock.advance(seconds=SESSION_TTL.total_seconds() - 1)
    assert client.get("/v1/auth/session").status_code == 200
    world.clock.advance(seconds=1)
    assert client.get("/v1/auth/session").status_code == 401
    post(client, "sign-in")
    with closing(sqlite3.connect(world.path)) as connection, connection:
        connection.execute("UPDATE accounts SET disabled_at = ?", (START.isoformat(),))
    assert client.get("/v1/conversations").status_code == 401
    assert code(post(client, "sign-in")) == "ACCOUNT_CREDENTIALS_INVALID"


@pytest.mark.parametrize("path", ["/v1/conversations", "/v1/voices", "/v1/memories", "/v1/anything-new"])
@pytest.mark.parametrize(
    "cookie", [None, f"{ACCOUNT_COOKIE}=x", f"{ACCOUNT_COOKIE}={'A' * 43}", f"{COOKIE}={'A' * 43}"]
)
def test_everything_is_closed_without_a_live_session(world, path, cookie):
    response = world.browser().get(path, headers={"cookie": cookie} if cookie else {})
    assert response.status_code == 401 and code(response) == "ACCOUNT_SESSION_REQUIRED"


def test_old_identities_cannot_enter_and_their_data_is_untouched(world):
    import hashlib

    token = "old-demo-token"
    with closing(sqlite3.connect(world.path)) as connection, connection:
        connection.execute(
            "INSERT INTO identities (id, token_hash, expires_at) VALUES ('old', ?, '2999-01-01T00:00:00+00:00')",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
    client = world.browser()
    post(client, "sign-up")
    old = world.browser().get("/v1/conversations", headers={"cookie": f"{COOKIE}={token}"})
    assert old.status_code == 401
    assert world.browser().post("/v1/demo-session", headers={"cookie": f"{COOKIE}={token}"}).status_code == 401
    assert world.rows("SELECT count(*) FROM identities WHERE id = 'old'") == [(1,)]
    assert client.get("/v1/auth/session").json()["id"] != "old"


def test_sign_in_rejections_do_not_say_which_part_was_wrong(world):
    post(world.browser(), "sign-up")
    wrong = post(world.browser(), "sign-in", password="not the password")
    unknown = post(world.browser(), "sign-in", email="nobody@example.test")
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json())
    assert wrong.status_code == 401 and "set-cookie" not in wrong.headers
    assert code(post(world.browser(), "sign-up")) == "ACCOUNT_EXISTS"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b"{}",
        b"not json",
        b"\xff\xfe",
        json.dumps({"email": "a@b.test", "password": "short"}).encode(),
        json.dumps({"email": "a@b.test", "password": "x" * 73}).encode(),
        json.dumps({"email": "nope", "password": PASSWORD}).encode(),
        json.dumps({"email": "a@b.test", "password": PASSWORD, "admin": True}).encode(),
        json.dumps({"email": ["a@b.test"], "password": PASSWORD}).encode(),
        b"[" * 100_000,
        b" " * 5000,
    ],
)
def test_malformed_input_is_refused_without_echo_or_provider_call(world, body):
    for action in ("sign-up", "sign-in"):
        response = world.browser().post(f"/v1/auth/{action}", content=body)
        assert response.status_code == 422
        assert response.json() == {"detail": {"code": "ACCOUNT_INPUT_INVALID"}}
    assert world.credentials.calls == 0


def test_provider_outage_is_retryable_and_creates_nothing(world):
    world.credentials.fail = CredentialServiceUnavailable()
    for action in ("sign-up", "sign-in"):
        response = post(world.browser(), action)
        assert response.status_code == 503 and code(response) == "ACCOUNT_SERVICE_UNAVAILABLE"
        assert "set-cookie" not in response.headers
    assert world.rows("SELECT count(*) FROM accounts") == [(0,)]


def test_attempts_are_limited_per_email_and_source_before_the_provider_is_called(world):
    for _ in range(EMAIL_SOURCE_LIMIT.attempts):
        assert post(world.browser(), "sign-in", password="wrong password").status_code == 401
    limited = post(world.browser(), "sign-in")
    assert limited.status_code == 429 and code(limited) == "ACCOUNT_ATTEMPT_LIMIT"
    assert int(limited.headers["retry-after"]) >= 1
    assert world.credentials.calls == EMAIL_SOURCE_LIMIT.attempts
    assert post(world.browser(), "sign-in", email="other@example.test").status_code == 401
    # The owner of the address, elsewhere, is not locked out by that source.
    elsewhere = post(world.browser(), "sign-in", headers={"x-reviewer-source": "198.51.100.9"})
    assert elsewhere.status_code == 401
    world.clock.advance(seconds=EMAIL_SOURCE_LIMIT.window.total_seconds())
    assert post(world.browser(), "sign-in").status_code == 401


def test_a_stranger_cannot_lock_an_owner_out_from_elsewhere(world, monkeypatch):
    limit = AttemptLimit("email-source", 2, EMAIL_SOURCE_LIMIT.window)
    monkeypatch.setattr("app.routes.accounts.EMAIL_SOURCE_LIMIT", limit)
    post(world.browser(), "sign-up")
    # 15 foreign sources x 2 failures: 30 failed sign-ins against the owner's email, enough to trip any
    # plausible email-only cap, while each source key stays under its own limit.
    for index in range(15):
        source = {"x-reviewer-source": f"192.0.2.{index}"}
        for _ in range(limit.attempts):
            post(world.browser(), "sign-in", password="wrong password", headers=source)
    assert post(world.browser(), "sign-in").status_code == 200


def test_successes_do_not_use_up_a_shared_address(world):
    post(world.browser(), "sign-up")
    for _ in range(SOURCE_LIMIT.attempts + 5):
        assert post(world.browser(), "sign-in").status_code == 200


def test_a_refused_attempt_counts_against_nothing(world):
    for _ in range(EMAIL_SOURCE_LIMIT.attempts):
        post(world.browser(), "sign-in")
    before = world.rows("SELECT key, attempts FROM account_attempt_windows ORDER BY key")
    assert post(world.browser(), "sign-in").status_code == 429
    assert world.rows("SELECT key, attempts FROM account_attempt_windows ORDER BY key") == before


def test_ended_sessions_are_removed_at_the_next_sign_in(world):
    browser = world.browser()
    post(browser, "sign-up")
    browser.post("/v1/auth/sign-out")
    post(world.browser(), "sign-in")
    assert world.rows("SELECT count(*) FROM account_sessions") == [(1,)]
    world.clock.advance(seconds=SESSION_TTL.total_seconds())
    post(world.browser(), "sign-in")
    assert world.rows("SELECT count(*) FROM account_sessions") == [(1,)]


def test_sign_ups_and_sources_are_limited_and_the_source_comes_from_the_proxy(world):
    def sign_up(index, source):
        return post(world.browser(), "sign-up", email=f"u{index}@example.test", headers={"x-reviewer-source": source})

    for index in range(SIGN_UP_LIMIT.attempts):
        assert sign_up(index, "203.0.113.7").status_code == 200
    assert code(sign_up(90, "203.0.113.7")) == "ACCOUNT_ATTEMPT_LIMIT"
    assert sign_up(91, "198.51.100.9").status_code == 200
    for index in range(SOURCE_LIMIT.attempts):
        post(world.browser(), "sign-in", email=f"x{index}@example.test", headers={"x-reviewer-source": "192.0.2.1"})
    last = post(world.browser(), "sign-in", email="y@example.test", headers={"x-reviewer-source": "192.0.2.1"})
    assert code(last) == "ACCOUNT_ATTEMPT_LIMIT"


def test_no_email_password_address_or_token_is_stored_or_logged(world, caplog):
    client = world.browser()
    with caplog.at_level(logging.DEBUG):
        post(client, "sign-up", headers={"x-reviewer-source": "203.0.113.7"})
        post(client, "sign-in", password="wrong password", headers={"x-reviewer-source": "203.0.113.7"})
    stored = world.path.read_bytes()
    for secret in (b"ada@example.test", PASSWORD.encode(), b"203.0.113.7", client.cookies[ACCOUNT_COOKIE].encode()):
        assert secret not in stored
        assert secret.decode() not in caplog.text


def test_old_attempt_windows_are_pruned(world):
    post(world.browser(), "sign-in")
    world.clock.advance(days=2)
    post(world.browser(), "sign-in")
    windows = {row[0] for row in world.rows("SELECT window FROM account_attempt_windows")}
    assert windows and all(window >= (world.clock() - timedelta(days=1)).isoformat() for window in windows)


def test_concurrent_first_sign_ins_share_one_account(tmp_path):
    world = World(tmp_path)

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{world.path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def enter():
            async with sessions() as db:
                identity, token = await open_session(db, auth_user_id="same", previous_token="", now=START)
                return identity.id, token

        results = await asyncio.gather(*(enter() for _ in range(6)))
        await engine.dispose()
        return results

    with world.browser():
        results = asyncio.run(run())
    assert len({identity for identity, _ in results}) == 1
    assert len({token for _, token in results}) == 6
    assert world.rows("SELECT count(*) FROM accounts") == [(1,)]
    assert world.rows("SELECT count(*) FROM identities") == [(1,)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"reviewer_access_enabled": True},
        {"cookie_secure": False},
        {"development_protected": False},
        {"web_service_secret": ""},
        {"supabase_url": ""},
        {"supabase_url": "http://project.supabase.test"},
        {"supabase_publishable_key": ""},
        {"supabase_publishable_key": "sb_secret_x"},
    ],
)
def test_unsafe_configuration_refuses_to_start(tmp_path, overrides):
    with pytest.raises(ValueError):
        create_app(settings(tmp_path, **overrides))


def test_flag_off_keeps_the_previous_behaviour(tmp_path):
    config = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/off.db", _env_file=None)
    with TestClient(create_app(config)) as client:
        assert client.post("/v1/auth/sign-in", json={}).status_code == 404
        assert client.post("/v1/demo-session").status_code == 200
        assert client.get("/v1/conversations").status_code == 200


# --- provider adapter ------------------------------------------------------


def provider(handler):
    return SupabaseCredentials("https://project.supabase.test/", "sb_key", transport=httpx.MockTransport(handler))


def call(action, handler):
    return asyncio.run(getattr(provider(handler), action)("ada@example.test", PASSWORD))


def test_provider_requests_are_exact():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["apikey"], json.loads(request.content)))
        return httpx.Response(200, json={"access_token": "t", "user": {"id": "user-1"}})

    assert call("sign_up", handler) == call("sign_in", handler) == "user-1"
    body = {"email": "ada@example.test", "password": PASSWORD}
    assert seen == [
        ("https://project.supabase.test/auth/v1/signup", "sb_key", body),
        ("https://project.supabase.test/auth/v1/token?grant_type=password", "sb_key", body),
    ]


@pytest.mark.parametrize(
    ("action", "status", "error", "expected"),
    [
        ("sign_up", 422, "user_already_exists", "ACCOUNT_EXISTS"),
        ("sign_up", 422, "weak_password", "ACCOUNT_PASSWORD_WEAK"),
        ("sign_in", 400, "invalid_credentials", "ACCOUNT_CREDENTIALS_INVALID"),
        ("sign_in", 400, "email_not_confirmed", "ACCOUNT_EMAIL_UNCONFIRMED"),
    ],
)
def test_provider_refusals_map_to_our_codes(action, status, error, expected):
    with pytest.raises(CredentialsRejected) as caught:
        call(action, lambda request: httpx.Response(status, json={"error_code": error, "msg": "private words"}))
    assert caught.value.code == expected and "private words" not in str(caught.value)


def test_sign_up_without_a_session_means_confirmation_is_still_on():
    with pytest.raises(CredentialsRejected) as caught:
        call("sign_up", lambda request: httpx.Response(200, json={"id": "user-1", "identities": []}))
    assert caught.value.code == "ACCOUNT_EMAIL_UNCONFIRMED"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(429, json={"error_code": "over_request_rate_limit"}),
        httpx.Response(302, headers={"location": "https://evil.test"}),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"access_token": "t", "user": {"id": ""}}),
        httpx.Response(200, json={"access_token": "t", "user": {"id": "x" * 65}}),
        httpx.Response(200, json={"access_token": "t"}),
        httpx.Response(400, json={"error_code": ["invalid_credentials"]}),
        httpx.Response(500, json={"error_code": "invalid_credentials"}),
        httpx.Response(200, content=b" " * 70_000),
    ],
)
def test_unexpected_provider_answers_are_unavailable(response):
    for action in ("sign_up", "sign_in"):
        with pytest.raises(CredentialServiceUnavailable):
            call(action, lambda request: response)


def test_transport_failure_is_unavailable_and_logs_no_credential(caplog):
    def broken(request):
        raise httpx.ConnectError(f"cannot send {PASSWORD}")

    with pytest.raises(CredentialServiceUnavailable):
        call("sign_in", broken)
    assert PASSWORD not in caplog.text and "ada@example.test" not in caplog.text


# --- pure policy -----------------------------------------------------------


@given(st.text(max_size=300))
def test_normalized_email_is_stable_and_bounded(value):
    email = normalized_email(value)
    if email is not None:
        assert normalized_email(email) == email
        assert len(email) <= 254 and email.count("@") == 1 and not any(c.isspace() for c in email)


@given(st.text(max_size=120))
def test_accepted_passwords_fit_the_provider(value):
    if acceptable_password(value):
        assert len(value) >= 8 and len(value.encode()) <= PASSWORD_MAX_BYTES and "\x00" not in value


@given(st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1)), st.sampled_from([600, 3600]))
def test_windows_contain_their_instant(naive, seconds):
    now, window = naive.replace(tzinfo=UTC), timedelta(seconds=seconds)
    start = window_start(now, window)
    assert start <= now < start + window
    assert 1 <= retry_after_seconds(now, window) <= seconds + 1


def exchange(client, code=CODE, verifier=VERIFIER):
    return client.post("/v1/auth/oauth/exchange", json={"code": code, "code_verifier": verifier})


def test_a_provider_sign_in_opens_the_same_kind_of_session(world):
    client = world.browser()
    entered = exchange(client)
    assert entered.status_code == 200 and entered.headers["cache-control"] == "no-store"
    assert all(flag in entered.headers["set-cookie"] for flag in ("HttpOnly", "Secure", "SameSite=lax"))
    assert client.get("/v1/auth/session").json() == entered.json()
    assert exchange(world.browser()).json() == entered.json()
    assert client.post("/v1/auth/sign-out").status_code == 200
    assert client.get("/v1/auth/session").status_code == 401


def test_a_wrong_code_is_refused_without_a_session_and_counts_against_the_source(world):
    client = world.browser()
    refused = exchange(client, verifier="w" * 43)
    assert refused.status_code == 400 and code(refused) == "ACCOUNT_OAUTH_FAILED"
    assert "set-cookie" not in refused.headers
    for _ in range(SOURCE_LIMIT.attempts - 1):
        exchange(client, verifier="w" * 43)
    assert exchange(client).status_code == 429


def supabase_provider(identities):
    def handler(request):
        return httpx.Response(200, json={"access_token": "t", "user": {"id": "user-1", "identities": identities}})

    return SupabaseCredentials("https://project.supabase.test", "sb_key", transport=httpx.MockTransport(handler))


def test_an_email_account_is_refused_through_a_provider_and_opens_no_session(tmp_path):
    world = World(tmp_path, credentials=supabase_provider([{"provider": "email"}, {"provider": "google"}]))
    with world.browser() as client:
        refused = exchange(client)
        assert refused.status_code == 409 and code(refused) == "ACCOUNT_OAUTH_EMAIL_TAKEN"
        assert "set-cookie" not in refused.headers
        assert client.get("/v1/auth/session").status_code == 401


def test_a_provider_only_identity_opens_a_session(tmp_path):
    world = World(tmp_path, credentials=supabase_provider([{"provider": "google"}]))
    with world.browser() as client:
        entered = exchange(client)
        assert entered.status_code == 200
        assert client.get("/v1/auth/session").json() == entered.json()


def test_a_successful_exchange_gives_the_source_attempt_back(world):
    client = world.browser()
    for _ in range(SOURCE_LIMIT.attempts - 1):
        assert exchange(client, verifier="w" * 43).status_code == 400
    assert exchange(client).status_code == 200
    # The success did not use up the address: another failure still reaches the provider.
    assert exchange(client, verifier="w" * 43).status_code == 400


def test_provider_sign_ups_share_the_sign_up_limit(world):
    client = world.browser()
    for _ in range(SIGN_UP_LIMIT.attempts):
        assert exchange(client).status_code == 200
    calls = world.credentials.calls
    refused = exchange(client)
    assert refused.status_code == 429 and code(refused) == "ACCOUNT_ATTEMPT_LIMIT"
    assert world.credentials.calls == calls


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "short", "code_verifier": VERIFIER},
        {"code": CODE, "code_verifier": "bad verifier with spaces!!"},
        {"code": CODE, "code_verifier": VERIFIER, "extra": 1},
        {"code": "x" * 300, "code_verifier": VERIFIER},
    ],
)
def test_malformed_exchanges_are_refused_before_the_provider_is_called(world, body):
    calls = world.credentials.calls
    refused = world.browser().post("/v1/auth/oauth/exchange", json=body)
    assert refused.status_code == 422 and code(refused) == "ACCOUNT_INPUT_INVALID"
    assert world.credentials.calls == calls and VERIFIER not in refused.text


def test_a_provider_outage_during_the_exchange_is_retryable(world):
    world.credentials.fail = CredentialServiceUnavailable()
    outage = exchange(world.browser())
    assert outage.status_code == 503 and code(outage) == "ACCOUNT_SERVICE_UNAVAILABLE"


def test_the_exchange_is_closed_to_callers_without_the_web_secret(world):
    assert TestClient(world.app, base_url="https://testserver").post(
        "/v1/auth/oauth/exchange", json={"code": CODE, "code_verifier": VERIFIER}
    ).status_code in {401, 403}


def test_the_code_exchange_request_is_exact_and_refusals_map_to_our_code():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"access_token": "t", "user": {"id": "user-9"}})

    assert asyncio.run(provider(handler).exchange_code(CODE, VERIFIER)) == "user-9"
    assert seen == [
        ("https://project.supabase.test/auth/v1/token?grant_type=pkce", {"auth_code": CODE, "code_verifier": VERIFIER})
    ]
    for error in ("bad_code_verifier", "flow_state_not_found", "flow_state_expired"):
        with pytest.raises(CredentialsRejected) as caught:
            asyncio.run(
                provider(
                    lambda r, e=error: httpx.Response(400, json={"error_code": e, "msg": "private"})
                ).exchange_code(CODE, VERIFIER)
            )
        assert caught.value.code == "ACCOUNT_OAUTH_FAILED" and "private" not in str(caught.value)
