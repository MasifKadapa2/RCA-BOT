"""Git ORM models for Change Intelligence (V2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import utcnow


class GitCommit(Base):
    __tablename__ = "git_commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha"),
        Index("ix_git_commits_repo_committed", "repository_id", "committed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    commit_sha: Mapped[str] = mapped_column(String(64))
    short_sha: Mapped[str] = mapped_column(String(16))
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_sha: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_merge_commit: Mapped[bool] = mapped_column(Boolean, default=False)
    files_changed_count: Mapped[int] = mapped_column(Integer, default=0)
    insertions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    file_changes: Mapped[list["GitFileChange"]] = relationship(
        back_populates="commit", cascade="all, delete-orphan"
    )


class GitFileChange(Base):
    __tablename__ = "git_file_changes"
    __table_args__ = (
        UniqueConstraint("commit_id", "file_path"),
        Index("ix_git_file_changes_path", "file_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("git_commits.id"))
    file_path: Mapped[str] = mapped_column(String(1024))
    old_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    change_type: Mapped[str] = mapped_column(String(4))  # A/M/D/R/T
    additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    commit: Mapped[GitCommit] = relationship(back_populates="file_changes")
    hunks: Mapped[list["GitChangeHunk"]] = relationship(
        back_populates="file_change", cascade="all, delete-orphan"
    )


class GitChangeHunk(Base):
    __tablename__ = "git_change_hunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("git_commits.id"))
    file_change_id: Mapped[int] = mapped_column(ForeignKey("git_file_changes.id"))
    file_path: Mapped[str] = mapped_column(String(1024))
    old_start: Mapped[int] = mapped_column(Integer)
    old_lines: Mapped[int] = mapped_column(Integer)
    new_start: Mapped[int] = mapped_column(Integer)
    new_lines: Mapped[int] = mapped_column(Integer)
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    file_change: Mapped[GitFileChange] = relationship(back_populates="hunks")


class GitIndexState(Base):
    """Per-repository marker for incremental Git indexing."""

    __tablename__ = "git_index_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), unique=True)
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    commits_indexed: Mapped[int] = mapped_column(Integer, default=0)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
