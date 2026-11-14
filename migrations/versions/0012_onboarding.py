"""Owner-keyed, versioned first-run onboarding progress.

Additive table only.  Onboarding never stores memories; the capability step is
transparent because memory is on by default, not consent-gated.  ``applied_events``
holds the bounded set of ``(event_id, payload fingerprint)`` pairs used to make
retries idempotent.

This sits after ``0011_wordle`` so the integration chain keeps exactly one head;
memory work should parent ``0012_onboarding``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_onboarding"
down_revision = "0011_wordle"


def upgrade():
    op.create_table(
        "onboarding_progress",
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("preferred_name", sa.String(80), nullable=True),
        sa.Column("selected_voice_id", sa.String(100), nullable=True),
        sa.Column("selected_pace", sa.String(20), nullable=True),
        sa.Column("intro_played_at", sa.String(40), nullable=True),
        sa.Column("started_at", sa.String(40), nullable=True),
        sa.Column("completed_at", sa.String(40), nullable=True),
        sa.Column("suspended_from", sa.String(30), nullable=True),
        sa.Column("last_event_id", sa.String(100), nullable=True),
        sa.Column("applied_events", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE onboarding_progress ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON onboarding_progress FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON onboarding_progress FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    op.drop_table("onboarding_progress")
