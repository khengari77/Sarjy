"""Offline boundary test against the actual production web server."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

TOKEN = "test-review-token-0123456789abcdef"


def test_built_web_requires_review_token_before_proxying():
    if not shutil.which("node") or not Path("apps/web/build/index.js").exists():
        if os.environ.get("CI"):
            pytest.fail("CI must build the frontend before this test")
        pytest.skip("Build the frontend first to run the production boundary test")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as state_directory:
        env = os.environ | {
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DEVELOPMENT_PROTECTED": "true",
            "REVIEW_ACCESS_TOKEN": TOKEN,
            "REVIEW_ACCESS_MAX_USES": "2",
            "REVIEW_ACCESS_EXPIRES_AT": "2099-01-01T00:00:00Z",
            "REVIEW_ACCESS_STATE_PATH": os.path.join(state_directory, "access-state.json"),
            "SESSION_SECRET": "test-only-session-secret",
            "WEB_SERVICE_SECRET": "test-only-proxy-secret",
            "API_INTERNAL_URL": "http://127.0.0.1:1",
        }
        process = subprocess.Popen(
            ["node", "server.js"], cwd="apps/web", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        pytest.fail("Protected web server exited before readiness")
                    try:
                        if client.get("/").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Web server did not become ready")

                door = client.get("/")
                assert door.status_code == 200
                assert 'id="access-token"' in door.text
                assert 'id="voice-stage"' not in door.text
                assert "set-cookie" not in door.headers
                assert door.headers["referrer-policy"] == "no-referrer"
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
