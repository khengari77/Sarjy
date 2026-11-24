"""Pure statistics and turn decisions for the voice latency probe.

The probe shell (``voice_latency_probe.py``) owns I/O; every decision about a
latency number, a speech onset or a phrase turn lives here so it can be tested
without a network, a clock or an audio device.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TypedDict

import numpy as np

DEFAULT_THRESHOLD = 200.0
SPEECH_THRESHOLD = 300.0
WINDOW_SECONDS = 0.020
MIN_P95_ANSWERS = 20

TURN_SILENCE_S = 1.0
SILENCE_WINDOW_S = 30.0
ANSWER_TIMEOUT_S = 20.0
LATE_SILENCE_S = 5.0
LATE_WINDOW_S = 30.0
BARGE_SILENCE_S = 1.5

ANSWERED = "answered"
TIMEOUT = "timeout"
BARGE_IN = "barge_in"
NOT_SILENT = "not_silent"
STALE = "stale"
OUTCOMES = frozenset({ANSWERED, TIMEOUT, BARGE_IN, NOT_SILENT, STALE})
INVALID_OUTCOMES = frozenset({BARGE_IN, NOT_SILENT, STALE})


class Bucket(TypedDict):
    n: int
    answered: int
    timeouts: int
    invalid: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class ConnectBucket(TypedDict):
    n: int
    p50_ms: float | None
    max_ms: float | None


def percentile(values: Sequence[float], q: float) -> float:
    """Return the ``q`` percentile using linear interpolation between ranks."""
    if len(values) == 0:
        raise ValueError("percentile of an empty sequence")
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"percentile must be between 0 and 100, got {q}")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def speech_end_index(
    samples: Sequence[int] | np.ndarray,
    *,
    threshold: float = SPEECH_THRESHOLD,
    sample_rate: int = 16_000,
) -> int:
    """Return the exclusive end of the last 20 ms window whose RMS is above threshold.

    The result is suitable for ``samples[:result]``.  A phrase without a loud
    window returns zero.
    """
    chunk = np.asarray(samples, dtype=np.int16).reshape(-1)
    window_samples = round(WINDOW_SECONDS * sample_rate)
    if window_samples <= 0:
        raise ValueError("sample_rate is too low for a 20 ms window")
    end = 0
    for start in range(0, chunk.size - window_samples + 1, window_samples):
        window = chunk[start : start + window_samples].astype(np.float64)
        if math.sqrt(float(np.mean(np.square(window)))) > threshold:
            end = start + window_samples
    return end


def _number(row: Mapping[str, object], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _bucket(rows: Sequence[Mapping[str, object]]) -> Bucket:
    latencies: list[float] = []
    answered = 0
    timeouts = 0
    invalid = 0
    for row in rows:
        outcome = row.get("outcome")
        if outcome == ANSWERED:
            answered += 1
            latency = _number(row, "latency_ms")
            if latency is not None:
                latencies.append(latency)
        elif outcome == TIMEOUT:
            timeouts += 1
        elif outcome in INVALID_OUTCOMES:
            invalid += 1
    return {
        "n": len(rows),
        "answered": answered,
        "timeouts": timeouts,
        "invalid": invalid,
        "p50_ms": percentile(latencies, 50) if latencies else None,
        "p95_ms": percentile(latencies, 95) if len(latencies) >= MIN_P95_ANSWERS else None,
        "max_ms": max(latencies) if latencies else None,
    }


def summarize(rows: Iterable[Mapping[str, object]]) -> dict[str, Bucket]:
    """Group phrase rows by category and add an ``overall`` bucket.

    Meta, connect and failure rows are not phrase turns and are ignored.
    """
    groups: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        if row.get("type") != "phrase":
            continue
        category = row.get("category")
        if not isinstance(category, str):
            continue
        groups.setdefault(category, []).append(row)
    overall = [row for grouped in groups.values() for row in grouped]
    summary = {category: _bucket(grouped) for category, grouped in groups.items()}
    summary["overall"] = _bucket(overall)
    return summary


def connect_summary(rows: Iterable[Mapping[str, object]]) -> ConnectBucket:
    """Summarize ``connect`` rows independently of the phrase categories."""
    latencies = [
        value for row in rows if row.get("type") == "connect" if (value := _number(row, "connect_ms")) is not None
    ]
    return {
        "n": len(latencies),
        "p50_ms": percentile(latencies, 50) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
    }


def answered_counts(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Count answered rows per phrase id for before/after comparisons."""
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("type") != "phrase" or row.get("outcome") != ANSWERED:
            continue
        identifier = row.get("id")
        if isinstance(identifier, str):
            counts[identifier] = counts.get(identifier, 0) + 1
    return counts


class PhraseTurn:
    """Pure phrase-turn state machine driven entirely by timestamps.

    The shell creates one instance per phrase.  ``begin`` opens the pre-phrase
    silence window, ``phrase_started`` opens the barge-in window,
    ``reference_reached`` starts the answer timeout, and ``poll`` advances the
    timeout and late-reply windows.  No method sleeps or reads a clock.
    """

    def __init__(
        self,
        *,
        silence_s: float = TURN_SILENCE_S,
        silence_window_s: float = SILENCE_WINDOW_S,
        answer_timeout_s: float = ANSWER_TIMEOUT_S,
        late_silence_s: float = LATE_SILENCE_S,
        late_window_s: float = LATE_WINDOW_S,
        barge_silence_s: float = BARGE_SILENCE_S,
    ) -> None:
        self._silence_s = silence_s
        self._silence_window_s = silence_window_s
        self._answer_timeout_s = answer_timeout_s
        self._late_silence_s = late_silence_s
        self._late_window_s = late_window_s
        self._barge_silence_s = barge_silence_s
        self._phase = "pre"
        self._begin_s = 0.0
        self._agent_active = False
        self._last_active_s: float | None = None
        self._phrase_start_s: float | None = None
        self._reference_s: float | None = None
        self._timeout_s: float | None = None
        self._answer_s: float | None = None
        self._outcome: str | None = None
        self._late_reply = False

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def outcome(self) -> str | None:
        return self._outcome

    @property
    def late_reply(self) -> bool:
        return self._late_reply

    @property
    def finished(self) -> bool:
        return self._phase == "done"

    @property
    def latency_ms(self) -> float | None:
        if self._outcome != ANSWERED or self._reference_s is None or self._answer_s is None:
            return None
        return round(max(0.0, self._answer_s - self._reference_s) * 1000, 1)

    def begin(self, now_s: float, *, agent_active: bool = False, last_active_s: float | None = None) -> None:
        self._phase = "pre"
        self._begin_s = now_s
        self._agent_active = agent_active
        self._last_active_s = last_active_s

    def observe_agent(self, *, active: bool, last_active_s: float | None) -> None:
        self._agent_active = active
        if last_active_s is not None and (self._last_active_s is None or last_active_s > self._last_active_s):
            self._last_active_s = last_active_s

    def silence_ready(self, now_s: float) -> bool:
        if self._phase != "pre" or self._agent_active:
            return False
        silent_since = self._last_active_s if self._last_active_s is not None else self._begin_s
        return now_s - silent_since >= self._silence_s

    def silence_expired(self, now_s: float) -> bool:
        return self._phase == "pre" and now_s - self._begin_s >= self._silence_window_s

    def mark_not_silent(self) -> None:
        if self._phase == "pre":
            self._outcome = NOT_SILENT
            self._phase = "done"

    def phrase_started(self, now_s: float) -> None:
        if self._phase != "pre":
            raise RuntimeError("phrase can only start while waiting for silence")
        self._phrase_start_s = now_s
        self._phase = "playing"

    def agent_started(self, onset_s: float) -> str | None:
        """Record a confirmed speech onset and decide the turn when possible."""
        if self._phase == "pre":
            self._agent_active = True
            return None
        if self._phase == "done" or self._phrase_start_s is None:
            return self._outcome
        self._agent_active = True
        if self._outcome is not None:
            return self._outcome
        if onset_s < self._phrase_start_s:
            self._outcome = STALE
            self._phase = "done"
        elif self._reference_s is None or onset_s < self._reference_s:
            # Speech began while the phrase was still playing.  Keeping the
            # same onset means the continuation can never be reused as an
            # answer once the reference is reached.
            self._outcome = BARGE_IN
            self._phase = "done"
        else:
            self._answer_s = onset_s
            self._outcome = ANSWERED
            self._phase = "done"
        return self._outcome

    def reference_reached(self, reference_s: float) -> None:
        if self._phase != "playing":
            return
        self._reference_s = reference_s
        if self._outcome is not None:
            self._phase = "done"
            return
        self._phase = "awaiting"
        self._timeout_s = reference_s + self._answer_timeout_s

    def poll(self, now_s: float) -> str | None:
        """Advance timeout and late-reply windows; return the decision so far."""
        if self._phase == "awaiting":
            if self._outcome is not None:
                self._phase = "done"
            elif self._timeout_s is not None and now_s >= self._timeout_s:
                self._outcome = TIMEOUT
                self._phase = "late"
        if self._phase == "late":
            timeout_s = self._timeout_s if self._timeout_s is not None else self._begin_s
            if self._last_active_s is not None and self._last_active_s > timeout_s:
                self._late_reply = True
            # The late window is a hard cap: an agent that never falls silent
            # must still end the turn with the explicit timeout outcome.
            if now_s >= timeout_s + self._late_window_s:
                self._outcome = TIMEOUT
                self._phase = "done"
            elif self._agent_active:
                return self._outcome
            else:
                silent_since = max(timeout_s, self._last_active_s or timeout_s)
                if now_s - silent_since >= self._late_silence_s:
                    self._phase = "done"
        return self._outcome

    def barge_silence_ready(self, now_s: float) -> bool:
        if self._agent_active:
            return False
        if self._last_active_s is None:
            return True
        return now_s - self._last_active_s >= self._barge_silence_s


class AudioOnset:
    """Find confirmed speech runs in a stream of int16 samples.

    Samples may arrive in chunks of any size.  A window is loud when its RMS
    exceeds ``threshold``; a run becomes active after two consecutive loud
    windows and ``onset_s`` reports the start of the first one.  ``active`` and
    ``last_active_s`` follow the same two-window rule, so an isolated loud
    window never counts as agent speech.  Timestamps are the time of the first
    sample in a chunk, in seconds.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD, sample_rate: int = 16_000) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self._threshold = float(threshold)
        self._sample_rate = sample_rate
        self._window_samples = round(WINDOW_SECONDS * sample_rate)
        if self._window_samples <= 0:
            raise ValueError("sample_rate is too low for a 20 ms window")
        self._pending = np.empty(0, dtype=np.int16)
        self._consecutive = 0
        self._first_loud_s: float | None = None
        self._onset_s: float | None = None
        self._active = False
        self._last_active_s: float | None = None

    @property
    def onset_s(self) -> float | None:
        return self._onset_s

    @property
    def active(self) -> bool:
        return self._active

    @property
    def last_active_s(self) -> float | None:
        return self._last_active_s

    def reset(self) -> None:
        self._pending = np.empty(0, dtype=np.int16)
        self._consecutive = 0
        self._first_loud_s = None
        self._onset_s = None
        self._active = False
        self._last_active_s = None

    def fed(
        self, timestamp_s: float, samples: np.ndarray | Sequence[int] | bytes | bytearray | memoryview
    ) -> float | None:
        """Consume samples and return the first onset once it is confirmed."""
        if isinstance(samples, (bytes, bytearray, memoryview)):
            chunk = np.frombuffer(samples, dtype=np.int16)
        else:
            chunk = np.asarray(samples, dtype=np.int16)
        if chunk.ndim != 1:
            chunk = chunk.reshape(-1)
        if chunk.size == 0:
            return None

        # The buffered tail began where this chunk's timestamp says it should
        # have; this keeps arbitrary frame sizes aligned on one timeline.
        window_start_s = timestamp_s - self._pending.size / self._sample_rate
        buffer = np.concatenate((self._pending, chunk))
        offset = 0
        onset: float | None = None
        while buffer.size - offset >= self._window_samples:
            window = buffer[offset : offset + self._window_samples].astype(np.float64)
            rms = math.sqrt(float(np.mean(np.square(window))))
            started_s = window_start_s + offset / self._sample_rate
            if rms > self._threshold:
                if self._consecutive == 0:
                    self._first_loud_s = started_s
                self._consecutive += 1
                if self._consecutive >= 2:
                    self._active = True
                    self._last_active_s = started_s + WINDOW_SECONDS
                    if self._onset_s is None:
                        self._onset_s = self._first_loud_s
                        onset = self._onset_s
            else:
                self._consecutive = 0
                self._first_loud_s = None
                self._active = False
            offset += self._window_samples
        self._pending = buffer[offset:]
        return onset
