"""Offline tests for the voice latency probe's pure logic and CLI refusal."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import httpx
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import voice_latency_probe as probe  # noqa: E402
from voice_latency_probe import JsonlSink, main, report  # noqa: E402
from voice_latency_stats import (  # noqa: E402
    ANSWERED,
    BARGE_IN,
    DEFAULT_THRESHOLD,
    NOT_SILENT,
    STALE,
    TIMEOUT,
    AudioOnset,
    PhraseTurn,
    connect_summary,
    percentile,
    speech_end_index,
    summarize,
    turn_counts,
)

PHRASES = ROOT / "scripts" / "voice_latency_phrases.json"
CATEGORY_COUNTS = {"chat": 8, "ui_command": 4, "memory": 4, "weather": 2, "negative": 2}


def test_percentile_edge_cases():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 101)
    assert percentile([7.0], 0) == 7.0
    assert percentile([7.0], 100) == 7.0
    assert percentile([0.0, 10.0], 50) == 5.0
    assert percentile([0.0, 10.0, 20.0], 25) == 5.0
    assert percentile([0.0, 10.0, 20.0], 75) == 15.0
    assert percentile([3.0, 1.0, 2.0], 0) == 1.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0


@given(
    values=st.lists(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False), min_size=1),
    first=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    second=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_percentile_is_bounded_and_monotone(values, first, second):
    low, high = sorted((first, second))
    low_value = percentile(values, low)
    high_value = percentile(values, high)
    assert min(values) <= low_value <= max(values)
    assert min(values) <= high_value <= max(values)
    assert low_value <= high_value


def _row(
    category: str,
    outcome: str,
    latency: float | None = None,
    identifier: str | None = None,
):
    return {
        "type": "phrase",
        "category": category,
        "id": identifier or category,
        "outcome": outcome,
        "latency_ms": latency,
    }


def test_summarize_counts_outcomes_and_null_p95():
    rows = [
        _row("chat", ANSWERED, 100.0),
        _row("chat", ANSWERED, 300.0),
        _row("chat", TIMEOUT),
        _row("chat", BARGE_IN),
        _row("chat", NOT_SILENT),
        _row("chat", STALE),
        _row("weather", ANSWERED, 200.0),
        {"type": "meta", "rounds": 1},
        {"type": "connect", "round": 1, "connect_ms": 42.0},
        {"type": "connect_failed", "round": 1},
        {"type": "incomplete", "round": 1, "remaining": []},
        {"type": "end_failed", "round": 1},
    ]
    summary = summarize(rows)
    assert summary["chat"] == {
        "n": 6,
        "answered": 2,
        "timeouts": 1,
        "invalid": 3,
        "p50_ms": 200.0,
        "p95_ms": None,
        "max_ms": 300.0,
    }
    assert summary["weather"]["n"] == 1
    assert summary["overall"]["n"] == 7
    assert summary["overall"]["answered"] == 3
    assert summary["overall"]["timeouts"] == 1
    assert summary["overall"]["invalid"] == 3
    assert summary["overall"]["p95_ms"] is None


def test_summarize_p95_requires_twenty_answers():
    nineteen = [_row("chat", ANSWERED, float(value)) for value in range(19)]
    twenty = [*nineteen, _row("chat", ANSWERED, 19.0)]
    assert summarize(nineteen)["chat"]["p95_ms"] is None
    assert summarize(twenty)["chat"]["p95_ms"] is not None
    assert summarize(twenty)["chat"]["p95_ms"] == pytest.approx(percentile(list(range(20)), 95))


def test_connect_summary_and_turn_counts():
    rows = [
        {"type": "connect", "round": 1, "connect_ms": 40.0},
        {"type": "connect", "round": 2, "connect_ms": 60.0},
        _row("chat", ANSWERED, 100.0, "chat-01"),
        _row("chat", ANSWERED, 120.0, "chat-01"),
        _row("chat", TIMEOUT, identifier="chat-02"),
        _row("chat", BARGE_IN, identifier="chat-03"),
    ]
    assert connect_summary(rows) == {"n": 2, "p50_ms": 50.0, "max_ms": 60.0}
    assert turn_counts(rows) == {
        "chat-01": {"answered": 2, "timeouts": 0},
        "chat-02": {"answered": 0, "timeouts": 1},
        "chat-03": {"answered": 0, "timeouts": 0},
    }


def _window(amplitude: int) -> np.ndarray:
    return np.full(320, amplitude, dtype=np.int16)


def test_speech_end_index_finds_last_loud_window():
    samples = np.concatenate((np.zeros(320, dtype=np.int16), _window(1000), _window(1000)))
    assert speech_end_index(samples) == 960


def test_speech_end_index_ignores_trailing_silence():
    samples = np.concatenate((_window(1000), _window(1000), np.zeros(640, dtype=np.int16)))
    assert speech_end_index(samples) == 640


def test_speech_end_index_threshold_is_exclusive():
    assert speech_end_index(_window(300)) == 0
    assert speech_end_index(_window(301)) == 320
    assert speech_end_index(np.zeros(640, dtype=np.int16)) == 0


def test_audio_onset_silence_never_fires():
    detector = AudioOnset()
    for index in range(100):
        assert detector.fed(index * 0.02, np.zeros(320, dtype=np.int16)) is None
    assert detector.onset_s is None
    assert not detector.active
    assert detector.last_active_s is None


def test_audio_onset_single_loud_window_does_not_fire_or_activate():
    detector = AudioOnset()
    assert detector.fed(0.0, np.zeros(320, dtype=np.int16)) is None
    assert detector.fed(0.02, np.full(320, 1000, dtype=np.int16)) is None
    assert not detector.active
    assert detector.last_active_s is None
    assert detector.fed(0.04, np.zeros(320, dtype=np.int16)) is None
    assert detector.onset_s is None


def test_audio_onset_two_windows_activate_but_do_not_confirm_an_onset():
    detector = AudioOnset()
    assert detector.fed(0.0, _window(1000)) is None
    assert detector.fed(0.02, _window(1000)) is None
    assert detector.active
    assert detector.last_active_s == pytest.approx(0.04)
    assert detector.onset_s is None
    assert detector.fed(0.04, np.zeros(320, dtype=np.int16)) is None
    assert not detector.active
    assert detector.last_active_s == pytest.approx(0.04)
    assert detector.fed(0.06, _window(1000)) is None
    assert not detector.active
    assert detector.onset_s is None


def test_audio_onset_needs_eight_loud_windows_and_reports_the_first():
    detector = AudioOnset()
    for index in range(7):
        assert detector.fed(index * 0.02, _window(1000)) is None
    assert detector.active
    assert detector.onset_s is None
    assert detector.fed(0.14, _window(1000)) == pytest.approx(0.0)
    assert detector.onset_s == pytest.approx(0.0)


def test_audio_onset_inside_a_single_frame():
    detector = AudioOnset()
    samples = np.concatenate((np.zeros(8000, dtype=np.int16), np.full(2560, 1000, dtype=np.int16)))
    assert detector.fed(0.0, samples) == pytest.approx(0.5, abs=1 / 16000)


def test_audio_onset_arbitrary_frame_sizes_match_one_big_frame():
    samples = np.concatenate((np.zeros(8000, dtype=np.int16), np.full(2560, 1000, dtype=np.int16)))
    whole = AudioOnset()
    assert whole.fed(0.0, samples) == pytest.approx(0.5, abs=1 / 16000)

    chunks = AudioOnset()
    onset = None
    for start in range(0, samples.size, 7):
        chunk = samples[start : start + 7]
        onset = chunks.fed(start / 16000, chunk) or onset
    assert onset == pytest.approx(whole.onset_s, abs=1 / 16000)


def test_audio_onset_reset():
    detector = AudioOnset()
    for index in range(8):
        detector.fed(index * 0.02, _window(1000))
    assert detector.onset_s == pytest.approx(0.0)
    detector.reset()
    assert detector.onset_s is None
    assert not detector.active
    assert detector.last_active_s is None
    assert detector.fed(0.0, np.zeros(640, dtype=np.int16)) is None
    assert detector.onset_s is None


def test_audio_onset_threshold_is_exclusive():
    detector = AudioOnset(threshold=DEFAULT_THRESHOLD)
    at_threshold = np.full(3200, DEFAULT_THRESHOLD, dtype=np.int16)
    assert detector.fed(0.0, at_threshold) is None
    detector.reset()
    above = np.full(3200, DEFAULT_THRESHOLD + 1, dtype=np.int16)
    assert detector.fed(0.0, above) == pytest.approx(0.0)


def _turn_at_reference(reference_s: float = 2.0) -> PhraseTurn:
    turn = PhraseTurn()
    turn.begin(0.0)
    assert turn.silence_ready(1.0)
    turn.phrase_started(1.0)
    turn.reference_reached(reference_s)
    return turn


def test_phrase_turn_answered_carries_latency():
    turn = _turn_at_reference()
    assert turn.agent_started(2.25) == ANSWERED
    assert turn.latency_ms == pytest.approx(250.0)
    assert turn.late_reply is False
    assert turn.finished


def test_phrase_turn_timeout_then_five_seconds_of_silence():
    turn = _turn_at_reference()
    assert turn.poll(21.9) is None
    assert turn.poll(22.0) == TIMEOUT
    assert not turn.finished
    assert turn.poll(26.9) == TIMEOUT
    assert not turn.finished
    assert turn.poll(27.0) == TIMEOUT
    assert turn.finished
    assert turn.late_reply is False
    assert turn.latency_ms is None


def test_phrase_turn_timeout_marks_late_reply_without_crediting_an_answer():
    turn = _turn_at_reference()
    assert turn.poll(22.0) == TIMEOUT
    turn.observe_agent(active=True, last_active_s=24.0)
    assert turn.poll(24.5) == TIMEOUT
    assert turn.late_reply is True
    assert turn.latency_ms is None
    turn.observe_agent(active=False, last_active_s=25.0)
    assert not turn.finished
    assert turn.poll(29.9) == TIMEOUT
    assert not turn.finished
    assert turn.poll(30.0) == TIMEOUT
    assert turn.finished


def test_phrase_turn_caps_late_window_while_agent_stays_active():
    turn = _turn_at_reference()
    assert turn.poll(22.0) == TIMEOUT
    turn.observe_agent(active=True, last_active_s=22.0)
    assert turn.poll(51.9) == TIMEOUT
    assert not turn.finished
    turn.observe_agent(active=True, last_active_s=51.9)
    assert turn.poll(52.0) == TIMEOUT
    assert turn.finished
    assert turn.latency_ms is None


def test_phrase_turn_barge_in_during_playback():
    turn = PhraseTurn()
    turn.begin(0.0)
    assert turn.silence_ready(1.0)
    turn.phrase_started(1.0)
    assert turn.agent_started(1.5) == BARGE_IN
    assert turn.finished
    assert turn.latency_ms is None
    turn.reference_reached(2.0)
    assert turn.outcome == BARGE_IN


def test_phrase_started_does_not_fabricate_barge_in_from_stale_activity():
    # The probe resets the monitor after it has waited for agent silence, so
    # activity recorded before phrase_started must not become a barge-in.
    turn = PhraseTurn()
    turn.begin(0.0, agent_active=True)
    assert not turn.silence_ready(10.0)
    turn.phrase_started(10.0)
    assert turn.outcome is None
    assert not turn.finished
    assert turn.phase == "playing"


def test_phrase_turn_not_silent_when_silence_window_expires():
    turn = PhraseTurn()
    turn.begin(0.0, agent_active=True)
    assert not turn.silence_expired(29.9)
    assert turn.silence_expired(30.0)
    assert not turn.silence_ready(30.0)
    turn.mark_not_silent()
    assert turn.outcome == NOT_SILENT
    assert turn.finished
    assert turn.latency_ms is None


def test_phrase_turn_stale_onset_did_not_begin_inside_phrase():
    turn = PhraseTurn()
    turn.begin(0.0)
    assert turn.silence_ready(1.0)
    turn.phrase_started(1.0)
    assert turn.agent_started(0.5) == STALE
    assert turn.finished
    assert turn.latency_ms is None


def test_pre_reference_onset_is_never_reused_as_an_answer():
    turn = _turn_at_reference()
    # The onset is confirmed only after the reference, but it began while the
    # phrase was still playing, so it is a barge-in, not an answer.
    assert turn.agent_started(1.5) == BARGE_IN
    assert turn.latency_ms is None
    # The continuation of that same speech must not become a second onset.
    assert turn.agent_started(2.5) == BARGE_IN
    assert turn.outcome == BARGE_IN
    assert turn.finished


def test_onset_exactly_at_reference_is_an_answer():
    turn = _turn_at_reference(2.0)
    assert turn.agent_started(2.0) == ANSWERED
    assert turn.latency_ms == 0.0


def test_phrase_turn_barge_silence_requires_one_and_a_half_seconds():
    turn = PhraseTurn()
    turn.begin(0.0)
    turn.phrase_started(1.0)
    assert turn.agent_started(1.5) == BARGE_IN
    turn.observe_agent(active=True, last_active_s=3.0)
    assert not turn.barge_silence_ready(4.0)
    turn.observe_agent(active=False, last_active_s=3.0)
    assert not turn.barge_silence_ready(4.4)
    assert turn.barge_silence_ready(4.5)


def test_phrases_file_shape_and_negative_text():
    entries = json.loads(PHRASES.read_text(encoding="utf-8"))
    assert len(entries) == 20
    assert len({entry["id"] for entry in entries}) == 20
    assert Counter(entry["category"] for entry in entries) == CATEGORY_COUNTS
    for entry in entries:
        assert set(entry) == {"id", "category", "text"}
        assert entry["text"].strip()
    negative = {entry["id"]: entry["text"] for entry in entries if entry["category"] == "negative"}
    assert negative == {
        "negative-01": "I like dark mode better than light mode",
        "negative-02": "My sister hates captions",
    }


def test_jsonl_sink_refuses_to_overwrite(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("existing\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        JsonlSink(path)


class _RecordingSink:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def write(self, row):
        self.rows.append(dict(row))


def _phrases(*identifiers: str) -> list[probe.Phrase]:
    return [probe.Phrase(identifier, "chat", identifier) for identifier in identifiers]


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


def _stub_rtc(monkeypatch, room) -> None:
    async def fake_start(client, api, conversation_id):
        return {"url": "wss://room", "token": "token", "id": "session"}, 0.0

    async def fake_end(client, api, session_id):
        return True

    monkeypatch.setattr(probe, "_start_session", fake_start)
    monkeypatch.setattr(probe, "_end_session", fake_end)
    monkeypatch.setattr(probe, "_wait_greeting", lambda monitor: asyncio.sleep(0))
    monkeypatch.setattr(probe.rtc, "Room", lambda: room)
    monkeypatch.setattr(probe.rtc, "AudioSource", lambda *args, **kwargs: object())
    monkeypatch.setattr(probe.rtc, "LocalAudioTrack", _FakeLocalAudioTrack)
    monkeypatch.setattr(probe, "Microphone", _FakeMicrophone)


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:9/api/v1/conversations")
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status, request=request))


def test_failed_sessions_retry_then_write_one_incomplete(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01", "chat-02")
    attempts = 0

    async def fake_new_conversation(client, api):
        return "conversation"

    async def fake_run_session(client, api, conversation_id, round_number, remaining, audio, sink, streak=None):
        nonlocal attempts
        attempts += 1
        probe._write_session_failed(sink, round_number, "RuntimeError")
        return list(remaining)

    monkeypatch.setattr(probe, "_new_conversation", fake_new_conversation)
    monkeypatch.setattr(probe, "_run_session", fake_run_session)
    asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 2, phrases, {}, sink))
    assert attempts == probe.MAX_SESSIONS_PER_ROUND
    assert [row for row in sink.rows if row["type"] == "session_failed"] == [
        {"type": "session_failed", "round": 2, "error": "RuntimeError"}
    ] * probe.MAX_SESSIONS_PER_ROUND
    assert [row for row in sink.rows if row["type"] == "incomplete"] == [
        {"type": "incomplete", "round": 2, "remaining": ["chat-01", "chat-02"]}
    ]


def test_a_failed_join_is_a_failed_session(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01")
    _stub_rtc(monkeypatch, _FakeRoom())

    async def failing_wait(joined):
        raise RuntimeError("agent participant did not join")

    monkeypatch.setattr(probe, "_wait_agent", failing_wait)
    remaining = asyncio.run(probe._run_session(None, "http://127.0.0.1:9", "conversation", 1, phrases, {}, sink))
    assert remaining == phrases
    assert sink.rows == [{"type": "session_failed", "round": 1, "error": "RuntimeError"}]


def test_a_hanging_room_connect_times_out(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01")

    class HangingRoom(_FakeRoom):
        async def connect(self, url, token):
            await asyncio.sleep(60)

    monkeypatch.setattr(probe, "ROOM_CONNECT_TIMEOUT_S", 0.01)
    _stub_rtc(monkeypatch, HangingRoom())
    remaining = asyncio.run(probe._run_session(None, "http://127.0.0.1:9", "conversation", 1, phrases, {}, sink))
    assert remaining == phrases
    assert sink.rows == [{"type": "session_failed", "round": 1, "error": "TimeoutError"}]


def test_session_start_error_is_a_failed_session_and_the_next_session_runs(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01")
    attempts = 0

    async def fake_new_conversation(client, api):
        return "conversation"

    async def failing_start(client, api, conversation_id):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("token=super-secret")

    monkeypatch.setattr(probe, "_new_conversation", fake_new_conversation)
    monkeypatch.setattr(probe, "_start_session", failing_start)
    asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 1, phrases, {}, sink))
    failed = [row for row in sink.rows if row["type"] == "session_failed"]
    assert attempts == probe.MAX_SESSIONS_PER_ROUND
    assert len(failed) == probe.MAX_SESSIONS_PER_ROUND
    assert all(row["error"] == "RuntimeError" for row in failed)
    assert [row for row in sink.rows if row["type"] == "incomplete"] == [
        {"type": "incomplete", "round": 1, "remaining": ["chat-01"]}
    ]
    assert "super-secret" not in json.dumps(sink.rows)


def test_conversation_error_writes_one_incomplete_and_returns_zero(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01", "chat-02")

    async def failing_new_conversation(client, api):
        raise RuntimeError("boom")

    monkeypatch.setattr(probe, "_new_conversation", failing_new_conversation)
    assert asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 1, phrases, {}, sink)) == 0
    assert sink.rows == [
        {
            "type": "incomplete",
            "round": 1,
            "remaining": ["chat-01", "chat-02"],
            "error": "RuntimeError",
        }
    ]


def test_authentication_error_prints_the_class_and_returns_two(monkeypatch, capsys):
    sink = _RecordingSink()

    async def failing_new_conversation(client, api):
        raise _status_error(401)

    monkeypatch.setattr(probe, "_new_conversation", failing_new_conversation)
    assert asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 1, _phrases("chat-01"), {}, sink)) == 2
    assert sink.rows == []
    assert capsys.readouterr().err.strip() == "HTTPStatusError"


def test_git_head_runs_from_the_script_directory(monkeypatch, tmp_path):
    seen_cwd: list[object] = []

    def fake_run(*args, **kwargs):
        seen_cwd.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    assert probe._git_head() == "abc123"
    assert Path(seen_cwd[0]).resolve() == Path(probe.__file__).resolve().parent


def test_report_prints_paths_invalid_connect_and_warns_on_count_difference(tmp_path, capsys):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "meta", "rounds": 1, "phrase_count": 2, "api_host_kind": "local", "git_head": "aaa"},
                {"type": "connect", "round": 1, "connect_ms": 40.0},
                _row("chat", ANSWERED, 100.0, "chat-01"),
                _row("chat", TIMEOUT, identifier="chat-02"),
            )
        )
        + "\n{not json\n",
        encoding="utf-8",
    )
    after.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "meta", "rounds": 1, "phrase_count": 2, "api_host_kind": "local", "git_head": "bbb"},
                {"type": "connect", "round": 1, "connect_ms": 60.0},
                _row("chat", ANSWERED, 120.0, "chat-01"),
                _row("chat", ANSWERED, 130.0, "chat-02"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    assert report([before, after]) == 0
    captured = capsys.readouterr()
    assert "invalid" in captured.out
    assert "n<20" in captured.out
    assert str(before) in captured.out
    assert str(after) in captured.out
    assert f"connect {before}" in captured.out
    assert "git_head" in captured.out
    assert "aaa" in captured.out
    assert "bbb" in captured.out
    assert "skipped 1 unparsable lines" in captured.err
    assert "warning: answered/timeout counts differ for ids: chat-02" in captured.err


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("rounds", 2),
        ("phrase_count", 3),
        ("api_host_kind", "remote"),
    ],
)
def test_report_exits_two_when_meta_differs_but_prints_both_heads(tmp_path, capsys, key, value):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    base = {"type": "meta", "rounds": 1, "phrase_count": 2, "api_host_kind": "local"}
    first.write_text(json.dumps({**base, "git_head": "head-a"}) + "\n", encoding="utf-8")
    second.write_text(json.dumps({**base, "git_head": "head-b", key: value}) + "\n", encoding="utf-8")
    assert report([first, second]) == 2
    captured = capsys.readouterr()
    assert str(first) in captured.out
    assert str(second) in captured.out
    assert "head-a" in captured.out
    assert "head-b" in captured.out
    assert f"meta rows differ in: {key}" in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["synthesize"],
        ["run", "--api", "http://127.0.0.1:9", "--rounds", "1", "--out", "unused.jsonl"],
    ],
)
def test_live_commands_refuse_without_live(argv, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert main(argv) == 2


def test_a_dead_microphone_fails_the_phrase_instead_of_hanging():
    import asyncio

    import numpy as np
    from voice_latency_probe import Microphone

    class BrokenSource:
        async def capture_frame(self, frame):
            raise ConnectionError

    async def run():
        microphone = Microphone(BrokenSource())
        await microphone.start()
        try:
            await asyncio.wait_for(microphone.play(np.zeros(160, dtype=np.int16)), 1)
        finally:
            await microphone.stop()

    with pytest.raises(RuntimeError, match="microphone stopped"):
        asyncio.run(run())


def test_a_stalled_microphone_raises_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(probe, "MICROPHONE_STALL_MARGIN_S", 0.01)

    class StalledSource:
        async def capture_frame(self, frame):
            await asyncio.sleep(60)

    async def run():
        microphone = probe.Microphone(StalledSource())
        await microphone.start()
        try:
            await asyncio.wait_for(microphone.play(np.zeros(160, dtype=np.int16)), 1)
        finally:
            await microphone.stop()

    with pytest.raises(RuntimeError, match="microphone stalled"):
        asyncio.run(run())


def test_agent_monitor_merges_one_detector_per_track():
    monitor = probe.AgentMonitor()
    for index in range(8):
        monitor.feed("track-a", 1.0 + index * 0.02, _window(1000))
    assert monitor.onset_s == pytest.approx(1.0)
    for index in range(8):
        monitor.feed("track-b", 0.5 + index * 0.02, _window(1000))
    assert monitor.onset_s == pytest.approx(0.5)
    assert monitor.active
    assert monitor.last_active_s == pytest.approx(1.16)


class _FakeTrack:
    def __init__(self, sid, kind) -> None:
        self.sid = sid
        self.kind = kind


class _FakePublication:
    def __init__(self, kind, track) -> None:
        self.kind = kind
        self.track = track


class _FakeParticipant:
    def __init__(self, kind, publications=None) -> None:
        self.kind = kind
        self.track_publications = publications or {}


class _RecordingAudio:
    def __init__(self, monitor) -> None:
        self.attached: list[str] = []

    def attach(self, track) -> None:
        self.attached.append(track.sid)


def test_only_agent_audio_tracks_are_attached(monkeypatch):
    monkeypatch.setattr(probe, "AgentAudio", _RecordingAudio)
    agent = _FakeParticipant(probe.rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    viewer = _FakeParticipant(probe.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD)
    audio_kind = probe.rtc.TrackKind.KIND_AUDIO

    subscribed = _FakeRoom()
    audio = probe._bind_room(subscribed, probe.AgentMonitor())
    subscribed.handlers["track_subscribed"](_FakeTrack("agent-subscribed", audio_kind), None, agent)
    subscribed.handlers["track_subscribed"](_FakeTrack("viewer-subscribed", audio_kind), None, viewer)
    subscribed.handlers["track_subscribed"](_FakeTrack("agent-video", probe.rtc.TrackKind.KIND_VIDEO), None, agent)
    assert audio.attached == ["agent-subscribed"]

    existing = _FakeRoom()
    existing.remote_participants = {
        "agent": _FakeParticipant(
            probe.rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
            {"audio": _FakePublication(audio_kind, _FakeTrack("agent-existing", audio_kind))},
        ),
        "viewer": _FakeParticipant(
            probe.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
            {"audio": _FakePublication(audio_kind, _FakeTrack("viewer-existing", audio_kind))},
        ),
    }
    existing_audio = _RecordingAudio(None)
    probe._attach_existing_audio(existing, existing_audio)
    assert existing_audio.attached == ["agent-existing"]


def test_end_session_retries_once_after_one_second(monkeypatch):
    sleeps: list[float] = []

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, method, url):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("first attempt fails")
            return httpx.Response(200, request=httpx.Request(method, url))

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)
    client = FlakyClient()
    assert asyncio.run(probe._end_session(client, "http://127.0.0.1:9", "session")) is True
    assert client.calls == 2
    assert sleeps == [1.0]


def test_the_probe_names_the_proxy_origin():
    from voice_latency_probe import _origin

    assert _origin("https://sarjy.example/api/v1") == "https://sarjy.example"
    assert _origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_run_and_report_agree_on_the_meta_row():
    written = probe._meta_row("http://127.0.0.1:8000", 2, 20)
    assert probe._read_meta([{"type": "connect"}, written]) == written
    assert (written["rounds"], written["phrase_count"], written["api_host_kind"]) == (2, 20, "local")


def test_an_interrupted_run_is_not_compared(tmp_path, capsys):
    meta = probe._meta_row("http://127.0.0.1:8000", 1, 2)
    row = {"type": "phrase", "id": "a", "category": "chat", "outcome": "answered", "latency_ms": 5.0}
    complete, cut = tmp_path / "complete.jsonl", tmp_path / "cut.jsonl"
    complete.write_text("\n".join(json.dumps(item) for item in (meta, row, row)))
    cut.write_text("\n".join(json.dumps(item) for item in (meta, row)))
    assert report([complete, complete]) == 0
    assert report([complete, cut]) == 2
    assert "cut.jsonl is not a complete run" in capsys.readouterr().err
