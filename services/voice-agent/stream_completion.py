"""Observe terminal SSE metadata without replacing native provider parsing."""

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass

import httpx


@dataclass
class CompletionState:
    reason: str | None = None
    done: bool = False
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    provider_connect_ms: int | None = None  # TCP+TLS for this request; 0 when a kept-alive socket was reused
    provider_headers_ms: int | None = None  # request headers sent -> response headers received


def _usage_int(usage: object, name: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


completion_state: ContextVar[CompletionState | None] = ContextVar("completion_state", default=None)


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, state: CompletionState) -> None:
        self.stream, self.state = stream, state
        self.buffer: bytes = b""
        self.data: list[bytes] = []

    @property
    def prompt_cache_hit_tokens(self) -> int | None:
        return self.state.prompt_cache_hit_tokens

    @property
    def prompt_cache_miss_tokens(self) -> int | None:
        return self.state.prompt_cache_miss_tokens

    def event(self) -> None:
        if not self.data:
            return
        data = b"\n".join(self.data)
        self.data = []
        if self.state.done:
            return
        if data == b"[DONE]":
            self.state.done = True
            return
        try:
            payload = json.loads(data)
            for choice in payload.get("choices", []):
                if choice.get("index", 0) == 0 and choice.get("finish_reason") and self.state.reason is None:
                    self.state.reason = choice["finish_reason"]
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.state.prompt_cache_hit_tokens = _usage_int(usage, "prompt_cache_hit_tokens")
                self.state.prompt_cache_miss_tokens = _usage_int(usage, "prompt_cache_miss_tokens")
        except (ValueError, TypeError, AttributeError):
            pass  # Native SDK owns payload validation.

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        while match := re.search(rb"[\r\n]", self.buffer):
            index = match.start()
            if self.buffer[index:] == b"\r":
                break  # The next transport chunk may begin with LF.
            width = 2 if self.buffer[index : index + 2] == b"\r\n" else 1
            line, self.buffer = self.buffer[:index], self.buffer[index + width :]
            if not line:
                self.event()
            elif line.startswith(b"data:"):
                self.data.append(line[5:].lstrip(b" "))

    def finish(self) -> None:
        if self.buffer.endswith(b"\r"):
            self.feed(b"\n")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.stream:
            self.feed(chunk)
            yield chunk
        self.finish()

    async def aclose(self) -> None:
        await self.stream.aclose()


class DecodedResponseStream(httpx.AsyncByteStream):
    """Let HTTPX decode compression before observing SSE framing."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_bytes():
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


# Idle connections are kept for 50 s, not httpx's five: longer than a gap between turns, shorter than the 60-65 s
# at which servers and proxies close an idle one (a socket the other end already closed is a failed request).
KEEPALIVE_LIMITS = httpx.Limits(max_keepalive_connections=8, keepalive_expiry=50)


MAX_PHASE_MS = 120_000  # the metrics API rejects larger values


class _PhaseTrace:
    """httpcore trace hook: how long the connection setup and the wait for response headers took."""

    def __init__(self, state: CompletionState) -> None:
        self.state = state
        self.headers_started: float | None = None
        self.connect_started: float | None = None

    async def __call__(self, event: str, info: object) -> None:
        try:  # a metrics hook must never break the request it observes
            self._observe(event)
        except Exception:
            logging.getLogger(__name__).debug("trace hook failed", exc_info=True)

    def _observe(self, event: str) -> None:
        now = time.perf_counter()
        if event.endswith(".connect_tcp.started"):
            self.connect_started = now
        elif event.endswith((".connect_tcp.complete", ".start_tls.complete")) and self.connect_started is not None:
            self.state.provider_connect_ms = min(round((now - self.connect_started) * 1000), MAX_PHASE_MS)
        elif event.endswith(".send_request_headers.started"):
            self.headers_started = now
            if self.state.provider_connect_ms is None:
                self.state.provider_connect_ms = 0
        elif event.endswith(".receive_response_headers.complete") and self.headers_started is not None:
            self.state.provider_headers_ms = min(round((now - self.headers_started) * 1000), MAX_PHASE_MS)


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport or httpx.AsyncHTTPTransport(retries=0, limits=KEEPALIVE_LIMITS)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        state = completion_state.get()
        if state is not None:
            request.extensions = {**request.extensions, "trace": _PhaseTrace(state)}
        response = await self.transport.handle_async_request(request)
        if state is None or response.status_code != 200:
            return response
        headers = [
            (key, value)
            for key, value in response.headers.raw
            if key.lower() not in (b"content-encoding", b"content-length")
        ]
        return httpx.Response(
            response.status_code,
            headers=headers,
            stream=ObservedStream(DecodedResponseStream(response), state),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.transport.aclose()
