"""Live Jev UI-command evaluation harness (manual; never run in CI, makes paid-quota calls).

Usage: JEV_API_KEY=... uv run python scripts/jev_ui_eval.py [--limit N]
The key is read only from the environment; nothing is printed or stored with it.
"""

from __future__ import annotations

import argparse
import json
import os
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
    args = parser.parse_args()
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        raise SystemExit("JEV_API_KEY is not set")
    cases = json.loads(CASES.read_text())[: args.limit]
    correct = 0
    latencies: list[int] = []
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
            if got == case["expected"]:
                correct += 1
                continue
            gates = ""
            if result:
                family = result.family.choice
                detail = getattr(result, family, None) if family in ("theme", "captions", "speed", "voice") else None
                gates = (
                    f"route={result.route.choice}:{result.route.probability:.2f} "
                    f"family={family}:{result.family.probability:.2f}"
                    + (f" detail={detail.choice}:{detail.probability:.2f}" if detail else "")
                )
            print(f"MISS {case['text']!r} want={case['expected']} got={got} {gates}")
    ordered = sorted(latencies)
    print(
        f"correct {correct}/{len(cases)} latency ms: median {statistics.median(ordered):.0f} "
        f"p90 {ordered[int(len(ordered) * 0.9)]} max {ordered[-1]}"
    )


if __name__ == "__main__":
    main()
