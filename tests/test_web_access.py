"""Offline boundary tests against the actual production web server."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

TOKEN = "test-review-token-0123456789abcdef"
EXPIRY = "2099-01-01T00:00:00Z"


def _require_build() -> None:
    if not shutil.which("node") or not Path("apps/web/build/index.js").exists():
        if os.environ.get("CI"):
            pytest.fail("CI must build the frontend before this test")
        pytest.skip("Build the frontend first to run the production boundary test")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _launch(port: int, state_path: str, expires: str | None = EXPIRY) -> subprocess.Popen:
    env = os.environ | {
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "DEVELOPMENT_PROTECTED": "true",
        "REVIEW_ACCESS_TOKEN": TOKEN,
        "REVIEW_ACCESS_MAX_USES": "2",
        "REVIEW_ACCESS_STATE_PATH": state_path,
        "SESSION_SECRET": "test-only-session-secret",
        "WEB_SERVICE_SECRET": "test-only-proxy-secret",
        "API_INTERNAL_URL": "http://127.0.0.1:1",
    }
    if expires is None:
        env.pop("REVIEW_ACCESS_EXPIRES_AT", None)
    else:
        env["REVIEW_ACCESS_EXPIRES_AT"] = expires
    return subprocess.Popen(
        ["node", "server.js"], cwd="apps/web", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _wait_ready(client: httpx.Client, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            pytest.fail("Protected web server exited before readiness")
        try:
            if client.get("/").status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.1)
    pytest.fail("Web server did not become ready")


def _cookie_expiry(response: httpx.Response) -> int:
    value = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    return int(value.split(".")[2])


def test_built_web_requires_review_token_before_proxying():
    _require_build()
    port = _free_port()
    with tempfile.TemporaryDirectory() as state_directory:
        process = _launch(port, os.path.join(state_directory, "access-state.json"))
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                _wait_ready(client, process)

                door = client.get("/")
                assert door.status_code == 200
                assert 'id="access-token"' in door.text
                assert 'id="voice-stage"' not in door.text
                assert "set-cookie" not in door.headers
                assert door.headers["referrer-policy"] == "same-origin"
                assert door.headers["cache-control"] == "no-store"

                for path in ("/api/v1/conversations", "/api/v1/demo-session"):
                    response = client.get(path)
                    assert response.status_code == 401
                    assert "set-cookie" not in response.headers

                wrong = client.get("/?token=wrong")
                assert wrong.status_code == 200
                assert "That link is not valid." in wrong.text
                assert "set-cookie" not in wrong.headers

                exchange = client.get(f"/?token={TOKEN}", follow_redirects=False)
                assert exchange.status_code == 303
                assert exchange.headers["location"] == "/"
                assert "sarjy_review_session" in exchange.headers["set-cookie"]

                # Secure cookies are not replayed over plain HTTP by httpx;
                # forward the issued value explicitly to exercise authorization.
                session_cookie = exchange.headers["set-cookie"].split(";", 1)[0]
                assert client.get("/api/v1/conversations", headers={"cookie": session_cookie}).status_code == 503
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_expiry_is_absolute_across_two_boots():
    _require_build()
    expiries: list[int] = []
    with tempfile.TemporaryDirectory() as state_directory:
        for boot in range(2):
            port = _free_port()
            process = _launch(port, os.path.join(state_directory, f"state-{boot}.json"))
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                    _wait_ready(client, process)
                    exchange = client.get(f"/?token={TOKEN}", follow_redirects=False)
                    assert exchange.status_code == 303
                    expiries.append(_cookie_expiry(exchange))
            finally:
                process.terminate()
                process.wait(timeout=10)

    expected = int(datetime.strptime(EXPIRY, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())
    assert expiries == [expected, expected]
    assert expiries[0] > int(time.time())


@pytest.mark.parametrize("expires", [None, "", "not-an-instant", "2026-13-40T00:00:00Z"])
def test_missing_or_invalid_expiry_fails_closed(expires: str | None):
    _require_build()
    port = _free_port()
    with tempfile.TemporaryDirectory() as state_directory:
        process = _launch(port, os.path.join(state_directory, "state.json"), expires=expires)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            assert process.poll() is not None, "server booted without an absolute expiry"
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
