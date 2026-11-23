"""Additive accounts, revocable account sessions and attempt counters."""

import sqlalchemy as sa
from alembic import op

revision = "0016_accounts"
down_revision = "0015_intro_seen"

TABLES = ("accounts", "account_sessions", "account_attempt_windows")


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("auth_user_id", sa.String(64), unique=True, nullable=False),
        sa.Column("identity_id", sa.String(64), sa.ForeignKey("identities.id"), unique=True, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("disabled_at", sa.String(40), nullable=True),
    )
    op.create_table(
        "account_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
    )
    op.create_table(
        "account_attempt_windows",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window", sa.String(40), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON accounts, account_sessions, account_attempt_windows FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON accounts, account_sessions, account_attempt_windows FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
