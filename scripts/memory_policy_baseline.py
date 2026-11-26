"""Offline baseline for the labelled memory-policy set.

Runs the current keyword sensitivity floor and the deterministic parser on every
fixture case, derives the auto-mode decision and reports credential recall,
false refusals, per-tier confusion and decision accuracy. It never touches the
database or the network.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.domain.memory_capture import parse_capture  # noqa: E402
from app.domain.memory_policy import Decision, Sensitivity, classify_sensitivity, decide  # noqa: E402

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "memory_policy_cases.json"
DEFAULT_OUT = ROOT / "docs" / "data" / "memory-policy-baseline.json"
Z95 = 1.959963984540054
LABELS = ("save", "ask", "skip")
LANGUAGES = ("en", "ar")
TIERS = ("normal", "third_party", "health", "finance", "credential")


@dataclass(frozen=True)
class EvaluatedCase:
    """One fixture case with the current system's sensitivity and decision."""

    label: str
    tier: str
    language: str
    category: str
    sensitivity: Sensitivity
    decision: Decision
    captured: bool = True


def wilson_interval(hits: int, total: int, z: float = Z95) -> dict[str, Any]:
    """Hits, total, rate and the two-sided Wilson 95% interval for a proportion."""
    if total <= 0:
        return {"n": 0, "hits": 0, "rate": 0.0, "low": 0.0, "high": 0.0}
    proportion = hits / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return {
        "n": total,
        "hits": hits,
        "rate": round(proportion, 4),
        "low": round(max(0.0, centre - margin), 4),
        "high": round(min(1.0, centre + margin), 4),
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Read the labelled fixture."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("cases must be a JSON list")
    cases: list[dict[str, Any]] = []
    for case in payload:
        if not isinstance(case, dict) or not isinstance(case.get("text"), str):
            raise ValueError("every case must be an object with a text string")
        cases.append(case)
    return cases


def evaluate_case(text: str) -> tuple[Sensitivity, Decision, bool]:
    """The current system's output for one utterance in auto mode, and whether the parser recognised a fact."""
    sensitivity = classify_sensitivity(text)
    capture = parse_capture(text)
    if capture is None:
        return sensitivity, Decision.SKIP, False
    return sensitivity, decide("auto", sensitivity, explicit=capture.explicit, negated=capture.negated), True


def evaluate_cases(cases: Sequence[dict[str, Any]]) -> list[EvaluatedCase]:
    """Evaluate every fixture case."""
    evaluated: list[EvaluatedCase] = []
    for case in cases:
        sensitivity, decision, captured = evaluate_case(str(case["text"]))
        evaluated.append(
            EvaluatedCase(
                label=str(case["label"]),
                tier=str(case["tier"]),
                language=str(case["language"]),
                category=str(case["category"]),
                sensitivity=sensitivity,
                decision=decision,
                captured=captured,
            )
        )
    return evaluated


def _accuracy(rows: Sequence[EvaluatedCase]) -> dict[str, Any]:
    hits = sum(1 for row in rows if row.decision.value == row.label)
    return wilson_interval(hits, len(rows))


def build_report(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build the JSON-serialisable report."""
    rows = evaluate_cases(cases)
    credentials = [row for row in rows if row.tier == "credential"]
    saves = [row for row in rows if row.label == "save"]

    credential_hits = sum(1 for row in credentials if row.sensitivity is Sensitivity.CREDENTIAL)
    # Two different failures to save a fact worth saving: the credential floor wrongly blocks it (a false
    # refusal), or the fixed-shape parser does not recognise the sentence at all (a missed save; the model
    # tools and the observer exist to cover these).
    false_refusals = sum(1 for row in saves if row.sensitivity is Sensitivity.CREDENTIAL)
    missed_saves = sum(1 for row in saves if not row.captured)

    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({row.category for row in rows}):
        by_category[category] = _accuracy([row for row in rows if row.category == category])

    by_language: dict[str, dict[str, Any]] = {}
    for language in LANGUAGES:
        by_language[language] = _accuracy([row for row in rows if row.language == language])

    credential_by_language: dict[str, dict[str, Any]] = {}
    for language in LANGUAGES:
        language_credentials = [row for row in credentials if row.language == language]
        hits = sum(1 for row in language_credentials if row.sensitivity is Sensitivity.CREDENTIAL)
        credential_by_language[language] = wilson_interval(hits, len(language_credentials))

    confusion: dict[str, dict[str, int]] = {}
    for tier in TIERS:
        counts = Counter(row.sensitivity.value for row in rows if row.tier == tier)
        confusion[tier] = {sensitivity: counts.get(sensitivity, 0) for sensitivity in TIERS}

    return {
        "schema_version": 1,
        "generated_by": "scripts/memory_policy_baseline.py",
        "case_count": len(rows),
        "label_counts": dict(Counter(row.label for row in rows)),
        "language_counts": dict(Counter(row.language for row in rows)),
        "credential_recall": wilson_interval(credential_hits, len(credentials)),
        "credential_recall_by_language": credential_by_language,
        "false_refusal": wilson_interval(false_refusals, len(saves)),
        "missed_save": wilson_interval(missed_saves, len(saves)),
        "decision_accuracy": {
            "overall": _accuracy(rows),
            "by_category": by_category,
            "by_language": by_language,
        },
        "tier_confusion": confusion,
    }


def _metric_line(name: str, metric: dict[str, Any]) -> str:
    return (
        f"{name:<36} {metric['n']:>4} {metric['hits']:>5} {metric['rate']:>8.1%}"
        f"  {metric['low']:>6.1%} - {metric['high']:>6.1%}"
    )


def format_table(report: dict[str, Any]) -> str:
    """A plain-text table for the terminal."""
    labels = report["label_counts"]
    lines = [
        "Memory policy baseline: current keyword floor + parser, auto mode",
        f"cases: {report['case_count']}  save: {labels.get('save', 0)}  "
        f"ask: {labels.get('ask', 0)}  skip: {labels.get('skip', 0)}",
        "",
        f"{'metric':<36} {'n':>4} {'hits':>5} {'rate':>8}  {'95% Wilson':>15}",
        _metric_line("credential recall (tier)", report["credential_recall"]),
        _metric_line("false refusal (floor blocks a save)", report["false_refusal"]),
        _metric_line("missed save (parser finds no fact)", report["missed_save"]),
        _metric_line("overall decision accuracy", report["decision_accuracy"]["overall"]),
        "",
        "Credential recall by language",
    ]
    for language, metric in report["credential_recall_by_language"].items():
        lines.append(_metric_line(f"  {language}", metric))
    lines.extend(["", "Decision accuracy by category"])
    for category, metric in report["decision_accuracy"]["by_category"].items():
        lines.append(_metric_line(f"  {category}", metric))
    lines.extend(["", "Decision accuracy by language"])
    for language, metric in report["decision_accuracy"]["by_language"].items():
        lines.append(_metric_line(f"  {language}", metric))
    lines.extend(["", "Tier confusion (true tier -> predicted sensitivity)"])
    header = f"{'tier':<14}" + "".join(f"{sensitivity:>13}" for sensitivity in TIERS)
    lines.append(header)
    for tier, counts in report["tier_confusion"].items():
        lines.append(f"{tier:<14}" + "".join(f"{counts[sensitivity]:>13}" for sensitivity in TIERS))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help=f"labelled cases (default: {DEFAULT_CASES})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"JSON report (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        report = build_report(cases)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"memory_policy_baseline: {exc}", file=sys.stderr)
        return 2
    print(format_table(report))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
