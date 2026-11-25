"""Offline tests for the voice latency probe's kill and abort guards."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import voice_latency_probe as probe  # noqa: E402
from voice_latency_stats import ANSWERED, TIMEOUT  # noqa: E402


class _RecordingSink:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def write(self, row):
        self.rows.append(dict(row))


class _FakeRoom:
    def __init__(self) -> None:
        self.remote_participants: dict[str, object] = {}
        self.local_participant = _FakeLocalParticipant()
        self.handlers: dict[str, object] = {}

    def on(self, name, callback):
        self.handlers[name] = callback

    async def connect(self, url, token):
        return None

    async def disconnect(self):
        return None


class _FakeLocalParticipant:
    async def publish_track(self, track, options):
        return None


class _FakeLocalAudioTrack:
    @staticmethod
    def create_audio_track(name, source):
        return object()


class _FakeMicrophone:
    def __init__(self, source) -> None:
        self.source = source

    async def start(self):
        return None

    async def stop(self):
        return None


def _stub_session(monkeypatch, ended: list[str]) -> None:
    async def fake_start(client, api, conversation_id):
        return {"url": "wss://room", "token": "token", "id": "session"}, probe.time.monotonic()

    async def fake_end(client, api, session_id):
        ended.append(session_id)
        return True

    async def fake_wait_agent(joined):
        return None

    async def fake_greeting(monitor):
        return None

    monkeypatch.setattr(probe, "_start_session", fake_start)
    monkeypatch.setattr(probe, "_end_session", fake_end)
    monkeypatch.setattr(probe, "_wait_agent", fake_wait_agent)
    monkeypatch.setattr(probe, "_wait_greeting", fake_greeting)
    monkeypatch.setattr(probe.rtc, "Room", _FakeRoom)
    monkeypatch.setattr(probe.rtc, "AudioSource", lambda *args, **kwargs: object())
    monkeypatch.setattr(probe.rtc, "LocalAudioTrack", _FakeLocalAudioTrack)
    monkeypatch.setattr(probe, "Microphone", _FakeMicrophone)


def _phrases(count: int) -> list[probe.Phrase]:
    return [probe.Phrase(f"chat-{index:02d}", "chat", f"phrase {index}") for index in range(count)]


def _audio(phrases: list[probe.Phrase]) -> dict[str, object]:
    return {phrase.id: object() for phrase in phrases}


def test_sigterm_and_sigint_cancel_the_probe_task():
    cancelled: list[bool] = []

    class _Task:
        def cancel(self):
            cancelled.append(True)

    class _Loop:
        def __init__(self) -> None:
            self.handlers: dict[signal.Signals, object] = {}

        def add_signal_handler(self, signum, callback):
            self.handlers[signum] = callback

    loop = _Loop()
    installed = probe._install_cancel_handlers(loop, _Task())
    assert installed == [signal.SIGTERM, signal.SIGINT]
    for signum in installed:
        loop.handlers[signum]()
    assert cancelled == [True, True]


def test_five_consecutive_timeouts_abort_and_an_answer_resets_the_count(monkeypatch):
    sink = _RecordingSink()
    ended: list[str] = []
    _stub_session(monkeypatch, ended)
    phrases = _phrases(13)
    outcomes = [TIMEOUT] * 4 + [ANSWERED] + [TIMEOUT] * 5 + [ANSWERED] * 3
    played: list[str] = []

    async def fake_play(monitor, microphone, sink, phrase, round_number, samples):
        played.append(phrase.id)
        return outcomes.pop(0)

    monkeypatch.setattr(probe, "_play_phrase", fake_play)

    remaining = asyncio.run(
        probe._run_session(None, "http://127.0.0.1:9", "conversation", 2, phrases, _audio(phrases), sink)
    )

    assert remaining == phrases[10:]
    assert remaining.aborted is True
    assert len(played) == 10
    assert len(outcomes) == 3
    assert [row for row in sink.rows if row["type"] == "aborted"] == [
        {"type": "aborted", "reason": "consecutive_timeouts", "round": 2}
    ]
    assert ended == ["session"]


def test_the_round_stops_on_an_aborted_session(monkeypatch):
    sink = _RecordingSink()
    calls = 0

    async def fake_new_conversation(client, api):
        return "conversation"

    async def aborting_session(client, api, conversation_id, round_number, remaining, audio, sink):
        nonlocal calls
        calls += 1
        return probe.SessionRemaining(remaining[1:], aborted=True)

    monkeypatch.setattr(probe, "_new_conversation", fake_new_conversation)
    monkeypatch.setattr(probe, "_run_session", aborting_session)

    assert asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 1, _phrases(3), {}, sink)) == 3
    assert calls == 1
    assert [row for row in sink.rows if row["type"] == "incomplete"] == []


def test_a_cancelled_run_still_ends_the_session(monkeypatch):
    sink = _RecordingSink()
    ended: list[str] = []
    _stub_session(monkeypatch, ended)
    phrases = _phrases(2)
    started = asyncio.Event()

    async def hanging_play(*args, **kwargs):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(probe, "_play_phrase", hanging_play)

    async def run():
        task = asyncio.create_task(
            probe._run_session(None, "http://127.0.0.1:9", "conversation", 1, phrases, _audio(phrases), sink)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert ended == ["session"]
