"""Accounts end to end: the built web server, the real API and a fake credential provider."""

import os
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from app.config import Settings
from app.main import create_app
from app.services.account_auth import CredentialsRejected
from test_web_access import _free_port, _require_build

SECRET = "account-e2e-web-secret"
PASSWORD = "correct horse battery"
OAUTH_CODE = "o" * 36
SUPABASE = "https://project.supabase.test"
HTML = {"accept": "text/html"}


class FakeCredentials:
    def __init__(self):
        self.users = {}

    async def sign_up(self, email, password):
        if email in self.users:
            raise CredentialsRejected("ACCOUNT_EXISTS")
        self.users[email] = password
        return f"auth-{email}"

    async def sign_in(self, email, password):
        if self.users.get(email) != password:
            raise CredentialsRejected("ACCOUNT_CREDENTIALS_INVALID")
        return f"auth-{email}"

    async def exchange_code(self, code, verifier):
        if code != OAUTH_CODE or len(verifier) != 43:
            raise CredentialsRejected("ACCOUNT_OAUTH_FAILED")
        return "auth-provider-user"


def _web(port, api_port, **overrides):
    env = {name: value for name, value in os.environ.items() if not name.startswith("REVIEW")} | {
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "ORIGIN": f"http://127.0.0.1:{port}",
        "DEVELOPMENT_PROTECTED": "true",
        "ACCOUNT_ACCESS_ENABLED": "true",
        "WEB_SERVICE_SECRET": SECRET,
        "API_INTERNAL_URL": f"http://127.0.0.1:{api_port}",
        "ADDRESS_HEADER": "x-test-address",
        "SUPABASE_URL": SUPABASE,
        "OAUTH_PROVIDERS": "google,github",
    }
    return subprocess.Popen(
        ["node", "server.js"],
        cwd="apps/web",
        env=env | overrides,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Site:
    def __init__(self, root: Path):
        self.database = root / "e2e.db"
        config = Settings(
            database_url=f"sqlite+aiosqlite:///{self.database}",
            account_access_enabled=True,
            cookie_secure=True,
            development_protected=True,
            web_service_secret=SECRET,
            supabase_url="https://project.supabase.test",
            supabase_publishable_key="sb_publishable_test",
            openai_api_key="",
            deepseek_api_key="",
            deepgram_api_key="",
            _env_file=None,
        )
        self.api_port, self.port = _free_port(), _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(config, credentials=FakeCredentials()),
                host="127.0.0.1",
                port=self.api_port,
                log_level="error",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.web = _web(self.port, self.api_port)
        self.origin = f"http://127.0.0.1:{self.port}"
        for _ in range(150):
            try:
                if self.server.started and httpx.get(self.origin + "/healthz").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        self.close()
        pytest.fail("The account site did not become ready")

    def stop_api(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def close(self):
        self.web.terminate()
        self.web.wait(timeout=10)
        self.stop_api()

    def rows(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchall()


class Browser:
    """Plain HTTP client that replays Secure cookies and looks like one address."""

    def __init__(self, site, address="198.51.100.7"):
        self.site, self.cookie = site, ""
        self.address = address

    def send(self, method, path, **kwargs):
        headers = {"origin": self.site.origin} | kwargs.pop("headers", HTML)
        if self.cookie:
            headers["cookie"] = f"sarjy_account={self.cookie}"
        headers["x-test-address"] = self.address
        response = httpx.request(method, self.site.origin + path, headers=headers, timeout=10, **kwargs)
        for value in response.headers.get_list("set-cookie"):
            if match := re.match(r"sarjy_account=([^;]*)", value):
                self.cookie = match[1]
                if match[1]:
                    assert all(flag in value for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"))
        return response

    def enter(self, action, email="ada@example.test", password=PASSWORD, next_path="/"):
        return self.send("POST", f"/{action}", data={"email": email, "password": password, "next": next_path})


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    _require_build()
    site = Site(tmp_path_factory.mktemp("accounts"))
    yield site
    site.close()


def test_a_stranger_is_sent_to_sign_in_and_reaches_no_data(site):
    browser = Browser(site)
    page = browser.send("GET", "/?panel=memory&token=secret")
    assert (page.status_code, page.headers["location"]) == (303, "/sign-in?next=%2F%3Fpanel%3Dmemory")
    assert browser.send("GET", "/").headers["location"] == "/sign-in"
    assert browser.send("GET", "/", headers={"accept": "*/*"}).status_code == 401
    assert browser.send("POST", "/", data={"token": "anything"}).status_code == 401
    data = browser.send("GET", "/__data.json", headers={"accept": "*/*"})
    assert (data.json()["type"], data.json()["location"]) == ("redirect", "/sign-in")
    for path in ("conversations", "settings", "demo-session", "auth/session"):
        assert browser.send("GET", f"/api/v1/{path}").status_code in {401, 405}
    assert browser.send("POST", "/api/v1/demo-session", json={}).status_code == 401
    assert browser.send("GET", "/review").headers["location"].startswith("/sign-in")
    for path in ("/sign-in", "/sign-up"):
        form = browser.send("GET", path)
        assert form.status_code == 200 and 'name="password"' in form.text
        assert form.headers.get_list("cache-control") == ["no-store"]
        assert "access-token" not in form.text
    assert not browser.cookie


def test_sign_up_use_sign_out_and_sign_in_again(site):
    first = Browser(site)
    created = first.enter("sign-up", next_path="/?panel=memory")
    assert (created.status_code, created.headers["location"]) == (303, "/?panel=memory")
    assert created.headers.get_list("cache-control") == ["no-store"]
    old = first.cookie
    app = first.send("GET", "/")
    assert app.status_code == 200 and 'id="voice-stage"' in app.text and "Sign out" in app.text
    conversation = first.send("POST", "/api/v1/conversations", json={}).json()["id"]
    assert first.send("GET", "/sign-in").headers["location"] == "/"

    # A link cannot sign anyone out, and neither can another site's form.
    assert first.send("GET", "/logout").headers["location"] == "/"
    hostile = first.send("POST", "/logout", headers={"origin": "https://evil.test"}, data={"x": "1"})
    assert hostile.status_code == 403
    assert first.send("GET", "/").status_code == 200

    out = first.send("POST", "/logout", data={"x": "1"})
    assert (out.status_code, out.headers["location"], first.cookie) == (303, "/sign-in", "")
    # The session itself is dead, not merely forgotten by this browser.
    first.cookie = old
    assert first.send("GET", "/").status_code == 303
    assert first.send("GET", "/api/v1/conversations").status_code == 401

    second = Browser(site)
    assert second.enter("sign-in", email=" ADA@example.test ").status_code == 303
    assert [row["id"] for row in second.send("GET", "/api/v1/conversations").json()] == [conversation]

    stranger = Browser(site)
    assert stranger.enter("sign-up", email="bob@example.test").status_code == 303
    assert stranger.send("GET", "/api/v1/conversations").json() == []


@pytest.mark.parametrize(
    ("action", "values", "text"),
    [
        ("sign-in", {"email": "nobody@example.test"}, "do not match an account"),
        ("sign-up", {"email": "taken@example.test"}, "already exists"),
        ("sign-up", {"email": "new@example.test", "password": "short"}, "at least 8 characters"),
        ("sign-up", {"email": "not-an-address"}, "valid email"),
    ],
)
def test_failures_explain_themselves_without_echoing_the_password(site, action, values, text):
    Browser(site, "198.51.100.8").enter("sign-up", email="taken@example.test")
    browser = Browser(site, "198.51.100.8")
    response = browser.enter(action, **values)
    assert response.status_code == 400
    assert text in response.text
    assert values["email"] in response.text
    assert values.get("password", PASSWORD) not in response.text
    assert not browser.cookie


def test_next_never_leaves_the_site_and_attempts_count_per_visitor(site):
    browser = Browser(site, "198.51.100.9")
    # The same POST reaches the API's source limit from this address only.
    assert browser.enter("sign-up", email="eve@example.test", next_path="//evil.test/x").headers["location"] == "/"
    statuses = [
        browser.enter("sign-in", email="eve@example.test", password="wrong guess").status_code for _ in range(11)
    ]
    assert statuses == [400] * 10 + [429]
    assert "Too many attempts" in browser.enter("sign-in", email="eve@example.test").text
    assert Browser(site, "198.51.100.10").enter("sign-in", email="eve@example.test").status_code == 303


def test_an_api_outage_keeps_the_cookie_and_never_opens_the_app(tmp_path):
    _require_build()
    site = Site(tmp_path)
    try:
        browser = Browser(site)
        assert browser.enter("sign-up").status_code == 303
        site.stop_api()
        page = browser.send("GET", "/")
        assert page.status_code == 503 and "set-cookie" not in page.headers and "voice-stage" not in page.text
        failed = browser.enter("sign-in")
        assert failed.status_code == 503 and "not available right now" in failed.text
    finally:
        site.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"ACCOUNT_ACCESS_ENABLED": "yes"},
        {"ACCOUNT_ACCESS_ENABLED": ""},
        {"DEVELOPMENT_PROTECTED": "false"},
        {"WEB_SERVICE_SECRET": ""},
        {"REVIEWER_ACCESS_ENABLED": "true"},
        {"ADDRESS_HEADER": ""},
        {"OAUTH_PROVIDERS": "google,twiter"},
    ],
)
def test_an_unsafe_account_configuration_does_not_serve_the_app(overrides):
    _require_build()
    port = _free_port()
    process = _web(port, 1, **overrides)
    try:
        for _ in range(100):
            if process.poll() is not None:
                return
            try:
                assert httpx.get(f"http://127.0.0.1:{port}/", headers=HTML).status_code == 500
                return
            except httpx.TransportError:
                time.sleep(0.1)
        pytest.fail("The server neither refused to boot nor answered")
    finally:
        process.terminate()
        process.wait(timeout=10)


def _started(browser, provider, next_path="/?panel=memory"):
    """Begin a provider sign-in; returns the redirect and the verifier cookie the browser now holds."""
    response = browser.send("GET", f"/auth/{provider}?next={quote(next_path)}")
    cookies = {
        m[1]: m[2]
        for v in response.headers.get_list("set-cookie")
        if (m := re.match(r"(sarjy_oauth[a-z_]*)=([^;]*)", v))
    }
    return response, cookies


def test_provider_buttons_exist_only_for_enabled_providers(site):
    page = Browser(site).send("GET", "/sign-in?next=%2F%3Fpanel%3Dmemory")
    assert "/auth/google?next=" in page.text and "/auth/github?next=" in page.text
    assert "/auth/apple" not in page.text and "Continue with Google" in page.text
    assert Browser(site).send("GET", "/auth/apple").status_code == 404
    assert Browser(site).send("GET", "/auth/nonsense").headers["location"].startswith("/sign-in")


def test_a_provider_sign_in_round_trip_opens_the_app(site):
    browser = Browser(site)
    started, cookies = _started(browser, "google")
    location = started.headers["location"]
    assert started.status_code == 303 and location.startswith(f"{SUPABASE}/auth/v1/authorize?provider=google&")
    assert f"redirect_to=http%3A%2F%2F127.0.0.1%3A{site.port}%2Fauth%2Fcallback" in location
    assert "code_challenge_method=s256" in location and "code_challenge=" in location
    assert cookies["sarjy_oauth"].startswith("google.") and "Path=/auth" in " ".join(
        started.headers.get_list("set-cookie")
    )
    assert started.headers["cache-control"] != "public"

    jar = "; ".join(f"{name}={value}" for name, value in cookies.items())
    arrived = browser.send("GET", f"/auth/callback?code={OAUTH_CODE}", headers=HTML | {"cookie": jar})
    assert (arrived.status_code, arrived.headers["location"]) == (303, "/?panel=memory")
    assert browser.send("GET", "/").status_code == 200 and browser.cookie
    # The verifier was single use: both cookies are expired on the way back.
    assert sum("Max-Age=0" in v or "expires=Thu, 01 Jan 1970" in v for v in arrived.headers.get_list("set-cookie")) == 2


@pytest.mark.parametrize(
    "path, jar, location",
    [
        (f"/auth/callback?code={OAUTH_CODE}", "", "/sign-in?oauth=failed"),
        (f"/auth/callback?code={OAUTH_CODE}", "sarjy_oauth=google.short", "/sign-in?oauth=failed"),
        (f"/auth/callback?code={OAUTH_CODE}", "sarjy_oauth=azure." + "v" * 43, "/sign-in?oauth=failed"),
        (f"/auth/callback?code={'x' * 36}", "sarjy_oauth=google." + "v" * 43, "/sign-in?oauth=failed"),
        ("/auth/callback?error=access_denied", "sarjy_oauth=google." + "v" * 43, "/sign-in?oauth=failed"),
        ("/auth/callback", "sarjy_oauth=google." + "v" * 43, "/sign-in?oauth=failed"),
    ],
)
def test_a_provider_return_without_a_matching_start_signs_nobody_in(site, path, jar, location):
    browser = Browser(site)
    response = browser.send("GET", path, headers=HTML | ({"cookie": jar} if jar else {}))
    assert (response.status_code, response.headers["location"]) == (303, location)
    assert not browser.cookie
    page = browser.send("GET", location)
    assert "did not complete" in page.text


def test_a_failed_callback_keeps_the_destination(site):
    browser = Browser(site)
    _, cookies = _started(browser, "google", next_path="/?panel=memory")
    jar = "; ".join(f"{name}={value}" for name, value in cookies.items())
    response = browser.send("GET", "/auth/callback?error=access_denied", headers=HTML | {"cookie": jar})
    assert (response.status_code, response.headers["location"]) == (
        303,
        "/sign-in?oauth=failed&next=%2F%3Fpanel%3Dmemory",
    )


@pytest.mark.parametrize(
    "path",
    [
        "/auth/callback?error=access_denied",
        "/auth/callback",
        f"/auth/callback?code={'x' * 36}",
    ],
)
def test_a_failed_callback_expires_both_pkce_cookies(site, path):
    jar = f"sarjy_oauth=google.{'v' * 43}; sarjy_oauth_next=/?panel=memory"
    response = Browser(site).send("GET", path, headers=HTML | {"cookie": jar})
    assert response.status_code == 303
    assert (
        sum("Max-Age=0" in v or "expires=Thu, 01 Jan 1970" in v for v in response.headers.get_list("set-cookie")) == 2
    )


def test_a_protocol_relative_next_starts_over_at_the_root_after_success(site):
    browser = Browser(site)
    _, cookies = _started(browser, "google", next_path="//evil.test")
    jar = "; ".join(f"{name}={value}" for name, value in cookies.items())
    arrived = browser.send("GET", f"/auth/callback?code={OAUTH_CODE}", headers=HTML | {"cookie": jar})
    assert (arrived.status_code, arrived.headers["location"]) == (303, "/")
