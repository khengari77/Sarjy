"""Whether an early reply's interim transcript still matches the finalized one."""

import asyncio
import re

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def _normalized(text: str) -> str:
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub("", text.casefold())).strip()


def transcripts_equivalent(first: str, second: str) -> bool:
    """Compare casefolded transcripts ignoring punctuation and whitespace.

    Mirrors the SDK's adoption rule: an exact match, or the same non-empty words.
    """
    left = _normalized(first)
    return bool(left) and left == _normalized(second)


class RecentFinals:
    """Final speech segments heard since the last turn hook.

    A reply request for a message the hook has not seen is either an early (preemptive) reply
    or typed input. Only a spoken one ends with a final transcript segment, so that is how the
    worker tells them apart.
    """

    def __init__(self, size: int = 4) -> None:
        self._size = size
        self._segments: list[str] = []

    def add(self, transcript: str) -> None:
        segment = _normalized(transcript)
        if segment:
            self._segments = [*self._segments, segment][-self._size :]

    def size(self) -> int:
        return len(self._segments)

    def clear(self) -> None:
        self._segments = []

    def covers(self, text: str) -> bool:
        """True when the text ends with the latest final segment."""
        return bool(self._segments) and _normalized(text).endswith(self._segments[-1])


class EarlyTurn:
    """The one early reply the SDK may still adopt: ``mode`` is "early" or "wait"."""

    def __init__(self, message_id: str, text: str, ready) -> None:
        self.message_id = message_id
        self.text = text
        self.ready = ready  # future resolved once the turn is open (or failed)
        self.mode = "early"
        self.turn_id: str | None = None
        self.adopted = False
        self.retired = False
        self.decided = asyncio.get_running_loop().create_future()  # True once adopted, False once retired
        self.saved: dict = {}  # memory state before the early compile, restored if it is retired
