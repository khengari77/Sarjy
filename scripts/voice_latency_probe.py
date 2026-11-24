"""Offline-safe I/O shell for the voice latency probe.

Two live subcommands exist:

* ``synthesize`` creates the 20 phrase WAVs once through Deepgram TTS.
* ``run`` drives a real LiveKit call and streams latency rows to JSONL.

Neither touches the network or the provider key without ``--live``. ``report``
is always offline and reads only the JSONL rows.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from livekit import rtc

try:
    from voice_latency_stats import (
        ANSWERED,
        BARGE_IN,
        DEFAULT_THRESHOLD,
        SILENCE_WINDOW_S,
        AudioOnset,
        PhraseTurn,
        answered_counts,
        connect_summary,
        speech_end_index,
        summarize,
    )
except ModuleNotFoundError:  # Imported as scripts.voice_latency_probe from the repo root.
    from scripts.voice_latency_stats import (
        ANSWERED,
        BARGE_IN,
        DEFAULT_THRESHOLD,
        SILENCE_WINDOW_S,
        AudioOnset,
        PhraseTurn,
        answered_counts,
        connect_summary,
        speech_end_index,
        summarize,
    )

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.010
FRAME_SAMPLES = round(SAMPLE_RATE * FRAME_SECONDS)
COOKIE_NAME = "sarjy_demo"
ACCOUNT_COOKIE_NAME = "sarjy_account"
DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_TTS_MODEL = "aura-2-thalia-en"
PHRASES_PATH = Path(__file__).with_name("voice_latency_phrases.json")
DEFAULT_AUDIO_DIR = Path("artifacts/voice-probe")
DEFAULT_REPORT = Path("artifacts/voice-latency.jsonl")
GREETING_SILENCE_S = 1.5
AGENT_JOIN_TIMEOUT_S = 20.0
# The API gives sessions 10 minutes; stop comfortably before that expiry.
SESSION_LIMIT_S = 480.0
MAX_SESSIONS_PER_ROUND = 3
POLL_SECONDS = 0.02


@dataclass(frozen=True)
class Phrase:
    id: str
    category: str
    text: str


def load_phrases(path: Path = PHRASES_PATH) -> list[Phrase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Phrase(id=item["id"], category=item["category"], text=item["text"]) for item in raw]


def _api_url(api: str, path: str) -> str:
    base = api.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{path}"
    if base.endswith("/api"):
        return f"{base}/v1/{path}"
    return f"{base}/api/v1/{path}"


def _require_live(live: bool, command: str) -> int | None:
    if live:
        return None
    print(f"{command} requires --live; refusing to touch the network or provider key.", file=sys.stderr)
    return 2


def _check_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as audio:
        shape = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
    if shape != (1, 2, SAMPLE_RATE):
        raise SystemExit(f"{path} is not 16 kHz mono PCM16 WAV (got {shape})")


def load_wav(path: Path) -> np.ndarray:
    _check_wav(path)
    with wave.open(str(path), "rb") as audio:
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).copy()


def load_phrase_audio(path: Path) -> np.ndarray:
    """Load a phrase and trim it to the end of its final speech window."""
    samples = load_wav(path)
    trimmed = samples[: speech_end_index(samples)]
    if trimmed.size == 0:
        raise SystemExit(f"no speech found in {path}")
    return trimmed


def synthesize(out: Path) -> int:
    key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not key:
        print("DEEPGRAM_API_KEY is not set", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    params = {
        "model": DEEPGRAM_TTS_MODEL,
        "encoding": "linear16",
        "sample_rate": str(SAMPLE_RATE),
        "container": "wav",
    }
    headers = {"Authorization": f"Token {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        for phrase in load_phrases():
            target = out / f"{phrase.id}.wav"
            if target.exists():
                continue
            response = client.post(DEEPGRAM_SPEAK_URL, params=params, headers=headers, json={"text": phrase.text})
            response.raise_for_status()
            temporary = target.with_suffix(".wav.tmp")
            temporary.write_bytes(response.content)
            try:
                _check_wav(temporary)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            temporary.replace(target)
    return 0


class JsonlSink:
    """Write each row immediately so an interrupted probe keeps its evidence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("x", encoding="utf-8", buffering=1)

    def write(self, row: Mapping[str, object]) -> None:
        self._file.write(json.dumps(row, sort_keys=True) + "\n")

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AgentMonitor:
    """Track confirmed agent speech, greeting state and the first turn onset."""

    def __init__(self) -> None:
        self._detector = AudioOnset(threshold=DEFAULT_THRESHOLD, sample_rate=SAMPLE_RATE)
        self.greeting_started = False
        self.onset_s: float | None = None
        self.onset = asyncio.Event()

    @property
    def active(self) -> bool:
        return self._detector.active

    @property
    def last_active_s(self) -> float | None:
        return self._detector.last_active_s

    def reset_turn(self) -> None:
        self._detector.reset()
        self.onset_s = None
        self.onset.clear()

    def feed(self, timestamp_s: float, samples: np.ndarray) -> None:
        onset = self._detector.fed(timestamp_s, samples)
        if self._detector.active:
            self.greeting_started = True
        if onset is not None:
            self.onset_s = onset
            self.onset.set()


class AgentAudio:
    """Consume every subscribed agent audio track on the probe's event loop."""

    def __init__(self, monitor: AgentMonitor) -> None:
        self._monitor = monitor
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def attach(self, track: rtc.Track) -> None:
        if track.sid in self._tasks:
            return
        self._tasks[track.sid] = asyncio.create_task(self._consume(track))

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        try:
            async for event in stream:
                frame = event.frame
                samples = np.frombuffer(frame.data, dtype=np.int16)
                timestamp = time.monotonic() - samples.size / SAMPLE_RATE
                self._monitor.feed(timestamp, samples)
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()


class Microphone:
    """One real-time 10 ms task: silence by default, the phrase when asked."""

    def __init__(self, source: rtc.AudioSource) -> None:
        self._source = source
        self._queue: asyncio.Queue[tuple[np.ndarray, asyncio.Event]] = asyncio.Queue()
        self._playing: np.ndarray | None = None
        self._offset = 0
        self._done: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self.reference_s: float | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def play(self, samples: np.ndarray) -> float:
        """Play the trimmed phrase and return the reference timestamp."""
        done = asyncio.Event()
        self._queue.put_nowait((samples, done))
        if self._task is None:
            raise RuntimeError("microphone is not running")
        finished = asyncio.create_task(done.wait())
        try:
            # A dead microphone never finishes a phrase; do not wait for it.
            await asyncio.wait({finished, self._task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            finished.cancel()
        if not done.is_set():
            raise RuntimeError("microphone stopped during playback")
        if self.reference_s is None:
            raise RuntimeError("phrase playback did not produce a reference")
        return self.reference_s

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        next_tick = time.monotonic()
        while True:
            if self._playing is None and not self._queue.empty():
                self._playing, self._done = self._queue.get_nowait()
                self._offset = 0
                self.reference_s = None
            if self._playing is not None:
                chunk = self._playing[self._offset : self._offset + FRAME_SAMPLES]
                self._offset += chunk.size
                if chunk.size < FRAME_SAMPLES:
                    chunk = np.pad(chunk, (0, FRAME_SAMPLES - chunk.size))
                await self._source.capture_frame(
                    rtc.AudioFrame(
                        data=chunk.astype(np.int16).tobytes(),
                        sample_rate=SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=FRAME_SAMPLES,
                    )
                )
                if self._offset >= self._playing.size:
                    self.reference_s = time.monotonic() + self._source.queued_duration
                    self._playing = None
                    if self._done is not None:
                        self._done.set()
                        self._done = None
            else:
                await self._source.capture_frame(
                    rtc.AudioFrame(
                        data=bytes(FRAME_SAMPLES * 2),
                        sample_rate=SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=FRAME_SAMPLES,
                    )
                )
            next_tick += FRAME_SECONDS
            delay = next_tick - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_tick = time.monotonic()


async def _json_request(
    client: httpx.AsyncClient, method: str, url: str, body: Mapping[str, object] | None = None
) -> Any:
    response = await client.request(method, url, json=body) if body is not None else await client.request(method, url)
    response.raise_for_status()
    return response.json() if response.content else {}


def _cookie_value(cookie_env: str) -> str:
    value = os.environ.get(cookie_env, "")
    if not value:
        raise SystemExit(f"{cookie_env} is not set")
    return value


def _origin(api: str) -> str:
    """The web proxy refuses a POST whose Origin is not its own."""
    url = httpx.URL(api)
    return f"{url.scheme}://{url.netloc.decode('ascii')}"


async def _authenticate(client: httpx.AsyncClient, api: str, cookie_env: str | None, cookie_name: str) -> None:
    if cookie_env is not None:
        client.cookies.set(cookie_name, _cookie_value(cookie_env))
    else:
        response = await client.post(_api_url(api, "demo-session"))
        response.raise_for_status()
        # Keep the value but drop Set-Cookie attributes such as Secure so the
        # probe works against a local HTTP API as well as HTTPS.
        cookie = response.cookies.get(COOKIE_NAME)
        if cookie:
            client.cookies.set(COOKIE_NAME, cookie)


async def _new_conversation(client: httpx.AsyncClient, api: str) -> str:
    created = await _json_request(client, "POST", _api_url(api, "conversations"), {})
    return str(created["id"])


async def _start_session(client: httpx.AsyncClient, api: str, conversation_id: str) -> tuple[Any, float]:
    started = time.monotonic()
    body: dict[str, object] = {
        "conversation_id": conversation_id,
        "language": "en",
        "response_preference": "automatic",
    }
    session = await _json_request(client, "POST", _api_url(api, "voice/sessions"), body)
    return session, started


async def _end_session(client: httpx.AsyncClient, api: str, session_id: str) -> bool:
    try:
        await _json_request(client, "POST", _api_url(api, f"voice/sessions/{session_id}/end"))
    except httpx.HTTPStatusError as exc:
        print(f"end session failed: {exc.response.status_code}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"end session failed: {type(exc).__name__}", file=sys.stderr)
        return False
    return True


def _bind_room(room: rtc.Room, monitor: AgentMonitor) -> AgentAudio:
    audio = AgentAudio(monitor)
    room.on(
        "track_subscribed",
        lambda track, _publication, _participant: (
            audio.attach(track) if track.kind == rtc.TrackKind.KIND_AUDIO else None
        ),
    )
    return audio


def _attach_existing_audio(room: rtc.Room, audio: AgentAudio) -> None:
    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.kind == rtc.TrackKind.KIND_AUDIO and publication.track is not None:
                audio.attach(publication.track)


async def _wait_agent(joined: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(joined.wait(), AGENT_JOIN_TIMEOUT_S)
    except TimeoutError:
        raise RuntimeError("agent participant did not join the room") from None


async def _wait_greeting(monitor: AgentMonitor) -> None:
    start = time.monotonic()
    while True:
        now = time.monotonic()
        if monitor.greeting_started and not monitor.active and monitor.last_active_s is not None:
            if now - monitor.last_active_s >= GREETING_SILENCE_S:
                return
        elif not monitor.greeting_started and now - start >= 20.0:
            return
        if now - start >= 60.0:
            return
        await asyncio.sleep(POLL_SECONDS)


def _write_phrase_row(sink: JsonlSink, phrase: Phrase, round_number: int, turn: PhraseTurn) -> None:
    row: dict[str, object] = {
        "type": "phrase",
        "round": round_number,
        "id": phrase.id,
        "category": phrase.category,
        "outcome": turn.outcome,
        "late_reply": turn.late_reply,
    }
    if turn.outcome == ANSWERED:
        row["latency_ms"] = turn.latency_ms
    sink.write(row)


def _write_incomplete(
    sink: JsonlSink, round_number: int, phrases: Sequence[Phrase], *, error: str | None = None
) -> None:
    row: dict[str, object] = {
        "type": "incomplete",
        "round": round_number,
        "remaining": [phrase.id for phrase in phrases],
    }
    if error is not None:
        row["error"] = error
    sink.write(row)


async def _wait_turn_start(turn: PhraseTurn, monitor: AgentMonitor) -> bool:
    """Wait for a 1 s agent silence; return False when the 30 s window expires."""
    while True:
        now = time.monotonic()
        turn.observe_agent(active=monitor.active, last_active_s=monitor.last_active_s)
        if turn.silence_ready(now):
            return True
        if turn.silence_expired(now):
            return False
        await asyncio.sleep(POLL_SECONDS)


async def _await_turn(turn: PhraseTurn, monitor: AgentMonitor, fed_onset_s: float | None) -> None:
    while not turn.finished:
        now = time.monotonic()
        turn.observe_agent(active=monitor.active, last_active_s=monitor.last_active_s)
        if fed_onset_s is None and monitor.onset_s is not None:
            fed_onset_s = monitor.onset_s
            turn.agent_started(fed_onset_s)
        turn.poll(now)
        if turn.finished:
            return
        await asyncio.sleep(POLL_SECONDS)


async def _wait_barge_silence(turn: PhraseTurn, monitor: AgentMonitor) -> None:
    deadline = time.monotonic() + SILENCE_WINDOW_S
    while time.monotonic() < deadline:
        turn.observe_agent(active=monitor.active, last_active_s=monitor.last_active_s)
        if turn.barge_silence_ready(time.monotonic()):
            return
        await asyncio.sleep(POLL_SECONDS)


async def _play_phrase(
    monitor: AgentMonitor,
    microphone: Microphone,
    sink: JsonlSink,
    phrase: Phrase,
    round_number: int,
    samples: np.ndarray,
) -> None:
    turn = PhraseTurn()
    turn.begin(time.monotonic(), agent_active=monitor.active, last_active_s=monitor.last_active_s)
    if not await _wait_turn_start(turn, monitor):
        turn.mark_not_silent()
        _write_phrase_row(sink, phrase, round_number, turn)
        return

    monitor.reset_turn()
    turn.phrase_started(time.monotonic())
    reference_s = await microphone.play(samples)
    fed_onset_s = monitor.onset_s
    if fed_onset_s is not None:
        turn.agent_started(fed_onset_s)
    turn.reference_reached(reference_s)
    await _await_turn(turn, monitor, fed_onset_s)
    _write_phrase_row(sink, phrase, round_number, turn)
    if turn.outcome == BARGE_IN:
        await _wait_barge_silence(turn, monitor)


async def _run_session(
    client: httpx.AsyncClient,
    api: str,
    conversation_id: str,
    round_number: int,
    phrases: Sequence[Phrase],
    audio: Mapping[str, np.ndarray],
    sink: JsonlSink,
) -> tuple[list[Phrase], bool] | None:
    """Run one session.

    Return ``(phrases_left, recorded_incomplete)`` so the caller knows whether
    the failed session already wrote its own ``incomplete`` row, or ``None``
    when the agent did not join and the round must stop.
    """
    session: Any = None
    room: rtc.Room | None = None
    agent_audio: AgentAudio | None = None
    microphone: Microphone | None = None
    try:
        try:
            session, started = await _start_session(client, api, conversation_id)
            room = rtc.Room()
            monitor = AgentMonitor()
            joined = asyncio.Event()
            room.on(
                "participant_connected",
                lambda participant: (
                    joined.set() if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT else None
                ),
            )
            agent_audio = _bind_room(room, monitor)
            await room.connect(str(session["url"]), str(session["token"]))
            for participant in room.remote_participants.values():
                if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                    joined.set()
            _attach_existing_audio(room, agent_audio)
            source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=100)
            track = rtc.LocalAudioTrack.create_audio_track("voice-latency-probe", source)
            await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            microphone = Microphone(source)
            await microphone.start()
        except Exception as exc:
            # Only the class name: exception text may contain session tokens.
            _write_incomplete(sink, round_number, phrases, error=type(exc).__name__)
            return list(phrases), True
        try:
            await _wait_agent(joined)
        except RuntimeError as exc:
            sink.write({"type": "connect_failed", "round": round_number, "error": type(exc).__name__})
            return None
        sink.write(
            {
                "type": "connect",
                "round": round_number,
                "connect_ms": round((time.monotonic() - started) * 1000, 1),
            }
        )
        await _wait_greeting(monitor)
        remaining: list[Phrase] = []
        for index, phrase in enumerate(phrases):
            if time.monotonic() - started >= SESSION_LIMIT_S:
                remaining = list(phrases[index:])
                break
            try:
                await _play_phrase(monitor, microphone, sink, phrase, round_number, audio[phrase.id])
            except Exception as exc:
                remaining = list(phrases[index:])
                _write_incomplete(sink, round_number, remaining, error=type(exc).__name__)
                return remaining, True
        return remaining, False
    finally:
        if microphone is not None:
            await microphone.stop()
        if agent_audio is not None:
            await agent_audio.close()
        if room is not None:
            with contextlib.suppress(Exception):
                await room.disconnect()
        if session is not None and not await _end_session(client, api, str(session["id"])):
            sink.write({"type": "end_failed", "round": round_number})


async def _run_round(
    client: httpx.AsyncClient,
    api: str,
    round_number: int,
    phrases: Sequence[Phrase],
    audio: Mapping[str, np.ndarray],
    sink: JsonlSink,
) -> None:
    conversation_id = await _new_conversation(client, api)
    remaining = list(phrases)
    sessions = 0
    recorded = False
    while remaining and sessions < MAX_SESSIONS_PER_ROUND:
        sessions += 1
        outcome = await _run_session(client, api, conversation_id, round_number, remaining, audio, sink)
        if outcome is None:
            _write_incomplete(sink, round_number, remaining)
            return
        remaining, recorded = outcome
    if remaining and not recorded:
        _write_incomplete(sink, round_number, remaining)


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _api_host_kind(api: str) -> str:
    host = (httpx.URL(api).host or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return "local"
    return "remote"


def _meta_row(api: str, rounds: int, phrase_count: int) -> dict[str, object]:
    return {
        "type": "meta",
        "utc": datetime.now(UTC).isoformat(),
        "git_head": _git_head(),
        "api_host_kind": _api_host_kind(api),
        "rounds": rounds,
        "phrase_count": phrase_count,
    }


async def _run_probe(
    api: str, rounds: int, out: Path, cookie_env: str | None, audio_dir: Path, cookie_name: str
) -> int:
    phrases = load_phrases()
    missing = [phrase.id for phrase in phrases if not (audio_dir / f"{phrase.id}.wav").exists()]
    if missing:
        print(f"missing phrase audio in {audio_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2
    audio = {phrase.id: load_phrase_audio(audio_dir / f"{phrase.id}.wav") for phrase in phrases}
    try:
        sink = JsonlSink(out)
    except FileExistsError:
        print(f"{out} already exists; refusing to overwrite", file=sys.stderr)
        return 2
    with sink:
        sink.write(_meta_row(api, rounds, len(phrases)))
        print(
            "warning: memory and settings phrases change that identity's data",
            file=sys.stderr,
        )
        async with httpx.AsyncClient(timeout=30.0, headers={"origin": _origin(api)}) as client:
            await _authenticate(client, api, cookie_env, cookie_name)
            for round_number in range(1, rounds + 1):
                await _run_round(client, api, round_number, phrases, audio, sink)
    return 0


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _cells(bucket: Mapping[str, object]) -> list[str]:
    return [
        str(bucket["n"]),
        str(bucket["answered"]),
        str(bucket["timeouts"]),
        str(bucket["invalid"]),
        _fmt(bucket["p50_ms"]) if isinstance(bucket["p50_ms"], float) else "-",
        _fmt(bucket["p95_ms"]) if isinstance(bucket["p95_ms"], float) else "-",
        _fmt(bucket["max_ms"]) if isinstance(bucket["max_ms"], float) else "-",
    ]


_SINGLE_WIDTHS = (4, 8, 8, 8, 8, 8, 8)
_HEADINGS = ("n", "answered", "timeouts", "invalid", "p50_ms", "p95_ms", "max_ms")


def _table_line(label: str, cells: Sequence[str], widths: Sequence[int] = _SINGLE_WIDTHS) -> str:
    return f"{label:<14} " + " ".join(f"{cell:>{width}}" for cell, width in zip(cells, widths, strict=True))


def _print_connect(label: str, bucket: Mapping[str, object]) -> None:
    print(f"{label:<14} n={bucket['n']} p50_ms={_fmt(bucket['p50_ms'])} max_ms={_fmt(bucket['max_ms'])}")


def _print_single(summary: Mapping[str, Mapping[str, object]]) -> None:
    print(_table_line("category", _HEADINGS))
    for category, bucket in summary.items():
        print(_table_line(category, _cells(bucket)))


def _print_side_by_side(summaries: Sequence[Mapping[str, Mapping[str, object]]], labels: Sequence[str]) -> None:
    categories: list[str] = []
    for summary in summaries:
        for category in summary:
            if category not in categories:
                categories.append(category)
    widths = (4, 8, 8, 8, 7, 7, 7)
    head = " ".join(f"{label:^{sum(widths) + 2}}" for label in labels)
    print(f"{'category':<14} {head}")
    print(_table_line("", _HEADINGS * len(summaries), widths * len(summaries)))
    for category in categories:
        cells: list[str] = []
        for summary in summaries:
            bucket = summary.get(category)
            cells.extend(_cells(bucket) if bucket is not None else ["-"] * 7)
        print(_table_line(category, cells, widths * len(summaries)))


def report(paths: Sequence[Path]) -> int:
    if not paths or len(paths) > 2:
        print("report takes one or two JSONL files", file=sys.stderr)
        return 2
    files = [_read_rows(path) for path in paths]
    summaries = [summarize(rows) for rows in files]
    if len(summaries) == 1:
        _print_single(summaries[0])
        _print_connect("connect", connect_summary(files[0]))
        return 0
    _print_side_by_side(summaries, ["before", "after"])
    for label, rows in zip(("connect before", "connect after"), files, strict=True):
        _print_connect(label, connect_summary(rows))
    before = answered_counts(files[0])
    after = answered_counts(files[1])
    if before != after:
        differing = sorted(
            identifier for identifier in {*before, *after} if before.get(identifier, 0) != after.get(identifier, 0)
        )
        print(f"warning: answered counts differ for ids: {', '.join(differing)}", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice latency probe")
    commands = parser.add_subparsers(dest="command", required=True)

    synthesize_parser = commands.add_parser("synthesize", help="create phrase WAVs through Deepgram")
    synthesize_parser.add_argument("--out", type=Path, default=DEFAULT_AUDIO_DIR)
    synthesize_parser.add_argument("--live", action="store_true", help="allow provider calls")

    run_parser = commands.add_parser("run", help="measure latency in a real LiveKit call")
    run_parser.add_argument("--api", required=True)
    run_parser.add_argument("--rounds", type=int, default=1)
    run_parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    run_parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    run_parser.add_argument("--cookie-env", default=None)
    run_parser.add_argument("--cookie-name", choices=(COOKIE_NAME, ACCOUNT_COOKIE_NAME), default=COOKIE_NAME)
    run_parser.add_argument("--live", action="store_true", help="allow network calls")

    report_parser = commands.add_parser("report", help="print a plain latency table")
    report_parser.add_argument("files", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return report(args.files)
    refused = _require_live(args.live, args.command)
    if refused is not None:
        return refused
    if args.command == "synthesize":
        return synthesize(args.out)
    try:
        return asyncio.run(
            _run_probe(args.api, args.rounds, args.out, args.cookie_env, args.audio_dir, args.cookie_name)
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
