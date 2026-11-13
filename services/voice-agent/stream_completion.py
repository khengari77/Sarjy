"""Observe terminal SSE metadata without replacing native provider parsing."""

import json
import re
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass

import httpx


@dataclass
class CompletionState:
    reason: str | None = None
    done: bool = False


completion_state: ContextVar[CompletionState | None] = ContextVar("completion_state", default=None)


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, state: CompletionState) -> None:
        self.stream, self.state = stream, state
        self.buffer: bytes = b""
        self.data: list[bytes] = []

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


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.transport.handle_async_request(request)
        state = completion_state.get()
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
