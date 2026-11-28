"""Record the memory scope on the durable write claim (temporary memory write path)."""

import sqlalchemy as sa
from alembic import op

revision = "0021_memory_write_scope"
down_revision = "0020_memory_terms"


def upgrade():
    with op.batch_alter_table("memory_write_requests") as batch:
        batch.add_column(sa.Column("scope", sa.String(12), nullable=False, server_default="global"))


def downgrade():
    with op.batch_alter_table("memory_write_requests") as batch:
        batch.drop_column("scope")
