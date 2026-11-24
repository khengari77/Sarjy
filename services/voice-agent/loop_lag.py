"""How late the worker's event loop wakes up: a turn's audio and model work all share this one loop."""

import asyncio
import time

INTERVAL_SECONDS = 0.02
MAX_LAG_MS = 120_000  # the metrics API rejects larger values


class LoopLagMonitor:
    """Sleeps in short steps and remembers the worst overshoot since the last `take()`."""

    def __init__(self) -> None:
        self.worst = 0.0
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while True:
            started = time.perf_counter()
            await asyncio.sleep(INTERVAL_SECONDS)
            self.worst = max(self.worst, time.perf_counter() - started - INTERVAL_SECONDS)

    def worst_ms(self) -> int | None:
        """Worst lag since the last `take()` without resetting; None when the monitor is not running."""
        if self.task is None or self.task.done():
            return None
        return min(round(self.worst * 1000), MAX_LAG_MS)

    def take(self) -> int:
        """Worst lag in whole ms since the previous call, then reset."""
        worst, self.worst = self.worst, 0.0
        return min(round(worst * 1000), MAX_LAG_MS)

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
