"""Add durable tool-operation state for idempotent weather effects."""

import sqlalchemy as sa
from alembic import op

revision = "0008_tool_operation_state"
down_revision = "0007_memory_write_requests"


def upgrade():
    with op.batch_alter_table("tool_calls") as batch:
        batch.add_column(sa.Column("state", sa.String(20), nullable=False, server_default="completed"))
        batch.add_column(sa.Column("error_code", sa.String(80), nullable=True))
        batch.add_column(sa.Column("arguments_hash", sa.String(64), nullable=True))
        batch.alter_column("result", existing_type=sa.JSON(), nullable=True)
        batch.create_index("ix_tool_calls_state", ["state"])
    op.execute("UPDATE tool_calls SET state = 'completed' WHERE state IS NULL")


def downgrade():
    with op.batch_alter_table("tool_calls") as batch:
        batch.drop_index("ix_tool_calls_state")
        batch.drop_column("arguments_hash")
        batch.drop_column("error_code")
        batch.drop_column("state")
        batch.alter_column("result", existing_type=sa.JSON(), nullable=False)
