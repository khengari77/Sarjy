"""Delayed fake upstream used to reproduce proxy deadline behaviour.

Serves the two paths the deadline check needs and delays every API response by
FAKE_API_DELAY seconds so a slow dispatch can be reproduced without LiveKit.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY_SECONDS = float(os.environ.get("FAKE_API_DELAY", "20"))
PORT = int(os.environ.get("FAKE_API_PORT", "8010"))


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.reply(200, {"status": "ok"})
            return
        if self.path.split("?")[0] == "/v1/echo":
            self.reply(200, self.echo())
            return
        time.sleep(DELAY_SECONDS)
        self.reply(200, [])

    def echo(self) -> dict[str, object]:
        forwarded = [
            name
            for name in ("x-agent-secret", "authorization", "x-web-service-secret", "cookie", "idempotency-key")
            if self.headers.get(name)
        ]
        return {"path": self.path, "forwarded": forwarded}

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.split("?")[0] == "/v1/echo":
            self.reply(200, self.echo())
            return
        time.sleep(DELAY_SECONDS)
        status = 201 if self.path == "/v1/voice/sessions" else 200
        self.reply(
            status,
            {"id": "fake-session", "url": "ws://fake", "token": "fake", "expires_at": "2030-01-01T00:00:00+00:00"},
        )

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()
