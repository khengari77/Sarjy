from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1536
# Postgres uses pgvector; SQLite keeps a JSON placeholder so local dev and CI
# exercise the same ORM without the extension.
EMBEDDING_TYPE = Vector(EMBEDDING_DIMENSIONS).with_variant(JSON(), "sqlite")


def now():
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class ReviewerInvitation(Base):
    __tablename__ = "reviewer_invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ReviewerAttemptWindow(Base):
    """Global admission-attempt counter for one UTC fixed minute."""

    __tablename__ = "reviewer_attempt_windows"
    minute: Mapped[str] = mapped_column(String(40), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ReviewerCreationWindow(Base):
    """Per-invitation identity-creation counter for one UTC fixed hour."""

    __tablename__ = "reviewer_creation_windows"
    invitation_id: Mapped[str] = mapped_column(ForeignKey("reviewer_invitations.id"), primary_key=True)
    hour: Mapped[str] = mapped_column(String(40), primary_key=True)
    created: Mapped[int] = mapped_column(Integer, default=0)


class Identity(Base):
    __tablename__ = "identities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[str] = mapped_column(String(40))
    invitation_id: Mapped[str | None] = mapped_column(ForeignKey("reviewer_invitations.id"), nullable=True, index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New conversation")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id"),
        Index("ix_turns_conversation_status_created", "conversation_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    room_name: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="starting")
    expires_at: Mapped[str] = mapped_column(String(40))
    pipeline: Mapped[dict] = mapped_column(JSON)
    room_cleanup_completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SessionGate(Base):
    __tablename__ = "session_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marker: Mapped[int] = mapped_column(Integer, default=1)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_turn_state", "turn_id", "state"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), index=True)
    # Stable operation identity: one model tool call maps to exactly one row.
    request_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed", index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    arguments_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DevelopmentBudget(Base):
    __tablename__ = "development_budgets"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    reserved_units: Mapped[int] = mapped_column(Integer, default=0)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key"),
        Index("ix_memories_owner_status_updated", "owner_id", "status", "updated_at", "id"),
    )
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id"), nullable=True, index=True)
    request_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(EMBEDDING_TYPE, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(60), default="text-embedding-3-small")
    source_turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id"), index=True, nullable=True)
    source_conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class MemoryWriteRequest(Base):
    __tablename__ = "memory_write_requests"
    __table_args__ = (UniqueConstraint("owner_id", "request_key", name="uq_memory_write_owner_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    request_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class ContextRevision(Base):
    __tablename__ = "context_revisions"
    __table_args__ = (UniqueConstraint("conversation_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class ContextOperation(Base):
    __tablename__ = "context_operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    command_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    revision_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    outcome: Mapped[str] = mapped_column(String(20), default="pending")
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class ContextExclusion(Base):
    __tablename__ = "context_exclusions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("context_operations.id"), index=True)
    scope: Mapped[str] = mapped_column(String(20), default="conversation")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), unique=True, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    canonical_messages: Mapped[list] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    pipeline_config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class TurnMetric(Base):
    __tablename__ = "turn_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), unique=True, index=True)
    stt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eot_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tts_ttfb_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttfa_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class UserSetting(Base):
    __tablename__ = "user_settings"
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), primary_key=True)
    speaking_speed: Mapped[float] = mapped_column(Float, default=1.0)
    captions_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    response_preference: Mapped[str] = mapped_column(String(20), default="automatic")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class SettingRevision(Base):
    __tablename__ = "setting_revisions"
    __table_args__ = (UniqueConstraint("owner_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
