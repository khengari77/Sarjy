"""Start the Jev UI classification while ``turn/start`` compiles, and let ``/command`` reuse it.

The classifier needs only the transcript, yet the worker asks for it after ``turn/start`` has returned, so first audio
waited for both in sequence. ``turn/start`` starts one classification per turn here; ``/internal/agent/command`` awaits
the same task instead of calling Jev again. Nothing about effects moves: this only holds a classification (no
side effects), and ``/command`` still runs after the turn row is committed and decides everything from it.

Process-local on purpose: a restart, an eviction or a retried ``turn/start`` with other text just means ``/command``
classifies for itself, exactly as before.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.domain.commands import (
    memory_restore_requested,
    memory_undo_requested,
    parse_exact_command,
    parse_exclusion_confirmation,
    parse_voice_gender,
    parse_voice_selection,
)
from app.domain.ui_routing import UiClassification, ui_text_is_eligible
from app.infrastructure.timing import current_statement_timing
from app.services.jev_ui import UiClassifier

CAPACITY: Final = 128
TTL_SECONDS: Final = 120.0


def classification_key(conversation_id: str, request_id: str) -> str:
    return f"{conversation_id}:{request_id}"


def should_classify_early(text: str) -> bool:
    """Texts every deterministic path in ``/command`` (exact grammar, memory commands, voice names) leaves to Jev.

    Pure text checks only, so a turn that ``/command`` answers without Jev does not spend a Jev request here. A text
    these checks pass but a database-backed path still answers is the only waste; it costs one request.
    """
    return (
        ui_text_is_eligible(text)
        and parse_exact_command(text) is None
        and parse_exclusion_confirmation(text) is None
        and not memory_restore_requested(text)
        and not memory_undo_requested(text)
        and parse_voice_gender(text) is None
        and parse_voice_selection(text) is None
    )


@dataclass
class _Entry:
    text: str
    task: asyncio.Task[UiClassification | None]
    created: float


class ClassificationStore:
    def __init__(
        self,
        *,
        capacity: int = CAPACITY,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        # The event loop keeps only weak references to tasks: hold every running one until it finishes.
        self._running: set[asyncio.Task[UiClassification | None]] = set()
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._clock = clock

    def start(self, key: str, text: str, classifier: UiClassifier | None) -> None:
        """Begin the one classification of this turn; a repeat for the same key and text keeps the running one."""
        if classifier is None or not should_classify_early(text):
            return
        self._expire()
        existing = self._entries.get(key)
        if existing is not None:
            if existing.text == text:
                return
            self._discard(key)
        while len(self._entries) >= self._capacity:
            self._discard(next(iter(self._entries)))
        request_id = "ui-early:" + hashlib.sha256(key.encode()).hexdigest()[:32]
        # A task inherits its creator's context. This one outlives the request that started it, and its budget writes
        # must not count toward that request's ``x-db-ms`` / ``x-db-statements``: detach the statement timer.
        context = contextvars.copy_context()
        context.run(current_statement_timing.set, None)
        task = asyncio.create_task(_classify(classifier, request_id, text), context=context)
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        self._entries[key] = _Entry(text, task, self._clock())

    def take(self, key: str, text: str) -> asyncio.Task[UiClassification | None] | None:
        """The running (or finished) classification for exactly this text, shared by every caller."""
        self._expire()
        entry = self._entries.get(key)
        if entry is None or entry.text != text:
            return None
        return entry.task

    def _expire(self) -> None:
        now = self._clock()
        for key in [key for key, entry in self._entries.items() if now - entry.created > self._ttl]:
            self._discard(key)

    def _discard(self, key: str) -> None:
        # Forget the entry only. The task is bounded by the Jev deadlines and swallows its own errors, and a
        # /command may be awaiting it right now: cancelling it would abort that request instead of a clean miss.
        self._entries.pop(key)


async def _classify(classifier: UiClassifier, request_id: str, text: str) -> UiClassification | None:
    try:
        return await classifier.classify(request_id=request_id, instruction=text)
    except Exception:
        return None


class StoredClassifier:
    """A ``UiClassifier`` for one ``/command`` call that reuses the turn's early classification when there is one."""

    def __init__(self, inner: UiClassifier, store: ClassificationStore, key: str) -> None:
        self._inner = inner
        self._store = store
        self._key = key

    async def classify(self, *, request_id: str, instruction: str) -> UiClassification | None:
        task = self._store.take(self._key, instruction)
        if task is None:
            return await self._inner.classify(request_id=request_id, instruction=instruction)
        # Shielded: a cancelled /command must not cancel the classification a retry of it will reuse.
        return await asyncio.shield(task)
