"""Track confirmed room cleanup independently of admission status."""
import sqlalchemy as sa
from alembic import op

revision = '0004_voice_cleanup'
down_revision = '0003'
def upgrade():
    op.add_column('voice_sessions', sa.Column('room_cleanup_completed_at', sa.String(40), nullable=True))
def downgrade():
    op.drop_column('voice_sessions', 'room_cleanup_completed_at')
