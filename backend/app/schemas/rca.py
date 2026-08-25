"""Structured RCA output schema produced by the LLM and validated by RCAValidator."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceType(str, Enum):
    SENTRY = "SENTRY"
    SLACK = "SLACK"
    CODE = "CODE"


class Evidence(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    source_type: EvidenceType = Field(description="SENTRY | SLACK | CODE")
    source_reference: str = Field(description="Issue id, message ts or file path reference")
    explanation: str = Field(description="Why this evidence matters")

    @field_validator("source_type", mode="before")
    @classmethod
    def _normalize_source_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value


class RelevantFile(BaseModel):
    path: str = Field(description="Repository relative path")
    start_line: int = Field(description="1-indexed start line")
    end_line: int = Field(description="1-indexed end line, >= start_line")
    reason: str = ""

    @field_validator("path")
    @classmethod
    def _strip_path(cls, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("/"):
            return stripped
        return stripped.lstrip("./")


class AnalysisStatus(str, Enum):
    CONFIDENT = "CONFIDENT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class IncidentAnalysis(BaseModel):
    """The complete structured RCA the LLM must produce."""

    summary: str
    root_cause: str
    evidence: list[Evidence] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    suggested_fix: str = ""
    confidence: int = Field(default=0, ge=0, le=100)
    analysis_status: AnalysisStatus = Field(default=AnalysisStatus.CONFIDENT)
    unknowns: list[str] = Field(default_factory=list)
    relevant_files: list[RelevantFile] = Field(default_factory=list)
