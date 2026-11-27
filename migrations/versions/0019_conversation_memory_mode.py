"""Per-conversation memory mode override."""

import sqlalchemy as sa
from alembic import op

revision = "0019_conversation_memory_mode"
down_revision = "0018_conversation_delete_scope"


def upgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("memory_mode", sa.String(10), nullable=True))


def downgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("memory_mode")
