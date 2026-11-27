"""Live Jev memory_op evaluation (manual; never run in CI, makes quota calls).

Usage: PYTHONPATH=apps/api JEV_API_KEY=... uv run python scripts/jev_memory_eval.py
The key is read only from the environment and is never printed.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import httpx
from app.domain.ui_routing import FORCED_MEMORY_THRESHOLDS, FORCED_MEMORY_TOOLS, build_ui_request, parse_ui_response
from app.services.jev import JEV_ENDPOINT

CASES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "jev_memory_live_cases.json"


def main() -> None:
    key = os.environ.get("JEV_API_KEY", "")
    if not key:
        raise SystemExit("JEV_API_KEY is not set")
    cases = json.loads(CASES.read_text())
    correct = 0
    latencies: list[int] = []
    with httpx.Client(timeout=5) as client:
        for case in cases:
            started = time.monotonic()
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
            answer = result.memory_op if result else None
            threshold = FORCED_MEMORY_THRESHOLDS.get(answer.choice) if answer else None
            fired = (
                answer.choice
                if answer
                and answer.choice in FORCED_MEMORY_TOOLS
                and threshold is not None
                and answer.probability >= threshold
                else "none"
            )
            if fired == case["expected"]:
                correct += 1
            else:
                detail = f"{answer.choice}:{answer.probability:.2f}" if answer else "ERR"
                print(f"MISS {case['text']!r} want={case['expected']} got={fired} ({detail})")
    ordered = sorted(latencies)
    print(
        f"correct {correct}/{len(cases)} latency ms: median {statistics.median(ordered):.0f} "
        f"p90 {ordered[int(len(ordered) * 0.9)]} max {ordered[-1]}"
    )


if __name__ == "__main__":
    main()
