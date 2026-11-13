import pytest
from app.domain.context import ContextCandidate, compile_turn_context
from app.domain.provider_bounds import provider_request_bytes
from app.domain.sessions import admission_error
from app.services.weather import WeatherError, select_location
from hypothesis import given
from hypothesis import strategies as st


@given(
    st.lists(st.uuids().map(str), unique=True, max_size=30),
    st.sets(st.uuids().map(str), max_size=30),
)
def test_visibility_and_compilation_are_pure(ids, excluded):
    history = tuple(ContextCandidate(id, "user", f"Fact {id}", "history") for id in ids)
    before = tuple(history)
    result = compile_turn_context(revision=0, instructions="Rules", history=history, excluded_ids=frozenset(excluded))
    assert (set(result.included_ids) - {"system"}).isdisjoint(excluded)
    assert history == before
    assert result == compile_turn_context(
        revision=0, instructions="Rules", history=history, excluded_ids=frozenset(excluded)
    )


def test_ambiguous_location_requires_clarification():
    with pytest.raises(WeatherError, match="AMBIGUOUS_LOCATION"):
        select_location([{"name": "Tripoli", "country_code": "LY"}, {"name": "Tripoli", "country_code": "LB"}])
    assert select_location([{"country_code": "LY"}, {"country_code": "LB"}], "LY") == {"country_code": "LY"}


@given(st.lists(st.text(max_size=100), max_size=30), st.integers(min_value=0, max_value=2000))
def test_budget_preserves_protected_rules_and_newest_fitting_input(texts, budget):
    history = tuple(ContextCandidate(str(i), "user", text, "history") for i, text in enumerate(texts))
    try:
        result = compile_turn_context(
            revision=0, instructions="Protected", history=history, max_provider_bytes=budget, reserve_bytes=0
        )
    except ValueError as error:
        assert str(error) == "CONTEXT_BUDGET_EXCEEDED"
        return
    assert result.messages[0]["content"] == "Protected"
    assert provider_request_bytes(result.messages) <= budget


@given(st.tuples(st.text(), st.text()), st.text())
def test_full_call_capacity_always_rejects(active, owner):
    assert admission_error(active, owner, 0, 0) == "CALL_LIMIT_REACHED"


@given(st.integers(min_value=80), st.integers(min_value=0))
def test_exhausted_month_budget_cannot_start_a_call(month_calls, identity_calls):
    assert admission_error((), "owner", month_calls, identity_calls) == "DEMO_BUDGET_REACHED"
