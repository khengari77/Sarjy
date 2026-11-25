"""build_report must not let a dead key or an empty run look like a clean result."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jev_ui_eval import build_report, percentile  # noqa: E402


def record(expected, got, ms=10):
    return {"text": "t", "expected": expected, "got": got, "ms": ms}


def test_all_errors_are_counted_as_errors_not_as_a_clean_run():
    report = build_report([record("theme:light", "ERR"), record("NONE", "ERR")])
    assert (report["errors"], report["missed"], report["false_triggers"]) == (2, 0, 0)
    assert report["correct"] == 0


def test_false_triggers_missed_and_multi_are_counted_separately():
    report = build_report(
        [
            record("NONE", "theme:dark"),
            record("NONE", "MULTI"),
            record("theme:light", "NONE"),
            record("MULTI", "NONE"),
            record("MULTI", "MULTI"),
            record("NONE", "NONE"),
        ]
    )
    assert report["false_triggers"] == 2
    assert report["missed"] == 1
    assert report["multi_wrong"] == 1
    assert report["correct"] == 2


def test_percentiles_handle_empty_single_and_even_samples():
    assert percentile([], 0.5) == 0
    assert percentile([7], 0.95) == 7
    assert percentile([1, 2, 3], 1.5) == 3
    assert build_report([record("NONE", "NONE", 100), record("NONE", "NONE", 200)])["p50_ms"] == 150
    assert build_report([])["p50_ms"] == 0


def test_a_dead_key_does_not_count_multi_cases_as_wrong():
    report = build_report([record("MULTI", "ERR"), record("theme:dark", "theme:light"), record("theme:dark", "MULTI")])
    assert report["multi_wrong"] == 0
    assert report["errors"] == 1
    assert report["wrong_action"] == 2
