"""Offline tests for the cache probe's pure helpers."""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cache_probe  # noqa: E402


def test_serialize_messages_is_compact_and_stable():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "héllo"}]
    assert cache_probe.serialize_messages(messages) == (
        '[{"role":"system","content":"rules"},{"role":"user","content":"héllo"}]'
    )


def test_aligned_common_prefix_rounds_down_to_256():
    assert cache_probe.aligned_common_prefix(None, "x" * 300) == 0
    assert cache_probe.aligned_common_prefix("a" * 200 + "b", "a" * 200 + "c") == 0
    assert cache_probe.aligned_common_prefix("a" * 300 + "b", "a" * 300 + "c") == 256
    assert cache_probe.aligned_common_prefix("a" * 1000, "a" * 1000) == 768


def test_turn_buckets():
    buckets = [cache_probe.turn_bucket(turn) for turn in (1, 2, 3, 4, 10, 11)]
    assert buckets == ["1", "2-3", "2-3", "4-10", "4-10", "4-10"]


def test_percentile_interpolates_and_handles_empty():
    assert cache_probe.percentile([], 0.5) is None
    assert cache_probe.percentile([4.0], 0.95) == 4.0
    assert cache_probe.percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert math.isclose(cache_probe.percentile([1.0, 2.0], 0.95), 1.95)


def test_summarize_rows_uses_hit_over_hit_plus_miss():
    rows = [
        {"turn": 1, "hit": 100, "miss": 100, "prompt_tokens": 200, "ttft_ms": 100.0},
        {"turn": 2, "hit": 200, "miss": 0, "prompt_tokens": 200, "ttft_ms": 300.0},
        {"turn": 4, "hit": 50, "miss": 50, "prompt_tokens": 100, "ttft_ms": 500.0},
    ]
    summary = cache_probe.summarize_rows(rows)

    assert summary["1"] == {
        "n": 1,
        "hit_pct": 50.0,
        "mean_miss_tokens": 100.0,
        "mean_prompt_tokens": 200.0,
        "ttft_p50": 100.0,
        "ttft_p95": 100.0,
    }
    assert summary["2-3"]["hit_pct"] == 100.0
    assert summary["2-3"]["mean_miss_tokens"] == 0.0
    assert summary["4-10"]["hit_pct"] == 50.0
    assert summary["overall"]["n"] == 3
    assert summary["overall"]["hit_pct"] == 70.0
    assert math.isclose(summary["overall"]["mean_prompt_tokens"], 500 / 3)
    assert summary["overall"]["ttft_p50"] == 300.0
    assert summary["overall"]["ttft_p95"] == 480.0


def test_summarize_rows_ignores_missing_ttft_and_empty_input():
    summary = cache_probe.summarize_rows([{"turn": 1, "hit": 1, "miss": 0, "prompt_tokens": 1, "ttft_ms": None}])
    assert summary["1"]["ttft_p50"] is None

    empty = cache_probe.summarize_rows([])
    assert empty["overall"]["n"] == 0
    assert empty["overall"]["hit_pct"] is None
    assert set(empty) == {"1", "2-3", "4-10", "overall"}


def test_estimate_plan_counts_calls_and_tokens():
    plan = cache_probe.estimate_plan(10, 10)
    assert plan["calls"] == 100
    assert plan["prompt_tokens"] > 0
    assert plan["completion_tokens"] > 0
    assert plan["total_tokens"] == plan["prompt_tokens"] + plan["completion_tokens"]

    assert cache_probe.estimate_plan(1, 1)["calls"] == 1
    assert cache_probe.estimate_plan(3, 7)["calls"] == 21


def test_estimate_cost_is_all_cache_miss_upper_bound():
    assert math.isclose(cache_probe.estimate_cost_usd(1_000_000, 0), 0.28)
    assert math.isclose(cache_probe.estimate_cost_usd(0, 1_000_000), 0.42)


def test_every_conversation_opens_with_a_unique_nonce_so_runs_cannot_share_cache():
    phrase = cache_probe.PHRASES[0]
    first = cache_probe.opening_phrase(phrase, "aaaaaaaaaa")
    second = cache_probe.opening_phrase(phrase, "bbbbbbbbbb")
    assert first != second and first.endswith(phrase) and "aaaaaaaaaa" in first
