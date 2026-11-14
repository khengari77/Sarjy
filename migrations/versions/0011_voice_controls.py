"""Add server-authorized live voice controls and the confirmed active voice.

Additive only: existing sessions keep their pipeline voice as the confirmed
active voice until the first accepted ACK.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_voice_controls"
down_revision = "0010_context_retrieval_indexes"


def upgrade():
    op.add_column("voice_sessions", sa.Column("active_voice_id", sa.String(length=100), nullable=True))
    op.add_column("voice_sessions", sa.Column("active_settings_revision", sa.Integer(), nullable=True))
    op.create_table(
        "voice_controls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("voice_id", sa.String(length=100), nullable=False),
        sa.Column("settings_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["identities.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["voice_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "request_id"),
    )
    op.create_index("ix_voice_controls_conversation_id", "voice_controls", ["conversation_id"])
    op.create_index("ix_voice_controls_owner_id", "voice_controls", ["owner_id"])
    op.create_index("ix_voice_controls_session_id", "voice_controls", ["session_id"])


def downgrade():
    op.drop_index("ix_voice_controls_session_id", table_name="voice_controls")
    op.drop_index("ix_voice_controls_owner_id", table_name="voice_controls")
    op.drop_index("ix_voice_controls_conversation_id", table_name="voice_controls")
    op.drop_table("voice_controls")
    op.drop_column("voice_sessions", "active_settings_revision")
    op.drop_column("voice_sessions", "active_voice_id")
