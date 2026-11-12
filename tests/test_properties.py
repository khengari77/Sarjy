from hypothesis import given, strategies as st
from app.domain.context import ContextRecord, compile_context
from app.services.weather import select_location, WeatherError
import pytest


@given(st.lists(st.text(min_size=1), unique=True, max_size=30), st.sets(st.text(min_size=1), max_size=30))
def test_visibility_and_compilation_are_pure(ids, excluded):
    records = tuple(ContextRecord(id, "user", f"Fact {id}") for id in ids)
    before = tuple(records)
    result = compile_context(records, 0, frozenset(excluded))
    assert set(result["included_record_ids"]).isdisjoint(excluded)
    assert records == before
    assert result == compile_context(records, 0, frozenset(excluded))


def test_ambiguous_location_requires_clarification():
    with pytest.raises(WeatherError, match="AMBIGUOUS_LOCATION"):
        select_location([{"name": "Tripoli", "country_code": "LY"}, {"name": "Tripoli", "country_code": "LB"}])
    assert select_location([{"country_code": "LY"}, {"country_code": "LB"}], "LY") == {"country_code": "LY"}


@given(st.lists(st.text(max_size=100), max_size=30), st.integers(min_value=0, max_value=2000))
def test_budget_preserves_protected_rules_and_newest_fitting_input(texts, budget):
    records = (ContextRecord("rules", "system", "Protected"),) + tuple(
        ContextRecord(str(i), "user", text) for i, text in enumerate(texts)
    )
    result = compile_context(records, 0, max_chars=budget)
    assert result["included_record_ids"][0] == "rules"
    assert sum(len(m["content"]) for m in result["messages"]) <= max(budget, len("Protected"))


from app.domain.sessions import admission_error


@given(st.tuples(st.text(), st.text()), st.text())
def test_full_call_capacity_always_rejects(active, owner):
    assert admission_error(active, owner, 0, 0) == "CALL_LIMIT_REACHED"


@given(st.integers(min_value=80), st.integers(min_value=0))
def test_exhausted_month_budget_cannot_start_a_call(month_calls, identity_calls):
    assert admission_error((), "owner", month_calls, identity_calls) == "DEMO_BUDGET_REACHED"
