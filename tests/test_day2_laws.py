from app.domain.context import ContextCandidate, compile_turn_context
from app.domain.context_operations import MemoryCandidate, exclusion_diff, restore_diff, select_candidates
from hypothesis import given
from hypothesis import strategies as st


def _history(ids):
    return tuple(ContextCandidate(key, "user", "Fact", "history") for key in sorted(ids))


@given(st.sets(st.uuids().map(str), max_size=20), st.integers(min_value=0, max_value=1000))
def test_exclusion_is_idempotent_and_undo_restores_visible_set(ids, revision):
    history = _history(ids)
    before = compile_turn_context(revision=revision, instructions="Rules", history=history)
    excluded = frozenset(ids)
    hidden = compile_turn_context(revision=revision + 1, instructions="Rules", history=history, excluded_ids=excluded)
    repeated = compile_turn_context(
        revision=revision + 1, instructions="Rules", history=history, excluded_ids=excluded | excluded
    )
    assert hidden == repeated
    assert hidden.included_ids == ("system",)
    restored = compile_turn_context(revision=revision + 2, instructions="Rules", history=history)
    assert restored.messages == before.messages
    removed = exclusion_diff(excluded, frozenset(), revision, revision + 1)
    added = restore_diff(excluded, frozenset(), revision + 1, revision + 2)
    assert removed["removed"] == added["added"] == sorted(ids)
    assert added["revision_after"] == removed["revision_before"] + 2


@given(st.lists(st.uuids().map(str), unique=True, max_size=20), st.data(), st.booleans())
def test_budget_records_are_only_kept_by_explicit_authority(ids, data, names_budget):
    keep = data.draw(st.sets(st.sampled_from(ids), max_size=len(ids))) if ids else set()
    candidates = tuple(MemoryCandidate(key, "Budget 500 USD" if i % 2 else "Travel plan") for i, key in enumerate(ids))
    query = "forget the budget" if names_budget else "forget everything"
    result = select_candidates(candidates + candidates, tuple(keep), query)
    budget_ids = {row.id for row in candidates if row.is_budget}
    assert keep <= result.kept_ids
    assert result.excluded_ids.isdisjoint(keep)
    assert result.excluded_ids | result.kept_ids == set(ids)
    assert len(result.matches) == len(ids)
    if names_budget:
        assert budget_ids - keep <= result.excluded_ids
        assert result.ambiguous == ()
    else:
        assert budget_ids - keep <= result.kept_ids
        assert {record.id for record in result.ambiguous} == budget_ids - keep
        assert result.requires_clarification is bool(budget_ids - keep)
