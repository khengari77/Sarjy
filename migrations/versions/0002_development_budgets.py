"""Durable, shared daily development reservations."""
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'

def upgrade():
    op.create_table('development_budgets',sa.Column('day',sa.String(10),primary_key=True),sa.Column('provider',sa.String(20),primary_key=True),sa.Column('reserved_units',sa.Integer(),nullable=False))

def downgrade():
    op.drop_table('development_budgets')
