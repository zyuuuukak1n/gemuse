"""SQLAlchemy ORM models for Personal Muse."""

import uuid
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class JSONType(TypeDecorator[Any]):
    """Cross-dialect JSON type: JSONB on PostgreSQL, standard JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class EmbeddingVector(TypeDecorator[list[float]]):
    """Cross-dialect 768-dim vector: pgvector Vector(768) on PostgreSQL, JSON on SQLite."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 768) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


class Conversation(Base):
    """A persistent chat session."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), default="New Conversation", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Message(Base):
    """A single message inside a Conversation."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    thinking_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    attachments: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Job(Base):
    """Persistent asynchronous / streaming agent execution job."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), default="queued", nullable=False, index=True
    )  # queued | running | waiting_approval | paused_manual | completed | failed
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    thinking_level: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="jobs")
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EpisodeMemory(Base):
    """Layer 2: Semantic episodic and knowledge RAG memory backed by pgvector."""

    __tablename__ = "episode_memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="conversation", nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(768), nullable=False)
    importance_score: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class SkillMemory(Base):
    """Layer 3: Reusable procedural skill memory."""

    __tablename__ = "skill_memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_keywords: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    steps_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ApprovalRequest(Base):
    """Human-in-the-loop approval record with hashed one-time HMAC token."""

    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )  # pending | approved | rejected | expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    job: Mapped[Job | None] = relationship(back_populates="approval_requests")


class BrowserAuditLog(Base):
    """Audit trail for Step 3 isolated browser actions and manual handovers."""

    __tablename__ = "browser_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator: Mapped[str] = mapped_column(String(64), default="agent", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

