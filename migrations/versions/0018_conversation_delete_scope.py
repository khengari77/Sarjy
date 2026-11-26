"""Conversation tombstone and per-memory scope."""

import sqlalchemy as sa
from alembic import op

revision = "0018_conversation_delete_scope"
down_revision = "0017_memory_mode"


def upgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("deleted_at", sa.String(40), nullable=True))
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("scope", sa.String(12), nullable=False, server_default="global"))


def downgrade():
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("scope")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("deleted_at")
