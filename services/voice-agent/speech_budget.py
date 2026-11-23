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
# The ledger route rejects larger single reservations.
MAX_RESERVATION: Final = 500


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
        # The last refill was refused for lack of allowance: stop asking for blocks.
        self._dry = False

    async def take(self, wanted: int) -> int:
        """Spend between one and ``wanted`` granted characters, waiting only when none are left."""
        if wanted < 1:
            raise ValueError("SPEECH_CHARACTERS_REQUIRED")
        while not self._available:
            if self._dry:
                await self._grant_exactly(min(wanted, MAX_RESERVATION))
                continue
            refill = self._start_refill()
            # Shielded: an interrupted reply must not lose a grant that is already charged.
            await asyncio.wait({refill})
            error = refill.exception()
            if error is not None and not _exhausted(error):
                await self._raise(error)
        spent = min(wanted, self._available)
        self._available -= spent
        if self._available < LOW_WATER and not self._dry:
            self._start_refill()
        return spent

    def _start_refill(self) -> asyncio.Task[None]:
        if self._refill is None or self._refill.done():
            self._refill = self._spawn(self._grant_block())
        return self._refill

    async def _grant_block(self) -> None:
        try:
            await self._reserve(BLOCK)
        except httpx.HTTPStatusError as exc:
            self._dry = _exhausted(exc)
            raise
        self._available += BLOCK

    async def _grant_exactly(self, characters: int) -> None:
        # A block no longer fits today's allowance, but this text still might.
        try:
            await self._reserve(characters)
        except httpx.HTTPStatusError as exc:
            await self._raise(exc)
        self._available += characters

    async def _raise(self, error: BaseException) -> None:
        # Only text that was really needed reports a failure to the user.
        if isinstance(error, httpx.HTTPStatusError):
            await self._report(error)
        raise error


__all__ = ["BLOCK", "LOW_WATER", "SpeechBudget"]
