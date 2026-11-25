"""Undo decisions: restore exactly the journalled state, or refuse and change nothing."""

from app.domain.memory_changes import MemoryChange, Revert, RowState, revert_decision
from hypothesis import assume, given
from hypothesis import strategies as st

STATUSES = st.sampled_from(["active", "superseded", "hidden", "deleted", ""])
NON_ACTIVE = st.sampled_from(["superseded", "hidden", "deleted", "", "ACTIVE"])
NON_SUPERSEDED = st.sampled_from(["active", "hidden", "deleted", "", "SUPERSEDED"])
ROWS = st.builds(RowState, status=STATUSES, version=st.integers())
MAYBE_ROWS = st.one_of(st.none(), ROWS)
CHANGES = st.builds(
    MemoryChange,
    memory_id=st.text(),
    version=st.integers(),
    superseded_id=st.one_of(st.none(), st.text()),
    superseded_version=st.one_of(st.none(), st.integers()),
)
REPLACEMENTS = st.builds(
    MemoryChange,
    memory_id=st.text(),
    version=st.integers(),
    superseded_id=st.text(),
    superseded_version=st.integers(),
)


@given(CHANGES, MAYBE_ROWS, MAYBE_ROWS)
def test_undone_always_reports_already_undone(change, new, old):
    assert revert_decision(change, new, old, undone=True) is Revert.ALREADY_UNDONE


@given(CHANGES, MAYBE_ROWS)
def test_a_missing_new_row_conflicts(change, old):
    assert revert_decision(change, None, old, undone=False) is Revert.CONFLICT


@given(CHANGES, NON_ACTIVE, st.integers(), MAYBE_ROWS)
def test_a_new_row_that_is_not_active_conflicts(change, status, version, old):
    assert revert_decision(change, RowState(status, version), old, undone=False) is Revert.CONFLICT


@given(CHANGES, st.integers(), MAYBE_ROWS)
def test_a_new_row_at_a_different_version_conflicts(change, version, old):
    assume(version != change.version)
    assert revert_decision(change, RowState("active", version), old, undone=False) is Revert.CONFLICT


@given(REPLACEMENTS)
def test_a_missing_replaced_row_conflicts(change):
    new = RowState("active", change.version)
    assert revert_decision(change, new, None, undone=False) is Revert.CONFLICT


@given(REPLACEMENTS, NON_SUPERSEDED, st.integers())
def test_a_replaced_row_that_is_not_superseded_conflicts(change, status, version):
    new = RowState("active", change.version)
    assert revert_decision(change, new, RowState(status, version), undone=False) is Revert.CONFLICT


@given(REPLACEMENTS, st.integers())
def test_a_replaced_row_at_a_different_version_conflicts(change, version):
    assume(version != change.superseded_version)
    new = RowState("active", change.version)
    assert revert_decision(change, new, RowState("superseded", version), undone=False) is Revert.CONFLICT


@given(REPLACEMENTS)
def test_an_exactly_matching_replacement_applies(change):
    new = RowState("active", change.version)
    old = RowState("superseded", change.superseded_version)
    assert revert_decision(change, new, old, undone=False) is Revert.APPLY


@given(
    st.builds(
        MemoryChange,
        memory_id=st.text(),
        version=st.integers(),
        superseded_version=st.one_of(st.none(), st.integers()),
    ),
    MAYBE_ROWS,
)
def test_without_a_replaced_row_the_old_state_is_ignored(change, old):
    assert change.superseded_id is None
    assert revert_decision(change, RowState("active", change.version), old, undone=False) is Revert.APPLY


def test_plain_save_examples():
    change = MemoryChange(memory_id="memory", version=1)
    assert revert_decision(change, RowState("active", 1), None, undone=False) is Revert.APPLY
    # No earlier fact was replaced, so whatever the caller passes as the old row cannot block undo.
    assert revert_decision(change, RowState("active", 1), RowState("hidden", 9), undone=False) is Revert.APPLY
    assert revert_decision(change, None, None, undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("hidden", 1), None, undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("deleted", 1), None, undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("active", 2), None, undone=False) is Revert.CONFLICT


def test_replacement_examples():
    change = MemoryChange(memory_id="new", version=2, superseded_id="old", superseded_version=1)
    assert revert_decision(change, RowState("active", 2), RowState("superseded", 1), undone=False) is Revert.APPLY
    assert revert_decision(change, RowState("active", 2), None, undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("active", 2), RowState("active", 1), undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("active", 2), RowState("superseded", 2), undone=False) is Revert.CONFLICT
    assert revert_decision(change, RowState("hidden", 2), RowState("superseded", 1), undone=False) is Revert.CONFLICT


def test_undone_takes_precedence_over_every_row_state():
    change = MemoryChange(memory_id="new", version=2, superseded_id="old", superseded_version=1)
    assert revert_decision(change, None, None, undone=True) is Revert.ALREADY_UNDONE
    assert (
        revert_decision(change, RowState("deleted", 99), RowState("hidden", 99), undone=True) is Revert.ALREADY_UNDONE
    )
