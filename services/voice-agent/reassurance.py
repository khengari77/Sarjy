"""One short spoken filler while a reply is slow, never more than once per turn.

The timer is owned by one voice session. It records the turn hook start and the
filler already spoken for a turn, so a slow model call, a tool call and a later
tool continuation cannot add a second filler to the same turn.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any, Final
from uuid import uuid4

from livekit.agents import llm

PHRASES: Final = ("One moment.", "Let me check that.", "Just a second.", "Give me a moment.")
# Tools whose result the application speaks itself (Wordle, memory consent): a filler would precede that speech.
WORDLE_TOOL_NAMES: Final = frozenset(
    {
        "play_wordle",
        "submit_wordle_guess",
        "wordle_status",
        "reset_wordle",
        "close_wordle",
        "propose_memory",
        "propose_forget",
    }
)


class ReassuranceTimer:
    """At most one filler per turn, decided from the turn hook's own clock."""

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold if threshold > 0 else 0.0
        self._starts: dict[str, float] = {}
        self._fired: set[str] = set()
        self._answered: set[str] = set()
        self._phrase = 0

    def begin(self, turn_id: str, started: float) -> None:
        self._starts[turn_id] = started

    def begin_if_missing(self, turn_id: str, started: float) -> None:
        self._starts.setdefault(turn_id, started)

    def started_at(self, turn_id: str) -> float | None:
        return self._starts.get(turn_id)

    def can_fire(self, turn_id: str) -> bool:
        return (
            self.threshold > 0
            and turn_id in self._starts
            and turn_id not in self._fired
            and turn_id not in self._answered
        )

    def remaining(self, turn_id: str) -> float | None:
        started = self._starts.get(turn_id)
        if started is None:
            return None
        return max(0.0, self.threshold - (time.perf_counter() - started))

    def fire(self, turn_id: str) -> str | None:
        """Mark the turn, and return its one phrase in deterministic round-robin order."""
        if not self.can_fire(turn_id):
            return None
        self._fired.add(turn_id)
        phrase = PHRASES[self._phrase % len(PHRASES)]
        self._phrase += 1
        return phrase

    def answered(self, turn_id: str) -> None:
        """A real chunk was yielded; this turn may never receive a filler."""
        self._answered.add(turn_id)

    def finish(self, turn_id: str) -> None:
        self._starts.pop(turn_id, None)
        self._fired.discard(turn_id)
        self._answered.discard(turn_id)


def filler_chunk(text: str) -> llm.ChatChunk:
    return llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=text + " "))


def has_tool_call(chunk: Any) -> bool:
    """True for a chunk that calls a tool other than a Wordle tool."""
    delta = getattr(chunk, "delta", None)
    calls = getattr(delta, "tool_calls", None) or ()
    return any(getattr(call, "name", None) not in WORDLE_TOOL_NAMES for call in calls)


_DONE: Final = object()


async def _passthrough(reply, timer, turn_id, on_answer):
    first = True
    try:
        async for item in reply:
            if first:
                first = False
                timer.answered(turn_id)
                on_answer()
            yield item
    finally:
        await reply.aclose()


async def _pump(reply, queue: asyncio.Queue) -> None:
    try:
        async for item in reply:
            await queue.put((item, None))
    except asyncio.CancelledError:
        raise
    except BaseException as error:  # handed to the consumer, which re-raises it
        await queue.put((None, error))
    else:
        await queue.put(_DONE)
    finally:
        await reply.aclose()


async def _next(queue: asyncio.Queue) -> Any:
    entry = await queue.get()
    if entry is _DONE:
        return _DONE
    item, error = entry
    if error is not None:
        raise error
    return item


async def with_reassurance(
    reply: AsyncGenerator[Any, None],
    timer: ReassuranceTimer,
    turn_id: str,
    *,
    allowed: bool,
    on_filler: Callable[[str], None],
    on_answer: Callable[[], None],
) -> AsyncGenerator[Any, None]:
    """Yield one filler if the first real chunk is late or is a tool call.

    One pump task owns ``reply`` from its first step to its close, because an async generator, its
    ContextVars and its timeouts belong to the task that drives them. The consumer only waits on a queue,
    so a late first chunk can be raced against the timer without ever moving the generator between tasks.
    Closing or cancelling this generator cancels the pump, which closes the reply in its own task.
    """
    if not allowed or not timer.can_fire(turn_id):
        async for item in _passthrough(reply, timer, turn_id, on_answer):
            yield item
        return

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    task = asyncio.ensure_future(_pump(reply, queue))

    def filler() -> llm.ChatChunk | None:
        phrase = timer.fire(turn_id)
        if phrase is None:
            return None
        on_filler(phrase)
        return filler_chunk(phrase)

    try:
        try:
            item = await asyncio.wait_for(_next(queue), timeout=timer.remaining(turn_id))
        except TimeoutError:
            if (chunk := filler()) is not None:
                yield chunk
            item = await _next(queue)
        if item is _DONE:
            return
        if has_tool_call(item) and (chunk := filler()) is not None:
            yield chunk
        timer.answered(turn_id)
        on_answer()
        yield item
        while (item := await _next(queue)) is not _DONE:
            yield item
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "PHRASES",
    "WORDLE_TOOL_NAMES",
    "ReassuranceTimer",
    "filler_chunk",
    "has_tool_call",
    "with_reassurance",
]
