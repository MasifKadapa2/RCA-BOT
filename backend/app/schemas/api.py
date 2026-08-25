"""API-facing schemas for incidents and repositories."""

from datetime import datetime

from pydantic import BaseModel


class IncidentSummary(BaseModel):
    id: int
    correlation_id: str
    status: str
    sentry_issue_id: str | None
    sentry_title: str | None
    confidence: int | None
    slack_channel_id: str
    slack_thread_ts: str
    created_at: datetime


class EvidenceOut(BaseModel):
    id: int
    source_type: str
    source_reference: str
    explanation: str | None
    llm_cited: bool
    score: float | None
    retrieval_method: str | None
    metadata: dict | None


class StackFrameOut(BaseModel):
    filename: str | None = None
    function: str | None = None
    module: str | None = None
    line_number: int | None = None
    in_app: bool = False


class IncidentDetail(BaseModel):
    id: int
    correlation_id: str
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # Sentry info
    sentry_issue_id: str | None
    sentry_title: str | None
    sentry_environment: str | None
    sentry_release: str | None
    sentry_last_seen: datetime | None
    exception_type: str | None
    exception_value: str | None
    platform: str | None
    stack_frames: list[StackFrameOut]
    # RCA
    summary: str | None
    root_cause: str | None
    suggested_fix: str | None
    confidence: int | None
    unknowns: list[str]
    affected_components: list[str]
    validation_warnings: list[str]
    investigation_completed_at: datetime | None
    # Slack reference (no secrets; enough to build a permalink client-side)
    slack_channel_id: str
    slack_thread_ts: str


class RepositoryOut(BaseModel):
    id: int
    name: str
    path: str
    indexed_files: int
    code_chunks: int
    last_indexed_at: datetime | None
    created_at: datetime


class IndexResult(BaseModel):
    repository_id: int
    name: str
    path: str
    files_scanned: int
    files_indexed: int
    files_skipped: int
    files_removed: int
    chunks_created: int
    duration_seconds: float
