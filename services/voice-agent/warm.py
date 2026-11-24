"""Fire-and-forget connection warm-up when the user starts speaking.

Speech start is the last moment before a turn. Touching the backend and the
provider connection then keeps their setup off the first-token path. Every
attempt is best effort, rate limited per session, spends no tokens and creates
no turn.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

WARM_INTERVAL_SECONDS = 20.0
WARM_PATH = "/internal/agent/warm"


class ConnectionWarmup:
    """Keep the backend and provider connections hot without delaying a reply."""

    def __init__(
        self,
        backend: Any,
        llm: Any = None,
        *,
        interval: float = WARM_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._backend = backend
        self._llm = llm
        self._interval = interval
        self._clock = clock
        self._last: float | None = None
        self.tasks: set[asyncio.Task[None]] = set()

    def user_state_changed(self, event: Any) -> None:
        """LiveKit session hook: a user who starts speaking is about to need the turn path."""
        if getattr(event, "new_state", None) == "speaking":
            self.warm_if_due()

    def warm_if_due(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        task = asyncio.create_task(self._warm())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _warm(self) -> None:
        try:
            await asyncio.gather(self._warm_backend(), self._warm_provider(), return_exceptions=True)
        except Exception:
            logging.getLogger(__name__).debug("connection warm-up failed", exc_info=True)

    async def _warm_backend(self) -> None:
        try:
            response = await self._backend.post(WARM_PATH)
            await response.aclose()
        except Exception:
            logging.getLogger(__name__).debug("backend warm-up failed", exc_info=True)

    async def _warm_provider(self) -> None:
        warm = getattr(self._llm, "warm_connection", None)
        if warm is None:
            return
        try:
            await warm()
        except Exception:
            logging.getLogger(__name__).debug("provider warm-up failed", exc_info=True)

    async def aclose(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
