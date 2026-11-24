"""Prepaid speech characters for one call.

Invariant: characters handed out by ``take`` never exceed characters the durable
ledger has already granted. Grants are charged before use and never refunded; what
one reply does not use stays available to the next reply of the same call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Final

import httpx

# One block covers most replies; a refill starts while this much is still unspent.
BLOCK: Final = 160
LOW_WATER: Final = 64
# Below this, the remaining allowance is asked for in exactly the size the text needs.
SMALLEST_BLOCK: Final = 20


def _exhausted(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


class SpeechBudget:
    def __init__(
        self,
        reserve: Callable[[int], Awaitable[None]],
        report: Callable[[httpx.HTTPStatusError], Awaitable[None]],
        spawn: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]],
    ) -> None:
        self._reserve = reserve
        self._report = report
        self._spawn = spawn
        self._available = 0
        self._refill: asyncio.Task[None] | None = None
        self._refused = 0
        # Halved after each refusal, so the tail of a day's allowance costs a few
        # requests rather than one per streamed piece. It never grows within a call.
        self._block = BLOCK

    async def take(self, wanted: int) -> int:
        """Spend between one and ``wanted`` granted characters, waiting only when none are left."""
        if wanted < 1:
            raise ValueError("SPEECH_CHARACTERS_REQUIRED")
        needed = min(wanted, SMALLEST_BLOCK)
        while not self._available:
            refill = self._start_refill(self._block or needed)
            # ``wait`` leaves the refill running if this reply is interrupted: its
            # grant may already be charged and the next reply can use it.
            await asyncio.wait({refill})
            if refill.cancelled():
                raise RuntimeError("SPEECH_REFILL_CANCELLED")
            error = refill.exception()
            if error is None:
                continue
            if _exhausted(error) and self._refused > needed:
                continue
            # Only text that was really needed reports a failure to the user.
            if isinstance(error, httpx.HTTPStatusError):
                await self._report(error)
            raise error
        spent = min(wanted, self._available)
        self._available -= spent
        if self._available < LOW_WATER and self._block == BLOCK:
            self._start_refill(BLOCK)
        return spent

    def prefill(self) -> None:
        """Reserve one block before the next reply needs it.

        A no-op while characters remain, while a refill is in flight, or after a refusal: a
        refused block is retried only when text actually needs it.
        """
        if self._available or not self._block:
            return
        if self._refill is not None:
            if not self._refill.done():
                return
            if self._refill.cancelled() or self._refill.exception() is not None:
                return
        self._start_refill(self._block)

    def _start_refill(self, size: int) -> asyncio.Task[None]:
        if self._refill is None or self._refill.done():
            self._refill = self._spawn(self._grant(size))
        return self._refill

    async def _grant(self, size: int) -> None:
        try:
            await self._reserve(size)
        except httpx.HTTPStatusError as exc:
            if _exhausted(exc):
                self._refused = size
                smaller = size // 2
                self._block = smaller if smaller >= SMALLEST_BLOCK else 0
            raise
        self._available += size


__all__ = ["BLOCK", "LOW_WATER", "SMALLEST_BLOCK", "SpeechBudget"]
