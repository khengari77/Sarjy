"""Additive per-identity memory capture mode."""

import sqlalchemy as sa
from alembic import op

revision = "0017_memory_mode"
down_revision = "0016_accounts"


def upgrade():
    with op.batch_alter_table("user_settings") as batch:
        batch.add_column(sa.Column("memory_mode", sa.String(10), nullable=False, server_default="auto"))


def downgrade():
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_column("memory_mode")
