"""Run a stream early; hand over nothing until its owner decides.

The stream is started, iterated and closed by one task, because an async generator
(and any timeout inside it) belongs to the task that drives it. Whatever it produces,
including its failure, stays here until ``released``; ``discard`` drops it unseen.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Any, Final

import httpx

Report = Callable[[httpx.HTTPStatusError], Awaitable[None]]

_END: Final = object()
DISCARD_SECONDS: Final = 0.5


class HeldStream[T]:
    def __init__(self, stream: AsyncGenerator[T]) -> None:
        self._stream = stream
        self._items: asyncio.Queue[T | object] = asyncio.Queue()
        self.first_item_at: float | None = None
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._retrieve)

    @property
    def task(self) -> asyncio.Task[None]:
        return self._task

    @staticmethod
    def _retrieve(task: asyncio.Task[None]) -> None:
        # A failure nobody follows is not a warning; ``released`` still raises it.
        if not task.cancelled():
            task.exception()

    async def _run(self) -> None:
        try:
            async for item in self._stream:
                if self.first_item_at is None:
                    self.first_item_at = time.perf_counter()
                self._items.put_nowait(item)
        finally:
            try:
                await self._stream.aclose()
            finally:
                self._items.put_nowait(_END)

    async def released(self) -> AsyncGenerator[T]:
        """Yield everything held so far, then follow the stream; re-raise its failure in order."""
        try:
            while (item := await self._items.get()) is not _END:
                yield item  # type: ignore[misc]
            await self._task
        finally:
            await self.discard()

    def cancel(self) -> None:
        """Ask the stream to stop, without waiting for it."""
        self._task.cancel()

    async def discard(self) -> None:
        """Stop the stream and give it a moment to close. Never raises the stream's own failure."""
        if self._task.done():
            return
        self.cancel()
        # A provider that will not close must not hold up the next turn.
        _, open_still = await asyncio.wait({self._task}, timeout=DISCARD_SECONDS)
        if open_still:
            logging.warning("A dropped model reply has not closed yet")


class EarlyReply:
    """A held model reply whose refused reservations are announced only once it is used."""

    def __init__(self, start: Callable[[Report], AsyncGenerator[Any]]) -> None:
        self._refusals: list[httpx.HTTPStatusError] = []
        self._announce: Report | None = None
        self.started = time.perf_counter()
        self._held: HeldStream[Any] = HeldStream(start(self._report))

    @property
    def first_item_at(self) -> float | None:
        return self._held.first_item_at

    async def _report(self, refusal: httpx.HTTPStatusError) -> None:
        if self._announce is None:
            self._refusals.append(refusal)
        else:
            await self._announce(refusal)

    async def released(self, announce: Report) -> AsyncGenerator[Any]:
        self._announce = announce
        while self._refusals:
            await announce(self._refusals.pop(0))
        async with aclosing(self._held.released()) as items:
            async for item in items:
                yield item

    def cancel(self) -> None:
        self._held.cancel()

    async def discard(self) -> None:
        # Nothing it still does is this turn's news.
        self._announce = None
        await self._held.discard()


__all__ = ["EarlyReply", "HeldStream"]
