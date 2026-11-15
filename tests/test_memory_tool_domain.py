"""Deterministic validation of model-supplied memory arguments."""

import pytest
from app.domain.memory_capture import (
    MAX_TOOL_FACT_CHARS,
    MAX_TOOL_QUERY_CHARS,
    clean_tool_query,
    parse_tool_fact,
)
from app.domain.policy import BASE_INSTRUCTIONS
from hypothesis import given
from hypothesis import strategies as st

ACCEPTED = [
    ("I live in Benghazi", "location"),
    ("I work at Acme", "work"),
    ("My favourite colour is green", "attribute:favourite colour"),
    ("I am allergic to peanuts", "diet:peanuts"),
    ("I prefer green tea", "preference:beverage"),
    ("Remember that my sister is called Huda", None),
    ("The user has two cats", None),
]
REJECTED = [
    "",
    "   ",
    "it",
    "that",
    "something",
    "I live in it",
    "I prefer that",
    "If I moved to Rome I would live in Rome",
    "Maybe I like jazz",
    '"I live in Rome"',
    "Do I live in Rome?",
    "never mind that",
    "x" * (MAX_TOOL_FACT_CHARS + 1),
]


@pytest.mark.parametrize(("fact", "subject"), ACCEPTED)
def test_valid_facts_become_explicit_candidates_with_a_stable_subject(fact, subject):
    capture = parse_tool_fact(fact)
    assert capture is not None and capture.explicit
    assert capture.subject and (subject is None or capture.subject.startswith(subject) or capture.subject == subject)


@pytest.mark.parametrize("fact", REJECTED)
def test_invalid_facts_are_refused(fact):
    assert parse_tool_fact(fact) is None


def test_control_characters_are_stripped_from_the_stored_text():
    capture = parse_tool_fact("I live\x00 in\nBenghazi‮")
    assert capture is not None and capture.text == "I live in Benghazi"


@given(st.text(max_size=1500))
def test_any_fact_is_bounded_printable_and_never_raises(fact):
    capture = parse_tool_fact(fact)
    if capture is not None:
        assert 0 < len(capture.text) <= MAX_TOOL_FACT_CHARS
        assert capture.text.isprintable() and capture.subject and capture.explicit


@given(
    st.sampled_from(["it", "this", "that", "them", "something"]),
    st.sampled_from(["I live in", "I work at", "I prefer"]),
)
def test_pronoun_only_values_never_become_facts(pronoun, lead):
    assert parse_tool_fact(f"{lead} {pronoun}") is None


@given(st.text(max_size=600))
def test_queries_are_bounded_and_sanitised(query):
    cleaned = clean_tool_query(query)
    if cleaned is not None:
        assert 0 < len(cleaned) <= MAX_TOOL_QUERY_CHARS and cleaned.isprintable()


def test_instructions_name_each_tool_and_forbid_premature_claims():
    for language in ("en", "ar"):
        text = BASE_INSTRUCTIONS[language]
        assert "propose_memory" in text and "propose_forget" in text and "recall" in text
    assert "before the user confirms" in BASE_INSTRUCTIONS["en"]
