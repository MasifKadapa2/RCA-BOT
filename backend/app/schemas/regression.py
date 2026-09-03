"""V2 Change Intelligence schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RegressionStatus(str, Enum):
    LIKELY_REGRESSION = "LIKELY_REGRESSION"
    POSSIBLE_REGRESSION = "POSSIBLE_REGRESSION"
    NO_CONFIDENT_REGRESSION = "NO_CONFIDENT_REGRESSION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class GitEvidenceRef(BaseModel):
    commit_sha: str = Field(description="Full or short commit SHA")
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    description: str = ""

    model_config = {"extra": "ignore"}


class HunkOut(BaseModel):
    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    patch: str | None = None
    truncated: bool = False


class ChangeCandidate(BaseModel):
    commit_sha: str
    short_sha: str
    message: str | None = None
    author: str | None = None
    committed_at: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    score: float = 0.0
    signals: dict[str, float] = Field(default_factory=dict)
    hunks: list[dict[str, Any]] = Field(default_factory=list)
    relevance_reason: str = ""
    context_only: bool = False  # True → commit happened after incident; never causal


class RegressionAnalysis(BaseModel):
    status: RegressionStatus
    likely_commit: str | None = None
    candidate_commits: list[ChangeCandidate] = Field(default_factory=list)
    causal_explanation: str = ""
    evidence_for: list[GitEvidenceRef] = Field(default_factory=list)
    evidence_against: list[GitEvidenceRef] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    unknowns: list[str] = Field(default_factory=list)
    recommended_action: str = ""

    model_config = {"use_enum_values": True, "extra": "ignore"}
