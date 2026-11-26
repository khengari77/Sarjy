"""One per-owner lock that serializes erasing a memory with committing a memory write.

A delete tombstones the fact and everything derived from it; a write (a confirmed capture, a correction, a direct
save) inserts rows. Interleaved, the write can commit the erased text after the delete has returned. Both take this
lock first in their transaction, so on PostgreSQL they run one after the other and each sees the other's committed
state; no row-lock order has to be kept consistent between them. SQLite serializes writers already.
"""

from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_owner_memories(db: AsyncSession, owner_id: str) -> None:
    """Wait for, then hold until the transaction ends, this owner's memory erasure/write lock."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    key = int.from_bytes(sha256(f"memory-erasure:{owner_id}".encode()).digest()[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
