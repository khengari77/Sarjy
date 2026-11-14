"""Owner-scoped settings idempotency records.

Preferences are written without a conversation, so replay cannot reuse
``context_operations`` (its ``conversation_id`` is a non-null FK). This is a
small owner-scoped key table: one row per committed settings request.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_setting_write_requests"
down_revision = "0011_wordle"


def upgrade():
    op.create_table(
        "setting_write_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_setting_write_owner_request"),
    )
    op.create_index("ix_setting_write_requests_owner_id", "setting_write_requests", ["owner_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE setting_write_requests ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON setting_write_requests FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON setting_write_requests FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    op.drop_table("setting_write_requests")
