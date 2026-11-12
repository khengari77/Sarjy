"""Durable memory-write claims precede paid inference."""

from alembic import op
import sqlalchemy as sa

revision = "0007_memory_write_requests"
down_revision = "0006_memory_versioning"


def upgrade():
    op.create_table(
        "memory_write_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_status", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_memory_write_owner_request"),
    )
    op.create_index("ix_memory_write_requests_owner_id", "memory_write_requests", ["owner_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE memory_write_requests ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON memory_write_requests FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON memory_write_requests FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    op.drop_table("memory_write_requests")
