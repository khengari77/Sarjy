"""Live Jev UI-command evaluation harness (manual; never run in CI, makes paid-quota calls).

Usage: JEV_API_KEY=... uv run python scripts/jev_ui_eval.py [--limit N] [--out PATH]
The key is read only from the environment; nothing is printed or stored with it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import time
from pathlib import Path

import httpx
from app.domain.ui_routing import (
    UiActionDecision,
    UiClarificationDecision,
    UiDecision,
    UiVoiceGenderDecision,
    build_ui_request,
    evaluate_ui_classification,
    parse_ui_response,
)
from app.services.jev import JEV_ENDPOINT

CASES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "jev_ui_live_cases.json"
ACTION_LABEL = re.compile(r"^(theme:(light|dark|system)|captions:(on|off)|speed:[+-]|voice:(male|female))$")


def percentile(ordered: list[int], fraction: float) -> int:
    """Nearest-rank percentile over an already sorted list."""
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))] if ordered else 0


def build_report(records: list[dict[str, object]]) -> dict[str, object]:
    expected_labels = sorted({str(record["expected"]) for record in records})
    per_expected = {
        label: {
            "n": sum(record["expected"] == label for record in records),
            "correct": sum(record["expected"] == label and record["got"] == label for record in records),
        }
        for label in expected_labels
    }
    ordered = sorted(int(record["ms"]) for record in records)  # type: ignore[arg-type]
    return {
        "n": len(records),
        "correct": sum(record["expected"] == record["got"] for record in records),
        # An error is neither a clean miss nor a clean pass: count it on its own so a dead key or a
        # timeout storm cannot look like a perfect run.
        "errors": sum(record["got"] == "ERR" for record in records),
        "false_triggers": sum(
            record["expected"] == "NONE" and record["got"] not in ("NONE", "ERR") for record in records
        ),
        "missed": sum(record["expected"] not in ("NONE", "MULTI") and record["got"] == "NONE" for record in records),
        "multi_wrong": sum(record["expected"] == "MULTI" and record["got"] != "MULTI" for record in records),
        "per_expected": per_expected,
        "p50_ms": round(statistics.median(ordered)) if ordered else 0,
        "p95_ms": percentile(ordered, 0.95),
        "cases": records,
    }


def label(decision: UiDecision | None) -> str:
    if decision is None:
        return "NONE"
    if isinstance(decision, UiClarificationDecision):
        return "MULTI"
    if isinstance(decision, UiVoiceGenderDecision):
        return f"voice:{decision.gender}"
    assert isinstance(decision, UiActionDecision)
    action = decision.action
    kind = type(action).__name__
    if kind == "SetTheme":
        return f"theme:{action.theme}"  # type: ignore[attr-defined]
    if kind == "SetCaptionsVisible":
        return f"captions:{'on' if action.visible else 'off'}"  # type: ignore[attr-defined]
    if kind == "AdjustSpeakingSpeed":
        return f"speed:{'+' if action.delta > 0 else '-'}"  # type: ignore[attr-defined]
    return kind


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        raise SystemExit("JEV_API_KEY is not set")
    cases = json.loads(CASES.read_text())[: args.limit]
    if not cases:
        raise SystemExit("no cases to run")
    if args.out:
        # Fail before the paid run, not after it: a bad path must not discard the results.
        args.out.parent.mkdir(parents=True, exist_ok=True)
    correct = 0
    latencies: list[int] = []
    records: list[dict[str, object]] = []
    try:
        with httpx.Client(timeout=5) as client:
            for case in cases:
                started = time.monotonic()
                body = None
                try:
                    response = client.post(
                        JEV_ENDPOINT,
                        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                        json=build_ui_request(case["text"]),
                    )
                    body = response.json() if response.status_code == 200 else None
                except (httpx.HTTPError, ValueError):
                    body = None
                latencies.append(round((time.monotonic() - started) * 1000))
                result = parse_ui_response(body) if body else None
                got = label(evaluate_ui_classification(result)) if result else "ERR"
                records.append({"text": case["text"], "expected": case["expected"], "got": got, "ms": latencies[-1]})
                if got == case["expected"]:
                    correct += 1
                    continue
                gates = ""
                if result:
                    family = result.family.choice
                    detail = (
                        getattr(result, family, None) if family in ("theme", "captions", "speed", "voice") else None
                    )
                    gates = (
                        f"route={result.route.choice}:{result.route.probability:.2f} "
                        f"family={family}:{result.family.probability:.2f}"
                        + (f" detail={detail.choice}:{detail.probability:.2f}" if detail else "")
                    )
                print(f"MISS {case['text']!r} want={case['expected']} got={got} {gates}")
    finally:
        # Written even when the run is interrupted, so paid results are never lost.
        if args.out:
            args.out.write_text(json.dumps(build_report(records), indent=2) + "\n", encoding="utf-8")
    ordered = sorted(latencies)
    print(
        f"correct {correct}/{len(cases)} latency ms: median {statistics.median(ordered):.0f} "
        f"p90 {ordered[int(len(ordered) * 0.9)]} max {ordered[-1]}"
    )


if __name__ == "__main__":
    main()
