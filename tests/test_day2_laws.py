from app.domain.context import ContextRecord, compile_context
from app.domain.context_operations import MemoryCandidate, exclusion_diff, restore_diff, select_candidates
from hypothesis import given
from hypothesis import strategies as st


@given(st.sets(st.uuids().map(str), max_size=20), st.integers(min_value=0, max_value=1000))
def test_exclusion_is_idempotent_and_undo_restores_visible_set(ids, revision):
    records = tuple(ContextRecord(key, 'user', 'Fact') for key in sorted(ids))
    before = compile_context(records, revision)
    excluded = frozenset(ids)
    hidden = compile_context(records, revision + 1, excluded)
    repeated = compile_context(records, revision + 1, excluded | excluded)
    assert hidden == repeated
    assert not hidden['included_record_ids']
    restored = compile_context(records, revision + 2)
    assert restored['messages'] == before['messages']
    removed = exclusion_diff(excluded, frozenset(), revision, revision + 1)
    added = restore_diff(excluded, frozenset(), revision + 1, revision + 2)
    assert removed['removed'] == added['added'] == sorted(ids)
    assert added['revision_after'] == removed['revision_before'] + 2


@given(st.lists(st.uuids().map(str), unique=True, max_size=20), st.data())
def test_protected_and_explicitly_kept_records_never_enter_exclusion(ids, data):
    keep = data.draw(st.sets(st.sampled_from(ids), max_size=len(ids))) if ids else set()
    candidates = tuple(MemoryCandidate(key, 'Budget 500 USD' if i % 2 else 'Travel plan') for i, key in enumerate(ids))
    result = select_candidates(candidates + candidates, tuple(keep))
    protected = {row.id for row in candidates if row.is_budget} | keep
    assert protected <= result.kept_ids
    assert result.excluded_ids.isdisjoint(protected)
    assert result.excluded_ids | result.kept_ids == set(ids)
    assert len(result.matches) == len(ids)
