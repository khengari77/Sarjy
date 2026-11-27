"""Which memories a reader working for one conversation may see (issue #302).

A ``global`` memory is permanent and visible everywhere. A ``conversation`` memory is temporary: it belongs to its
source conversation only. Every reader that selects, counts, walks or compares memory rows applies this predicate in
SQL, before ``ORDER BY`` / ``LIMIT`` / ``COUNT``, so a window is never filled with rows the reader may not see.
"""

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.domain.contracts import SCOPE_CONVERSATION, SCOPE_GLOBAL
from app.infrastructure.database import Memory


def visible_in(conversation_id: str | None) -> ColumnElement[bool]:
    """SQL predicate for the memories visible to a reader working for ``conversation_id``.

    Fails closed: with no conversation only global memories are visible. A caller that really means "everything the
    owner has" (the memory management panel) must say so by not calling this, never by passing ``None``.
    """
    if conversation_id is None:
        return Memory.scope == SCOPE_GLOBAL
    return or_(
        Memory.scope == SCOPE_GLOBAL,
        and_(Memory.scope == SCOPE_CONVERSATION, Memory.source_conversation_id == conversation_id),
    )


def is_visible(scope: str, source_conversation_id: str, conversation_id: str | None) -> bool:
    """The same rule for a row already in memory (tests and defensive re-checks; never a substitute for the SQL)."""
    if scope == SCOPE_GLOBAL:
        return True
    return scope == SCOPE_CONVERSATION and conversation_id is not None and source_conversation_id == conversation_id
