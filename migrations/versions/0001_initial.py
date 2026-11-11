"""Initial Day 1 schema."""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None

def upgrade():
    op.create_table('identities', sa.Column('id',sa.String(64),primary_key=True), sa.Column('token_hash',sa.String(64),unique=True,nullable=False), sa.Column('expires_at',sa.String(40),nullable=False))
    op.create_table('conversations',sa.Column('id',sa.String(36),primary_key=True),sa.Column('owner_id',sa.String(64),sa.ForeignKey('identities.id'),nullable=False),sa.Column('title',sa.String(120),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('created_at',sa.String(40),nullable=False))
    op.create_index('ix_conversations_owner_id','conversations',['owner_id'])
    op.create_table('turns',sa.Column('id',sa.String(36),primary_key=True),sa.Column('conversation_id',sa.String(36),sa.ForeignKey('conversations.id'),nullable=False),sa.Column('request_id',sa.String(100),nullable=False),sa.Column('role',sa.String(20),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('created_at',sa.String(40),nullable=False),sa.Column('snapshot',sa.JSON()),sa.UniqueConstraint('conversation_id','request_id'))
    op.create_index('ix_turns_conversation_id','turns',['conversation_id'])
    op.create_table('voice_sessions',sa.Column('id',sa.String(36),primary_key=True),sa.Column('conversation_id',sa.String(36),sa.ForeignKey('conversations.id'),nullable=False),sa.Column('owner_id',sa.String(64),sa.ForeignKey('identities.id'),nullable=False),sa.Column('room_name',sa.String(100),unique=True,nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('expires_at',sa.String(40),nullable=False),sa.Column('pipeline',sa.JSON(),nullable=False))
    op.create_index('ix_voice_sessions_conversation_id','voice_sessions',['conversation_id'])
    op.create_index('ix_voice_sessions_owner_id','voice_sessions',['owner_id'])
    op.create_table('tool_calls',sa.Column('id',sa.String(36),primary_key=True),sa.Column('turn_id',sa.String(36),sa.ForeignKey('turns.id'),nullable=False),sa.Column('request_id',sa.String(100),unique=True,nullable=False),sa.Column('name',sa.String(50),nullable=False),sa.Column('result',sa.JSON(),nullable=False))
    op.create_index('ix_tool_calls_turn_id','tool_calls',['turn_id'])
    op.create_table('session_gate',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('marker',sa.Integer(),nullable=False))
    op.execute(sa.text('INSERT INTO session_gate (id, marker) VALUES (1, 1)'))

def downgrade():
    for table in ['tool_calls','session_gate','voice_sessions','turns','conversations','identities']:
        op.drop_table(table)
