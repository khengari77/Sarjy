"""The labelled memory-policy set must be complete, honest and self-consistent."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "memory_policy_cases.json"
sys.path.insert(0, str(ROOT / "scripts"))

import memory_policy_baseline  # noqa: E402

CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))
FIELDS = {"text", "label", "tier", "negated", "language", "category", "note"}
LABELS = {"save", "ask", "skip"}
TIERS = {"normal", "third_party", "health", "finance", "credential"}
LANGUAGES = {"en", "ar"}
CATEGORY_MINIMUMS = {
    "ordinary": 40,
    "credential": 30,
    "health": 25,
    "finance": 20,
    "third_party": 20,
    "negation": 25,
    "false_refusal": 20,
    "not_fact": 25,
    "arabic": 30,
}


def test_at_least_240_cases():
    assert len(CASES) >= 240


def test_every_case_has_the_full_schema_with_valid_enums():
    for case in CASES:
        assert set(case) == FIELDS, case.get("text")
        assert isinstance(case["text"], str) and case["text"].strip()
        assert len(case["text"]) <= 160, case["text"]
        assert isinstance(case["note"], str) and case["note"].strip(), case["text"]
        assert isinstance(case["category"], str) and case["category"].strip(), case["text"]
        assert case["label"] in LABELS, case["text"]
        assert case["tier"] in TIERS, case["text"]
        assert case["language"] in LANGUAGES, case["text"]
        assert isinstance(case["negated"], bool), case["text"]


def test_texts_are_unique_casefolded():
    duplicates = [text for text, count in Counter(case["text"].casefold() for case in CASES).items() if count > 1]
    assert duplicates == []


def test_minimum_counts_per_category():
    counts = Counter(case["category"] for case in CASES)
    missing = {
        category: {"have": counts[category], "need": minimum}
        for category, minimum in CATEGORY_MINIMUMS.items()
        if counts[category] < minimum
    }
    assert missing == {}


def test_ordinary_facts_are_normal_saves():
    ordinary = [case for case in CASES if case["category"] == "ordinary"]
    assert len(ordinary) >= CATEGORY_MINIMUMS["ordinary"]
    for case in ordinary:
        assert case["label"] == "save", case["text"]
        assert case["tier"] == "normal", case["text"]
        assert case["negated"] is False, case["text"]


def test_arabic_cases_contain_arabic_letters():
    arabic = [case for case in CASES if case["language"] == "ar"]
    assert len(arabic) >= 42
    for case in arabic:
        assert any("\u0600" <= char <= "\u06ff" for char in case["text"]), case["text"]


def test_arabic_ordinary_and_sensitive_facts_are_labelled_for_consent():
    arabic = [case for case in CASES if case["category"] == "arabic"]
    assert len(arabic) >= CATEGORY_MINIMUMS["arabic"]
    for case in arabic:
        assert case["language"] == "ar", case["text"]
        assert case["label"] in {"save", "ask"}, case["text"]
        if case["label"] == "save":
            assert case["tier"] == "normal", case["text"]


def test_credentials_are_skipped_credentials():
    credentials = [case for case in CASES if case["category"] == "credential"]
    assert len(credentials) >= CATEGORY_MINIMUMS["credential"]
    assert sum(1 for case in credentials if case["language"] == "ar") >= 12
    for case in credentials:
        assert case["tier"] == "credential", case["text"]
        assert case["label"] == "skip", case["text"]
        assert case["negated"] is False, case["text"]


def test_sensitive_facts_ask_for_consent():
    for category, tier in (("health", "health"), ("finance", "finance"), ("third_party", "third_party")):
        cases = [case for case in CASES if case["category"] == category]
        assert len(cases) >= CATEGORY_MINIMUMS[category]
        for case in cases:
            assert case["label"] == "ask", case["text"]
            assert case["tier"] == tier, case["text"]


def test_negations_and_updates_are_flagged_and_asked_about():
    negations = [case for case in CASES if case["category"] == "negation"]
    assert len(negations) >= CATEGORY_MINIMUMS["negation"]
    for case in negations:
        assert case["negated"] is True, case["text"]
        assert case["label"] == "ask", case["text"]


def test_false_refusals_are_normal_saves():
    traps = [case for case in CASES if case["category"] == "false_refusal"]
    assert len(traps) >= CATEGORY_MINIMUMS["false_refusal"]
    for case in traps:
        assert case["label"] == "save", case["text"]
        assert case["tier"] == "normal", case["text"]


def test_not_facts_are_skipped():
    not_facts = [case for case in CASES if case["category"] == "not_fact"]
    assert len(not_facts) >= CATEGORY_MINIMUMS["not_fact"]
    for case in not_facts:
        assert case["label"] == "skip", case["text"]


def test_the_baseline_runs_offline_and_reports_the_expected_keys(tmp_path):
    out = tmp_path / "memory-policy-baseline.json"
    assert memory_policy_baseline.main(["--out", str(out)]) == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["case_count"] == len(CASES)
    assert set(report) >= {
        "schema_version",
        "generated_by",
        "case_count",
        "credential_recall",
        "false_refusal",
        "tier_confusion",
        "decision_accuracy",
    }
    for metric in (
        report["credential_recall"],
        report["false_refusal"],
        report["missed_save"],
        report["decision_accuracy"]["overall"],
    ):
        assert set(metric) == {"n", "hits", "rate", "low", "high"}
        assert metric["low"] <= metric["rate"] <= metric["high"]
    assert set(report["tier_confusion"]) == TIERS
    for row in report["tier_confusion"].values():
        assert set(row) == TIERS
    assert set(report["decision_accuracy"]["by_language"]) == LANGUAGES
    assert set(report["decision_accuracy"]["by_category"]) == {case["category"] for case in CASES}
