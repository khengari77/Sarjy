"""Add memory subjects, their lookup index and durable capture decisions.

Subject values are populated on new writes. Existing rows remain NULL and are
handled by a bounded legacy fallback; this migration rewrites no memory data.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_memory_subject"
down_revision = "0010_context_retrieval_indexes"


def upgrade():
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("subject", sa.String(120), nullable=True))
        batch.create_index("ix_memories_owner_subject_status", ["owner_id", "subject", "status"])
    op.create_table(
        "memory_capture_decisions",
        sa.Column("confirmation_id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("conversation_id", sa.String(64), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=True),
        sa.Column("target_version", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_memory_capture_decisions_owner_id", "memory_capture_decisions", ["owner_id"])
    op.create_index("ix_memory_capture_decisions_conversation_id", "memory_capture_decisions", ["conversation_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE memory_capture_decisions ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON memory_capture_decisions FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON memory_capture_decisions FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS memory_capture_decisions_private ON memory_capture_decisions")
    op.drop_index("ix_memory_capture_decisions_conversation_id", table_name="memory_capture_decisions")
    op.drop_index("ix_memory_capture_decisions_owner_id", table_name="memory_capture_decisions")
    op.drop_table("memory_capture_decisions")
    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_owner_subject_status")
        batch.drop_column("subject")
