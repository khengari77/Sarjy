"""Fenced leases for dispatcher preparation claims."""

import sqlalchemy as sa
from alembic import op

revision = "0009_dispatcher_leases"
down_revision = "0008_tool_operation_state"


def upgrade():
    with op.batch_alter_table("context_operations") as batch:
        batch.add_column(sa.Column("lease_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.String(40), nullable=True))
        batch.create_index("ix_context_operations_lease_id", ["lease_id"])


def downgrade():
    with op.batch_alter_table("context_operations") as batch:
        batch.drop_index("ix_context_operations_lease_id")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_id")
