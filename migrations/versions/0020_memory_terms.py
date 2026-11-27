"""Lexical memory terms index and backfill (#317)."""

import sqlalchemy as sa
from alembic import context, op

revision = "0020_memory_terms"
down_revision = "0019_conversation_memory_mode"

BACKFILL_BATCH = 500


def _backfill():
    if context.is_offline_mode():
        # Offline SQL generation (used to migrate the hosted database) cannot run Python. The operator backfills the
        # existing rows with the same tokenizer after applying this DDL; see the PR notes for #317.
        return
    # The same Python tokenizer as the write path and the query: SQL lower()/accent handling is not portable.
    from app.services.memory_terms import terms_of

    connection = op.get_bind()
    memories = sa.table(
        "memories",
        sa.column("id", sa.String),
        sa.column("owner_id", sa.String),
        sa.column("content", sa.Text),
        sa.column("status", sa.String),
    )
    terms = sa.table(
        "memory_terms",
        sa.column("memory_id", sa.String),
        sa.column("term", sa.String),
        sa.column("owner_id", sa.String),
    )
    last_id = None
    while True:
        # Read one page fully before inserting: streaming a cursor while writing on the same connection is not safe
        # on every driver.
        query = (
            sa.select(memories.c.id, memories.c.owner_id, memories.c.content)
            .where(memories.c.status.in_(("active", "hidden", "superseded")))
            .order_by(memories.c.id)
            .limit(BACKFILL_BATCH)
        )
        if last_id is not None:
            query = query.where(memories.c.id > last_id)
        batch = connection.execute(query).fetchall()
        if not batch:
            break
        last_id = batch[-1].id
        # Re-check inside the transaction: a memory read before a concurrent erase may now be deleted.
        memory_ids = [memory.id for memory in batch]
        live = set(
            connection.execute(
                sa.select(memories.c.id).where(memories.c.id.in_(memory_ids), memories.c.status != "deleted")
            ).scalars()
        )
        values = [
            {"memory_id": memory.id, "term": term, "owner_id": memory.owner_id}
            for memory in batch
            if memory.id in live
            for term in terms_of(memory.content)
        ]
        if values:
            connection.execute(sa.insert(terms), values)


def upgrade():
    op.create_table(
        "memory_terms",
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), primary_key=True),
        sa.Column("term", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
    )
    op.create_index("ix_memory_terms_owner_term", "memory_terms", ["owner_id", "term"])
    if op.get_bind().dialect.name == "postgresql":
        # Derived keywords of private memories: closed to the Supabase Data API like every other table.
        op.execute("ALTER TABLE memory_terms ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON memory_terms FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON memory_terms FROM authenticated;
        END IF;
        END $$""")
    _backfill()


def downgrade():
    op.drop_index("ix_memory_terms_owner_term", table_name="memory_terms")
    op.drop_table("memory_terms")
