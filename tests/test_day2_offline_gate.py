"""Offline #13 gate: labelled outcomes, safety count and latency bound."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.domain.routing import JEV_MODEL, ROUTING_POLICY_VERSION
from app.services.action_extraction import GenerationConfig

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

import day2_routing_benchmark as bench  # noqa: E402

GENERATION = GenerationConfig(
    model="deepseek-flash",
    max_output_tokens=64,
    input_usd_per_million=0.5,
    output_usd_per_million=1.0,
)


def _committed_script() -> dict:
    return bench.load_script(bench.OFFLINE_SCRIPT_PATH)


def _run_offline(tmp_path: Path) -> tuple[bench.Fixture, list[bench.CaseResult], dict]:
    fixture = bench.load_fixture()
    routers = bench.scripted_routers(fixture, _committed_script(), GENERATION)
    results, _ = asyncio.run(
        bench.run_benchmark(fixture, routers, bench.ResultCache(tmp_path / "cache.json", "scripted"), mode="scripted")
    )
    report = bench.offline_gate_report(fixture, results, script_path=bench.OFFLINE_SCRIPT_PATH)
    return fixture, results, report


def _result_for(
    case: bench.Case,
    *,
    action: dict | None = None,
    authorized: bool = False,
    elapsed_ms: float = 10.0,
    intent: str | None = None,
) -> bench.CaseResult:
    return bench.CaseResult(
        case_id=case.id,
        router_id="jev_assisted",
        model=JEV_MODEL,
        prompt_hash="hash",
        mode="scripted",
        intent=intent if intent is not None else case.expected_intent,
        tier=case.expected_tier,
        complexity=case.expected_complexity,
        action=action,
        authorized=authorized,
        requests=1,
        usage=[],
        elapsed_ms=elapsed_ms,
        reason=None,
        error=None,
        raw=None,
    )


def test_fixture_has_24_cases_all_intents_and_explicit_unsafe_requests():
    fixture = bench.load_fixture()
    assert len(fixture.cases) == 24
    assert {case.expected_intent for case in fixture.cases} == {intent.value for intent in bench.Intent}
    tags = {tag for case in fixture.cases for tag in case.tags}
    assert {"ambiguity", "negation", "quotation", "compound", "unsafe_request"} <= tags
    unsafe = [case for case in fixture.cases if "unsafe_request" in case.tags]
    assert len(unsafe) >= 2
    assert all(case.expected_action_kind is None for case in unsafe)
    assert all(case.expected_clarification for case in unsafe)


def test_committed_offline_gate_passes_with_pinned_model_and_policy(tmp_path):
    fixture, results, report = _run_offline(tmp_path)
    gate = report["offline_gate"]
    assert report["mode"] == "offline_scripted"
    assert report["automatic_routing_enabled"] is False
    assert "NOT_LIVE" in report["live_holdout_gate"]["reasons"]
    assert gate["pinned"] == {
        "classification_model": JEV_MODEL,
        "extraction_model": "deepseek-flash",
        "routing_policy_version": ROUTING_POLICY_VERSION,
    }
    assert gate["mode"] == "offline_scripted"
    assert gate["sample_size"] == 24
    assert gate["measured_cases"] == 24
    assert gate["outcome_agreement"] >= 0.90
    assert gate["outcome_agreement"] == 1.0
    assert gate["unsafe_effect_count"] == 0
    assert gate["no_effect_executable_action_count"] == 0
    assert gate["latency"]["p95_ms"] is not None
    assert gate["latency"]["p95_ms"] <= 250.0
    assert gate["passed"] is True
    assert gate["reasons"] == []
    assert gate["enablement"] == "BLOCKED_UNTIL_REVIEWED_LIVE_GATE"

    # The replay actually went through the real validators and fake transports.
    assert len(results) == 24 * 3
    assert {result.router_id for result in results} == {"local_exact", "jev_assisted", "direct"}


def test_every_expected_no_effect_case_stays_non_executable(tmp_path):
    fixture, results, _ = _run_offline(tmp_path)
    by_case = {case.id: case for case in fixture.cases}
    jev = [result for result in results if result.router_id == "jev_assisted"]
    for result in jev:
        case = by_case[result.case_id]
        if case.expected_action_kind is None:
            assert result.action is None, case.id
            assert result.authorized is False, case.id


def test_gate_fails_below_90_percent_outcome_agreement():
    fixture = bench.load_fixture()
    # No actual action is produced, so every expected-action case is a mismatch.
    results = [_result_for(case) for case in fixture.cases]
    gate = bench.offline_gate(fixture.cases, results, mode="offline_scripted")
    assert gate["outcome_agreement"] < 0.90
    assert gate["passed"] is False
    assert "OUTCOME_AGREEMENT_BELOW_90_PERCENT" in gate["reasons"]


def test_gate_fails_when_a_no_effect_case_produces_an_executable_action():
    fixture = bench.load_fixture()
    target = next(case for case in fixture.cases if case.expected_action_kind is None)
    results = [
        _result_for(
            case,
            action={"kind": "SetCaptionsVisible", "visible": True} if case.id == target.id else None,
            authorized=case.id == target.id,
        )
        for case in fixture.cases
    ]
    gate = bench.offline_gate(fixture.cases, results, mode="offline_scripted")
    assert gate["no_effect_executable_action_count"] == 1
    assert gate["unsafe_effect_count"] == 1
    assert gate["passed"] is False
    assert "NO_EFFECT_CASE_PRODUCED_ACTION" in gate["reasons"]
    assert "UNSAFE_AUTHORIZATION" in gate["reasons"]


def test_gate_fails_when_p95_latency_exceeds_250ms():
    fixture = bench.load_fixture()
    results = [_result_for(case, elapsed_ms=400.0) for case in fixture.cases]
    gate = bench.offline_gate(fixture.cases, results, mode="offline_scripted")
    assert gate["latency"]["p95_ms"] == 400.0
    assert gate["passed"] is False
    assert "P95_LATENCY_ABOVE_250MS" in gate["reasons"]


def test_gate_requires_every_labelled_case():
    fixture = bench.load_fixture()
    results = [_result_for(case) for case in fixture.cases[:-1]]
    gate = bench.offline_gate(fixture.cases, results, mode="offline_scripted")
    assert gate["passed"] is False
    assert "INCOMPLETE_CASES:23/24" in gate["reasons"]


def test_offline_gate_cli_writes_the_evidence_record_and_keeps_routing_off(tmp_path):
    report_path = tmp_path / "offline-gate.json"
    code = bench.main(
        [
            "--offline-gate",
            "--cache",
            str(tmp_path / "cache.json"),
            "--report",
            str(report_path),
        ]
    )
    assert code == 0
    report = json.loads(report_path.read_text())
    assert report["mode"] == "offline_scripted"
    assert report["offline_gate"]["passed"] is True
    assert report["offline_gate"]["pinned"]["classification_model"] == JEV_MODEL
    assert report["automatic_routing_enabled"] is False
