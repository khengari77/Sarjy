"""Durable memory correction lineage and owner-scoped request identity."""

from alembic import op
import sqlalchemy as sa

revision = "0006_memory_versioning"
down_revision = "0005_day2_context"


def upgrade():
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("supersedes_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("request_key", sa.String(100), nullable=True))
        batch.create_foreign_key("fk_memory_supersedes", "memories", ["supersedes_id"], ["id"])
        batch.create_unique_constraint("uq_memory_owner_request", ["owner_id", "request_key"])


def downgrade():
    with op.batch_alter_table("memories") as batch:
        batch.drop_constraint("uq_memory_owner_request", type_="unique")
        batch.drop_constraint("fk_memory_supersedes", type_="foreignkey")
        batch.drop_column("request_key")
        batch.drop_column("supersedes_id")
