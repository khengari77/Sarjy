"""Normalised lexical terms for uncapped memory recall and the SQL index that backs it.

``terms_of`` is the one tokenizer used by the write path, the migration backfill and the query, so an accented or
diacritised spelling finds the same fact. Terms are derived copies of memory content: every path that blanks or
tombstones content must also delete the matching ``memory_terms`` rows (see ``delete_terms``).
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context_operations import MemoryCandidate, classify
from app.infrastructure.database import Memory, MemoryTerm
from app.services.memory_eligibility import blocked_memory_ids_select
from app.services.memory_scope import visible_in

TERM_MAX_CHARS = 64

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "about",
        "all",
        "anything",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "chat",
        "conversation",
        "do",
        "does",
        "everything",
        "for",
        "from",
        "get",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "remember",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "your",
    ]
)

# Words that point at something without naming it; a target made of these (and stopwords) is no target.
REFERENTIAL = frozenset(
    {
        "them",
        "they",
        "us",
        "we",
        "our",
        "his",
        "her",
        "him",
        "their",
        "those",
        "these",
        "stuff",
        "things",
        "thing",
        "something",
        "anything",
        "nothing",
        "everything",
        "one",
        "ones",
    }
)

# Arabic harakat (combining marks), superscript alef and tatweel.  NFKD already splits most of these from their
# base letter, but the explicit set keeps a composed input stable across Unicode versions.
_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u0640]")
# Letters and digits, never the underscore: "user_name" is two terms.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def terms_of(text: str) -> frozenset[str]:
    """The searchable terms of ``text``: NFKC/casefolded, accent- and diacritic-free, stopwords dropped."""
    normalised = unicodedata.normalize("NFKC", text).casefold()
    normalised = unicodedata.normalize("NFKD", normalised)
    # Dropping every combining mark removes Latin accents ("café" -> "cafe") and Arabic harakat alike.
    normalised = "".join(character for character in normalised if not unicodedata.combining(character))
    normalised = _ARABIC_MARKS.sub("", normalised)
    return frozenset(token[:TERM_MAX_CHARS] for token in _TOKEN.findall(normalised) if token not in STOPWORDS)


async def write_terms(db: AsyncSession, *, memory_id: str, owner_id: str, content: str) -> None:
    """Index one memory's terms in the caller's transaction (called beside the memory insert)."""
    rows = [{"memory_id": memory_id, "owner_id": owner_id, "term": term} for term in sorted(terms_of(content))]
    if rows:
        await db.execute(insert(MemoryTerm), rows)


async def delete_terms(db: AsyncSession, memory_ids: Iterable[str]) -> None:
    """Remove the derived terms of memories whose content is being blanked or tombstoned."""
    ids = list(dict.fromkeys(memory_ids))
    if ids:
        await db.execute(delete(MemoryTerm).where(MemoryTerm.memory_id.in_(ids)))


async def search_all_memories(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    query: str,
) -> Sequence[MemoryCandidate]:
    """Every active, visible memory matching any query term, best overlap first, with no row window.

    The exclusions mirror ``keyword_search``: active only, the conversation visibility predicate, and the
    correction-lineage/exclusion policy computed in SQL before grouping, so a window can never hide a match.
    """
    wanted = terms_of(query) - REFERENTIAL
    if len(query.strip()) != 1:
        # A single character is a search term only when it is the whole query ("X"); next to other words it would
        # elect unrelated memories ("vitamin D" must not match "got a D in math").
        wanted = {term for term in wanted if len(term) > 1}
    if not wanted:
        # A query that names nothing matches nothing (the recall tool decides whether to browse instead).
        return ()
    score = func.count(func.distinct(MemoryTerm.term)).label("score")
    rows = (
        await db.execute(
            select(Memory, score)
            .join(MemoryTerm, (MemoryTerm.memory_id == Memory.id) & (MemoryTerm.owner_id == Memory.owner_id))
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                visible_in(conversation_id),
                Memory.id.not_in(blocked_memory_ids_select(owner_id, conversation_id)),
                MemoryTerm.term.in_(wanted),
            )
            .group_by(Memory.id)
            .order_by(score.desc(), Memory.updated_at.desc(), Memory.id)
        )
    ).all()
    return tuple(
        MemoryCandidate(
            id=memory.id,
            content=memory.content,
            source_turn_id=memory.source_turn_id,
            score=float(matched),
            kind=classify(memory.content),
        )
        for memory, matched in rows
    )
