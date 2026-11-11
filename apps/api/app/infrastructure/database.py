from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey, JSON, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

class Identity(Base):
    __tablename__ = 'identities'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[str] = mapped_column(String(40))

class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('identities.id'), index=True)
    title: Mapped[str] = mapped_column(String(120), default='New conversation')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Turn(Base):
    __tablename__ = 'turns'
    __table_args__ = (UniqueConstraint('conversation_id', 'request_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='completed')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class VoiceSession(Base):
    __tablename__ = 'voice_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('identities.id'), index=True)
    room_name: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='starting')
    expires_at: Mapped[str] = mapped_column(String(40))
    pipeline: Mapped[dict] = mapped_column(JSON)
    room_cleanup_completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class SessionGate(Base):
    __tablename__ = 'session_gate'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marker: Mapped[int] = mapped_column(Integer, default=1)

class ToolCall(Base):
    __tablename__ = 'tool_calls'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey('turns.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    result: Mapped[dict] = mapped_column(JSON)

class DevelopmentBudget(Base):
    __tablename__ = 'development_budgets'
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    reserved_units: Mapped[int] = mapped_column(Integer, default=0)
