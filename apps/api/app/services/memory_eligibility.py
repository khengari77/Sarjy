"""One conversation-scoped exclusion policy for memory and its tracked descendants."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import ContextExclusion, Memory


def _blocked_query(owner_id: str, conversation_id: str):
    # Hidden and deleted memories are blocked in every conversation (memory-level),
    # so derived history and source turns are dropped exactly like an exclusion.
    seed = (
        select(Memory.id.label("id"))
        .outerjoin(
            ContextExclusion,
            and_(
                ContextExclusion.memory_id == Memory.id,
                ContextExclusion.owner_id == owner_id,
                ContextExclusion.conversation_id == conversation_id,
                ContextExclusion.active.is_(True),
            ),
        )
        .where(
            Memory.owner_id == owner_id,
            or_(ContextExclusion.id.is_not(None), Memory.status.in_(("hidden", "deleted"))),
        )
    )
    # Pre-0006 consumers can use exact-ID exclusions while integrating the schema.
    # Full correction-lineage acceptance requires the migrated Memory model.
    supersedes = getattr(Memory, "supersedes_id", None)
    if supersedes is None:
        return seed
    blocked = seed.cte("blocked_memory_lineage", recursive=True)
    # UNION removes duplicates and terminates even for malformed cyclic legacy data.
    blocked = blocked.union(
        select(Memory.id).join(blocked, supersedes == blocked.c.id).where(Memory.owner_id == owner_id)
    )
    return select(blocked.c.id)


async def blocked_memory_ids(db: AsyncSession, owner_id: str, conversation_id: str) -> frozenset[str]:
    return frozenset(await db.scalars(_blocked_query(owner_id, conversation_id)))


async def blocked_source_turn_ids(db: AsyncSession, owner_id: str, conversation_id: str) -> frozenset[str]:
    return frozenset(
        await db.scalars(
            select(Memory.source_turn_id).where(
                Memory.owner_id == owner_id,
                Memory.id.in_(_blocked_query(owner_id, conversation_id)),
                Memory.source_turn_id.is_not(None),
            )
        )
    )


def blocked_memory_ids_select(owner_id: str, conversation_id: str):
    """The blocked memory ids as a select, for callers that combine reads."""
    return _blocked_query(owner_id, conversation_id)


async def blocked_memory_rows(db: AsyncSession, owner_id: str, conversation_id: str) -> list[Memory]:
    """The blocked memories themselves, in one statement: ids, source turns and policy evidence together."""
    return list(
        await db.scalars(
            select(Memory).where(
                Memory.owner_id == owner_id, Memory.id.in_(blocked_memory_ids_select(owner_id, conversation_id))
            )
        )
    )
