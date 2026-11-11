"""Keep backend-owned tables inaccessible through Supabase's public Data API."""
from alembic import op
revision = '0003'
down_revision = '0002'
TABLES = ('identities','conversations','turns','voice_sessions','tool_calls','session_gate','development_budgets')

def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
    # These roles are Supabase-specific; ordinary PostgreSQL installations need no grants removed.
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON identities, conversations, turns, voice_sessions, tool_calls, session_gate, development_budgets, alembic_version FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON identities, conversations, turns, voice_sessions, tool_calls, session_gate, development_budgets, alembic_version FROM authenticated;
    END IF;
    END $$""")
    op.execute('ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY')

def downgrade():
    # Preserve the access restriction on downgrade; restoring public grants is never automatic.
    pass
