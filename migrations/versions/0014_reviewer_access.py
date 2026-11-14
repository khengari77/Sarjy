"""Additive reviewer invitations, admission counters and identity linkage."""

import sqlalchemy as sa
from alembic import op

revision = "0014_reviewer_access"
down_revision = "0013_setting_write_requests"

TABLES = (
    "reviewer_invitations",
    "reviewer_attempt_windows",
    "reviewer_source_attempt_windows",
    "reviewer_creation_windows",
)


def upgrade():
    op.create_table(
        "reviewer_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
        sa.Column("max_identities", sa.Integer(), nullable=False),
        sa.Column("identities_created", sa.Integer(), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
    )
    op.create_table(
        "reviewer_attempt_windows",
        sa.Column("minute", sa.String(40), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "reviewer_source_attempt_windows",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("minute", sa.String(40), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "reviewer_creation_windows",
        sa.Column("invitation_id", sa.String(36), sa.ForeignKey("reviewer_invitations.id"), primary_key=True),
        sa.Column("hour", sa.String(40), primary_key=True),
        sa.Column("created", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("identities") as batch:
        batch.add_column(sa.Column("invitation_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_identities_invitation_id", "reviewer_invitations", ["invitation_id"], ["id"])
        batch.create_index("ix_identities_invitation_id", ["invitation_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON reviewer_invitations, reviewer_attempt_windows, reviewer_source_attempt_windows,
                reviewer_creation_windows FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON reviewer_invitations, reviewer_attempt_windows, reviewer_source_attempt_windows,
                reviewer_creation_windows FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    with op.batch_alter_table("identities") as batch:
        batch.drop_index("ix_identities_invitation_id")
        batch.drop_constraint("fk_identities_invitation_id", type_="foreignkey")
        batch.drop_column("invitation_id")
    for table in reversed(TABLES):
        op.drop_table(table)
