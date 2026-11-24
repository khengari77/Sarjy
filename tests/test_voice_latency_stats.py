"""Offline tests for the voice latency probe's pure logic and CLI refusal."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

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
    answered_counts,
    connect_summary,
    percentile,
    speech_end_index,
    summarize,
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


def test_connect_summary_and_answered_counts():
    rows = [
        {"type": "connect", "round": 1, "connect_ms": 40.0},
        {"type": "connect", "round": 2, "connect_ms": 60.0},
        _row("chat", ANSWERED, 100.0, "chat-01"),
        _row("chat", ANSWERED, 120.0, "chat-01"),
        _row("chat", TIMEOUT, identifier="chat-02"),
    ]
    assert connect_summary(rows) == {"n": 2, "p50_ms": 50.0, "max_ms": 60.0}
    assert answered_counts(rows) == {"chat-01": 2}


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


def test_audio_onset_two_windows_activate_and_silence_deactivates():
    detector = AudioOnset()
    assert detector.fed(0.0, _window(1000)) is None
    assert detector.fed(0.02, _window(1000)) == pytest.approx(0.0)
    assert detector.active
    assert detector.last_active_s == pytest.approx(0.04)
    assert detector.fed(0.04, np.zeros(320, dtype=np.int16)) is None
    assert not detector.active
    assert detector.last_active_s == pytest.approx(0.04)
    assert detector.fed(0.06, _window(1000)) is None
    assert not detector.active


def test_audio_onset_inside_a_single_frame():
    detector = AudioOnset()
    samples = np.concatenate((np.zeros(8000, dtype=np.int16), np.full(1600, 1000, dtype=np.int16)))
    assert detector.fed(0.0, samples) == pytest.approx(0.5, abs=1 / 16000)


def test_audio_onset_arbitrary_frame_sizes_match_one_big_frame():
    samples = np.concatenate((np.zeros(8000, dtype=np.int16), np.full(1600, 1000, dtype=np.int16)))
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
    assert detector.fed(0.0, _window(1000)) is None
    assert detector.fed(0.02, _window(1000)) == pytest.approx(0.0)
    detector.reset()
    assert detector.onset_s is None
    assert not detector.active
    assert detector.last_active_s is None
    assert detector.fed(0.0, np.zeros(640, dtype=np.int16)) is None
    assert detector.onset_s is None


def test_audio_onset_threshold_is_exclusive():
    detector = AudioOnset(threshold=DEFAULT_THRESHOLD)
    at_threshold = np.full(640, DEFAULT_THRESHOLD, dtype=np.int16)
    assert detector.fed(0.0, at_threshold) is None
    detector.reset()
    above = np.full(640, DEFAULT_THRESHOLD + 1, dtype=np.int16)
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


def test_join_failure_writes_incomplete_for_every_remaining_phrase(monkeypatch):
    sink = _RecordingSink()
    phrases = _phrases("chat-01", "chat-02")

    async def fake_new_conversation(client, api):
        return "conversation"

    async def fake_run_session(client, api, conversation_id, round_number, remaining, audio, sink):
        return None

    monkeypatch.setattr(probe, "_new_conversation", fake_new_conversation)
    monkeypatch.setattr(probe, "_run_session", fake_run_session)
    asyncio.run(probe._run_round(None, "http://127.0.0.1:9", 2, phrases, {}, sink))
    incomplete = [row for row in sink.rows if row["type"] == "incomplete"]
    assert incomplete == [{"type": "incomplete", "round": 2, "remaining": ["chat-01", "chat-02"]}]


def test_session_start_error_is_incomplete_and_the_next_session_runs(monkeypatch):
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
    incomplete = [row for row in sink.rows if row["type"] == "incomplete"]
    assert attempts == probe.MAX_SESSIONS_PER_ROUND
    assert len(incomplete) == probe.MAX_SESSIONS_PER_ROUND
    assert all(row["error"] == "RuntimeError" for row in incomplete)
    assert all(row["remaining"] == ["chat-01"] for row in incomplete)
    assert "super-secret" not in json.dumps(sink.rows)


def test_git_head_runs_from_the_script_directory(monkeypatch, tmp_path):
    seen_cwd: list[object] = []

    def fake_run(*args, **kwargs):
        seen_cwd.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    assert probe._git_head() == "abc123"
    assert Path(seen_cwd[0]).resolve() == Path(probe.__file__).resolve().parent


def test_report_prints_invalid_and_connect_and_warns_on_count_difference(tmp_path, capsys):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "meta", "rounds": 1, "phrase_count": 2},
                {"type": "connect", "round": 1, "connect_ms": 40.0},
                _row("chat", ANSWERED, 100.0, "chat-01"),
                _row("chat", TIMEOUT, identifier="chat-02"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    after.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "meta", "rounds": 1, "phrase_count": 2},
                {"type": "connect", "round": 1, "connect_ms": 60.0},
                _row("chat", ANSWERED, 120.0, "chat-01"),
                _row("chat", BARGE_IN, identifier="chat-02"),
                _row("chat", ANSWERED, 130.0, "chat-02"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    assert report([before, after]) == 0
    captured = capsys.readouterr()
    assert "invalid" in captured.out
    assert "connect before" in captured.out
    assert "warning: answered counts differ for ids: chat-02" in captured.err


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


def test_the_probe_names_the_proxy_origin():
    from voice_latency_probe import _origin

    assert _origin("https://sarjy.example/api/v1") == "https://sarjy.example"
    assert _origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
