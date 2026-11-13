"""Additive indexes for bounded context retrieval.

These support the deterministic recent-turn window, the owner-scoped active
memory candidate bound and the tool-result lookup.  No data is rewritten.
"""

from alembic import op

revision = "0010_context_retrieval_indexes"
down_revision = "0009_dispatcher_leases"

INDEXES = (
    ("ix_turns_conversation_status_created", "turns", ["conversation_id", "status", "created_at"]),
    ("ix_memories_owner_status_updated", "memories", ["owner_id", "status", "updated_at", "id"]),
    ("ix_memories_supersedes_id", "memories", ["supersedes_id"]),
    ("ix_tool_calls_turn_state", "tool_calls", ["turn_id", "state"]),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
