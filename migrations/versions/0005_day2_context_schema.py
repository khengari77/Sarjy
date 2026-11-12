"""Additive Day 2 context, memory, settings, operation and metrics tables.

Postgres gets pgvector with a 1536-dimension embedding column and private
grants; SQLite keeps a JSON placeholder so local development and CI run the
same models without the extension.
"""

from alembic import op
import sqlalchemy as sa

from app.infrastructure.database import EMBEDDING_TYPE

revision = "0005_day2_context"
down_revision = "0004_voice_cleanup"

TABLES = (
    "memories",
    "context_revisions",
    "context_operations",
    "context_exclusions",
    "context_snapshots",
    "turn_metrics",
    "user_settings",
    "setting_revisions",
)


def upgrade():
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", EMBEDDING_TYPE, nullable=True),
        sa.Column("embedding_model", sa.String(60), nullable=False),
        sa.Column("source_turn_id", sa.String(36), sa.ForeignKey("turns.id"), nullable=True),
        sa.Column("source_conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_memories_owner_id", "memories", ["owner_id"])
    op.create_index("ix_memories_source_turn_id", "memories", ["source_turn_id"])
    op.create_index("ix_memories_source_conversation_id", "memories", ["source_conversation_id"])
    op.create_index("ix_memories_status", "memories", ["status"])

    op.create_table(
        "context_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("conversation_id", "revision"),
    )
    op.create_index("ix_context_revisions_conversation_id", "context_revisions", ["conversation_id"])

    op.create_table(
        "context_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("command_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision_before", sa.Integer(), nullable=True),
        sa.Column("revision_after", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_context_operations_owner_id", "context_operations", ["owner_id"])
    op.create_index("ix_context_operations_conversation_id", "context_operations", ["conversation_id"])

    op.create_table(
        "context_exclusions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False),
        sa.Column("operation_id", sa.String(36), sa.ForeignKey("context_operations.id"), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_context_exclusions_owner_id", "context_exclusions", ["owner_id"])
    op.create_index("ix_context_exclusions_conversation_id", "context_exclusions", ["conversation_id"])
    op.create_index("ix_context_exclusions_memory_id", "context_exclusions", ["memory_id"])
    op.create_index("ix_context_exclusions_operation_id", "context_exclusions", ["operation_id"])
    op.create_index("ix_context_exclusions_active", "context_exclusions", ["active"])

    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("canonical_messages", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("pipeline_config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_context_snapshots_turn_id", "context_snapshots", ["turn_id"], unique=True)

    op.create_table(
        "turn_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("stt_ms", sa.Integer(), nullable=True),
        sa.Column("eot_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ttft_ms", sa.Integer(), nullable=True),
        sa.Column("tts_ttfb_ms", sa.Integer(), nullable=True),
        sa.Column("ttfa_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
    )
    op.create_index("ix_turn_metrics_turn_id", "turn_metrics", ["turn_id"], unique=True)

    op.create_table(
        "user_settings",
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), primary_key=True),
        sa.Column("speaking_speed", sa.Float(), nullable=False),
        sa.Column("captions_visible", sa.Boolean(), nullable=False),
        sa.Column("approved_voice_id", sa.String(100), nullable=True),
        sa.Column("response_preference", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )

    op.create_table(
        "setting_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("identities.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "revision"),
    )
    op.create_index("ix_setting_revisions_owner_id", "setting_revisions", ["owner_id"])

    if postgres:
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON memories, context_revisions, context_operations, context_exclusions, context_snapshots, turn_metrics, user_settings, setting_revisions FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON memories, context_revisions, context_operations, context_exclusions, context_snapshots, turn_metrics, user_settings, setting_revisions FROM authenticated;
        END IF;
        END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
