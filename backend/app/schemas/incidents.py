"""API response schemas for the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class IncidentListItem(BaseModel):
    id: int
    status: str
    sentry_issue_id: str | None
    title: str | None
    confidence: int | None
    slack_channel_id: str | None
    slack_thread_ts: str | None
    created_at: datetime


class InvestigationSummary(BaseModel):
    id: int
    summary: str | None
    root_cause: str | None
    suggested_fix: str | None
    confidence: int | None
    analysis_status: str | None = None
    unknowns: list[str]
    affected_components: list[str] | None = None
    created_at: datetime
    completed_at: datetime | None
    # Observability
    llm_model: str | None = None
    embedding_model: str | None = None
    slack_latency_ms: int | None = None
    sentry_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    llm_latency_ms: int | None = None
    validation_latency_ms: int | None = None
    total_latency_ms: int | None = None


class EvidenceOut(BaseModel):
    source_type: str
    source_reference: str
    explanation: str
    is_valid: bool = True
    metadata: dict[str, Any] = {}


class IncidentDetail(BaseModel):
    id: int
    status: str
    error_message: str | None = None
    sentry_issue_id: str | None
    sentry_title: str | None
    sentry_environment: str | None
    sentry_release: str | None
    slack_channel_id: str | None
    slack_thread_ts: str | None
    triggering_message_ts: str | None
    exception: dict[str, Any] | None = None
    stack_trace: list[dict[str, Any]] = []
    release: str | None = None
    environment: str | None = None
    platform: str | None = None
    transaction: str | None = None
    tags: dict[str, str] = {}
    timestamp: str | None = None
    investigation: InvestigationSummary | None = None
    evidence: list[EvidenceOut] = []
    relevant_files: list[dict[str, Any]] = []
    code_snippets: list[dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime


class RepositoryOut(BaseModel):
    id: int
    name: str
    path: str
    indexed_files: int
    created_at: datetime
    updated_at: datetime


class RetrievalTraceOut(BaseModel):
    query: dict[str, Any] | None = None
    lexical_results: list[dict[str, Any]] | None = None
    vector_results: list[dict[str, Any]] | None = None
    final_context: list[dict[str, Any]] | None = None


class IndexRequest(BaseModel):
    repository_path: str | None = None


# --- V2 — Change Intelligence -------------------------------------------------

class RegressionOut(BaseModel):
    status: str | None = None
    likely_commit: str | None = None
    confidence: int | None = None
    causal_explanation: str | None = None
    evidence_for: list[dict[str, Any]] = []
    evidence_against: list[dict[str, Any]] = []
    unknowns: list[str] = []
    recommended_action: str | None = None
    regression_model: str | None = None
    candidates: list[dict[str, Any]] = []


class TimelineEntry(BaseModel):
    time: str | None = None
    label: str
    detail: str | None = None


class GitIndexStatsOut(BaseModel):
    commits_indexed: int = 0
    new_commits: int = 0
    skipped_commits: int = 0
    files_changed: int = 0
    hunks_indexed: int = 0
    truncated_diffs: int = 0
