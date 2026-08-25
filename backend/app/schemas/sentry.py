"""Sentry domain schemas and normalized stack frames."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SentryReference(BaseModel):
    """A Sentry issue reference extracted from Slack thread content."""

    issue_id: str
    url: str
    host: str | None = None
    organization_slug: str | None = None
    event_id: str | None = None


class StackFrame(BaseModel):
    filename: str | None = None
    abs_path: str | None = None
    function: str | None = None
    module: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    # Source context as [lineno, text] pairs (may be None when absent).
    context: list[list[Any]] | None = None
    context_line: str | None = None
    pre_context: list[str] = Field(default_factory=list)
    post_context: list[str] = Field(default_factory=list)
    in_app: bool = False
    raw_frame: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "ignore", "validate_assignment": True}

    @property
    def display(self) -> str:
        loc = self.filename or self.abs_path or self.module or "<unknown>"
        line = f":{self.line_number}" if self.line_number else ""
        func = f" in {self.function}" if self.function else ""
        return f"{loc}{line}{func}"

    @property
    def basename(self) -> str:
        if not self.filename:
            return ""
        return self.filename.replace("\\", "/").rsplit("/", 1)[-1]


class ExceptionInfo(BaseModel):
    type: str | None = None
    value: str | None = None
    module: str | None = None
    mechanism_type: str | None = None
    stacktrace_frames: list[StackFrame] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @property
    def label(self) -> str:
        prefix = f"{self.module}." if self.module else ""
        return f"{prefix}{self.type or 'Unknown'}: {self.value or ''}".strip()


class SentryIssueData(BaseModel):
    issue_id: str
    title: str | None = None
    short_id: str | None = None
    culprit: str | None = None
    level: str | None = None
    project: str | None = None
    environment: str | None = None
    release: str | None = None
    platform: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    status: str | None = None
    times_seen: int | None = None
    permalink: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class SentryEventData(BaseModel):
    event_id: Optional[str] = None
    issue_id: str | None = None
    title: str | None = None
    platform: str | None = None
    environment: str | None = None
    release: str | None = None
    transaction: str | None = None
    timestamp: datetime | None = None
    exceptions: list[ExceptionInfo] = Field(default_factory=list)
    frames: list[StackFrame] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)
    message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}

    @property
    def primary_exception(self) -> ExceptionInfo | None:
        """The innermost (most specific) exception with a type."""
        with_type = [e for e in self.exceptions if e.type]
        return with_type[-1] if with_type else (self.exceptions[-1] if self.exceptions else None)

    @property
    def exception_label(self) -> str | None:
        exc = self.primary_exception
        return exc.label if exc else None

    @property
    def in_app_frames(self) -> list["StackFrame"]:
        return [f for f in self.frames if f.in_app]

    @property
    def library_frames(self) -> list["StackFrame"]:
        return [f for f in self.frames if not f.in_app]
