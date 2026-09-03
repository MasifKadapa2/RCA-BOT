"""SQLAlchemy ORM models for RCA Bot."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import utcnow


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    files: Mapped[list["IndexedFile"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class IndexedFile(Base):
    __tablename__ = "indexed_files"
    __table_args__ = (UniqueConstraint("repository_id", "relative_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    relative_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="files")
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="indexed_file", cascade="all, delete-orphan"
    )


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    indexed_file_id: Mapped[int] = mapped_column(ForeignKey("indexed_files.id"))
    symbol_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    symbol_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    vector_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    indexed_file: Mapped[IndexedFile] = relationship(back_populates="chunks")


class SentryIssue(Base):
    __tablename__ = "sentry_issues"

    issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class IncidentStatus:
    PENDING = "PENDING"
    INVESTIGATING = "INVESTIGATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("slack_channel_id", "triggering_message_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slack_channel_id: Mapped[str] = mapped_column(String(64))
    slack_thread_ts: Mapped[str] = mapped_column(String(64))
    triggering_message_ts: Mapped[str] = mapped_column(String(64))
    sentry_issue_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sentry_issues.issue_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default=IncidentStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sentry_issue: Mapped[SentryIssue | None] = relationship()
    investigations: Mapped[list["Investigation"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    slack_messages: Mapped[list["SlackMessage"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unknowns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    affected_components: Mapped[list | None] = mapped_column(JSON, nullable=True)
    validation_warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Observability: model metadata
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Latency breakdown in milliseconds
    slack_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentry_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Retrieval trace (persisted for dashboard observability)
    retrieval_query: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lexical_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    vector_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    final_context: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # V2 — Change Intelligence
    regression_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regression_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    regression_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_index_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    git_candidate_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diff_analysis_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    causal_llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regression_validation_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="investigations")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class EvidenceSource:
    SENTRY = "SENTRY"
    SLACK = "SLACK"
    CODE = "CODE"


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"))
    source_type: Mapped[str] = mapped_column(String(16))
    source_reference: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    llm_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieval_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    investigation: Mapped[Investigation] = relationship(back_populates="evidence")


class SlackMessage(Base):
    __tablename__ = "slack_messages"
    __table_args__ = (UniqueConstraint("channel_id", "message_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    channel_id: Mapped[str] = mapped_column(String(64))
    message_ts: Mapped[str] = mapped_column(String(64))
    thread_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident | None] = relationship(back_populates="slack_messages")


# V2 — Change Intelligence Git models
from app.models.git import GitChangeHunk, GitCommit, GitFileChange, GitIndexState  # noqa: E402
