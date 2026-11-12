"""One conversation-scoped exclusion policy for memory and its tracked descendants."""

from app.infrastructure.database import ContextExclusion, Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _blocked_query(owner_id: str, conversation_id: str):
    seed = (
        select(Memory.id.label("id"))
        .join(ContextExclusion, ContextExclusion.memory_id == Memory.id)
        .where(
            Memory.owner_id == owner_id,
            ContextExclusion.owner_id == owner_id,
            ContextExclusion.conversation_id == conversation_id,
            ContextExclusion.active.is_(True),
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
