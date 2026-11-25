"""Pure rules for undoing an auto-saved memory change.

No I/O. A change is journalled when a fact is saved (or replaces an earlier one) without a spoken question. Undo must
restore exactly the state before that change, and must refuse when anything touched the rows since: a stale undo that
silently clobbers a newer edit is worse than no undo.
"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class MemoryChange:
    """What the journal recorded when the change was committed."""

    memory_id: str
    version: int
    superseded_id: str | None = None
    # Version of the replaced row *after* the change superseded it.
    superseded_version: int | None = None


@dataclass(frozen=True)
class RowState:
    status: str
    version: int


class Revert(StrEnum):
    APPLY = "apply"
    ALREADY_UNDONE = "already_undone"
    CONFLICT = "conflict"


def revert_decision(change: MemoryChange, new: RowState | None, old: RowState | None, *, undone: bool) -> Revert:
    """Whether the change can be undone now.

    * Already undone: nothing to do, and repeating the request is harmless.
    * The new row must still be exactly as the change left it (active, same version). If the user hid, edited or
      deleted it since, undo refuses.
    * If the change replaced an earlier fact, that row must still be exactly as the change left it (superseded, same
      version); otherwise restoring it would resurrect something that moved on.
    """
    if undone:
        return Revert.ALREADY_UNDONE
    if new is None or new.status != "active" or new.version != change.version:
        return Revert.CONFLICT
    replaced_moved_on = old is None or old.status != "superseded" or old.version != change.superseded_version
    if change.superseded_id is not None and replaced_moved_on:
        return Revert.CONFLICT
    return Revert.APPLY
