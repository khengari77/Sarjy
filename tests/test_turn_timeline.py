from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import turn_timeline as timeline  # noqa: E402


def record(**stage_values: int) -> dict[str, object]:
    return {"turn_id": "turn-1", "eot_ms": 100, "ttfa_ms": 900, "stages": stage_values}


def by_name(segments: list[timeline.Segment]) -> dict[str, timeline.Segment]:
    return {segment.name: segment for segment in segments}


def test_offsets_are_shifted_by_eot() -> None:
    segments = timeline.parse_turn(
        record(t_turn_start_begin_ms=0, t_turn_start_end_ms=120, t_command_begin_ms=120, t_command_end_ms=160),
    )

    named = by_name(segments)
    assert named["end of turn"] == timeline.Segment("end of turn", 0, 100)
    assert named["turn start"].start_ms == 100
    assert named["turn start"].end_ms == 220
    assert named["command"].start_ms == 220
    assert named["command"].end_ms == 260


def test_missing_keys_are_skipped() -> None:
    segments = timeline.parse_turn(record(t_command_begin_ms=10, t_command_end_ms=40))

    assert [segment.name for segment in segments] == ["end of turn", "command"]


def test_tts_starts_at_model_first_chunk() -> None:
    segments = timeline.parse_turn(
        record(
            t_model_first_chunk_ms=150,
            t_tts_first_ms=400,
        ),
    )

    tts = by_name(segments)["to tts"]
    assert (tts.start_ms, tts.end_ms) == (250, 500)


def test_critical_segment_is_the_latest_of_turn_start_command_and_model() -> None:
    segments = timeline.parse_turn(
        record(
            t_turn_start_begin_ms=0,
            t_turn_start_end_ms=50,
            t_command_begin_ms=0,
            t_command_end_ms=120,
            t_model_request_ms=0,
            t_model_first_chunk_ms=200,
        ),
    )

    critical = [segment.name for segment in segments if segment.critical]
    assert critical == ["model first chunk"]


def test_text_render_contains_each_segment_name() -> None:
    segments = timeline.parse_turn(
        record(
            t_turn_start_begin_ms=0,
            t_turn_start_end_ms=20,
            t_command_begin_ms=20,
            t_command_end_ms=60,
            t_model_request_ms=60,
            t_model_first_chunk_ms=250,
            t_tool_start_ms=100,
            t_tool_end_ms=180,
            t_tts_first_ms=500,
        ),
    )

    text = timeline.render_text("turn-1", segments, 600)

    for segment in segments:
        assert segment.name in text
    assert "critical" in text
    assert "ttfa_ms=600" in text


def test_html_is_self_contained_and_marks_critical_segments() -> None:
    segments = timeline.parse_turn(
        record(
            t_turn_start_begin_ms=0,
            t_turn_start_end_ms=30,
            t_command_begin_ms=30,
            t_command_end_ms=120,
            t_model_request_ms=120,
            t_model_first_chunk_ms=300,
        ),
    )
    document = timeline.render_html([{"turn_id": "turn-1", "segments": segments, "ttfa_ms": 900}])

    lowered = document.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "<script" not in lowered
    assert "<link" not in lowered
    assert "prefers-color-scheme" in lowered
    assert "critical" in lowered
    assert "turn-1" in document


def test_non_finite_and_inverted_values_do_not_crash():
    parse_turn = timeline.parse_turn

    record = {
        "turn_id": "t",
        "eot_ms": 500,
        "ttfa_ms": 1500,
        "stages": {
            "t_model_request_ms": float("inf"),
            "t_model_first_chunk_ms": float("nan"),
            "t_tts_first_ms": 100,
        },
    }
    names = [segment.name for segment in parse_turn(record)]
    assert "model first chunk" not in names and "to tts" not in names
    assert "tts to audio" in names


def test_tts_gap_segments_follow_the_model_first_chunk():
    parse_turn = timeline.parse_turn

    record = {
        "turn_id": "t",
        "eot_ms": 100,
        "ttfa_ms": 900,
        "stages": {"t_model_request_ms": 0, "t_model_first_chunk_ms": 200, "t_tts_first_ms": 300},
    }
    by_name = {segment.name: segment for segment in parse_turn(record)}
    assert (by_name["to tts"].start_ms, by_name["to tts"].end_ms) == (300, 400)
    assert (by_name["tts to audio"].start_ms, by_name["tts to audio"].end_ms) == (400, 900)
