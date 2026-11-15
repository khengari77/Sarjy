"""Additive per-identity marker for the first-call spoken introduction."""

import sqlalchemy as sa
from alembic import op

revision = "0015_intro_seen"
down_revision = "0014_reviewer_access"


def upgrade():
    with op.batch_alter_table("user_settings") as batch:
        batch.add_column(sa.Column("intro_seen_at", sa.String(40), nullable=True))


def downgrade():
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_column("intro_seen_at")
