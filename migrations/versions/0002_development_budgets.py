"""Durable, shared daily development reservations."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.create_table(
        "development_budgets",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("development_budgets")
