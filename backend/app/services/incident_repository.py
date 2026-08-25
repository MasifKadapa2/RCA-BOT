"""Persistence helpers for incidents, investigations and Slack messages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Evidence,
    EvidenceSource,
    Incident,
    IncidentStatus,
    Investigation,
    SentryIssue,
    SlackMessage,
)


class DuplicateInvestigationError(Exception):
    """Raised when a trigger message already has an investigation."""


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return _as_str(value)


class IncidentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # --- Incidents ---------------------------------------------------------

    def find_by_trigger(self, channel_id: str, message_ts: str) -> Incident | None:
        return self._db.scalars(
            select(Incident).where(
                Incident.slack_channel_id == channel_id,
                Incident.triggering_message_ts == message_ts,
            )
        ).first()

    def create_incident(
        self,
        channel_id: str,
        thread_ts: str,
        triggering_message_ts: str,
        sentry_issue_id: str | None = None,
    ) -> Incident:
        """Create a PENDING incident; enforces idempotency at the DB level."""
        existing = self.find_by_trigger(channel_id, triggering_message_ts)
        if existing is not None:
            raise DuplicateInvestigationError(
                f"Investigation already exists for {channel_id}:{triggering_message_ts}"
            )
        incident = Incident(
            slack_channel_id=channel_id,
            slack_thread_ts=thread_ts,
            triggering_message_ts=triggering_message_ts,
            sentry_issue_id=sentry_issue_id,
            status=IncidentStatus.PENDING,
        )
        self._db.add(incident)
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            raise DuplicateInvestigationError("Duplicate trigger message") from exc
        return incident

    def get(self, incident_id: int) -> Incident | None:
        return self._db.get(Incident, incident_id)

    def set_status(self, incident: Incident, status: str, error_message: str | None = None) -> None:
        incident.status = status
        if status == IncidentStatus.FAILED:
            incident.error_message = error_message
        incident.updated_at = datetime.now(UTC)
        self._db.commit()

    def store_context(self, incident: Incident, context: dict[str, Any]) -> None:
        incident.context = context
        self._db.commit()

    # --- Investigations -----------------------------------------------------

    def latest_investigation(self, incident_id: int) -> Investigation | None:
        return self._db.scalars(
            select(Investigation)
            .where(Investigation.incident_id == incident_id)
            .order_by(Investigation.id.desc())
            .limit(1)
        ).first()

    def save_analysis(
        self,
        incident: Incident,
        analysis_data: dict[str, Any],
        evidence_rows: list[dict[str, Any]],
        extra_fields: dict[str, Any] | None = None,
    ) -> Investigation:
        extra_fields = extra_fields or {}
        investigation = Investigation(
            incident_id=incident.id,
            summary=analysis_data.get("summary"),
            root_cause=analysis_data.get("root_cause"),
            suggested_fix=analysis_data.get("suggested_fix"),
            confidence=analysis_data.get("confidence"),
            analysis_status=analysis_data.get("analysis_status") or extra_fields.get("analysis_status"),
            unknowns=analysis_data.get("unknowns") or [],
            affected_components=analysis_data.get("affected_components") or [],
            completed_at=datetime.now(UTC),
            llm_model=extra_fields.get("llm_model"),
            embedding_model=extra_fields.get("embedding_model"),
            slack_latency_ms=extra_fields.get("slack_latency_ms"),
            sentry_latency_ms=extra_fields.get("sentry_latency_ms"),
            retrieval_latency_ms=extra_fields.get("retrieval_latency_ms"),
            llm_latency_ms=extra_fields.get("llm_latency_ms"),
            validation_latency_ms=extra_fields.get("validation_latency_ms"),
            total_latency_ms=extra_fields.get("total_latency_ms"),
            retrieval_query=extra_fields.get("retrieval_query"),
            lexical_results=extra_fields.get("lexical_results"),
            vector_results=extra_fields.get("vector_results"),
            final_context=extra_fields.get("final_context"),
        )
        self._db.add(investigation)
        self._db.flush()
        for row in evidence_rows:
            self._db.add(
                Evidence(
                    investigation_id=investigation.id,
                    source_type=row["source_type"],
                    source_reference=row["source_reference"],
                    explanation=row.get("explanation", ""),
                    is_valid=row.get("is_valid", True),
                    metadata_json=row.get("metadata"),
                    score=row.get("score"),
                )
            )
        self._db.commit()
        return investigation

    def mark_failed(self, incident: Incident, error_message: str) -> None:
        investigation = self.latest_investigation(incident.id)
        if investigation is not None and investigation.completed_at is None:
            investigation.summary = f"Investigation failed: {error_message}"
            investigation.completed_at = datetime.now(UTC)
            self._db.commit()
        self.set_status(incident, IncidentStatus.FAILED, error_message=error_message)

    # --- Sentry issues -------------------------------------------------------

    def upsert_sentry_issue(self, issue_data: dict[str, Any]) -> SentryIssue:
        issue = self._db.get(SentryIssue, str(issue_data["issue_id"]))
        if issue is None:
            issue = SentryIssue(issue_id=str(issue_data["issue_id"]))
            self._db.add(issue)
        issue.title = issue_data.get("title")
        issue.environment = issue_data.get("environment")
        issue.release = _as_str(issue_data.get("release"))
        issue.last_seen = _as_str(issue_data.get("last_seen"))
        # JSON columns cannot hold datetimes; normalize everything safely.
        issue.metadata_json = {
            k: _json_safe(v)
            for k, v in issue_data.items()
            if k not in {"issue_id"}
        }
        self._db.commit()
        return issue

    # --- Slack messages ------------------------------------------------------

    def persist_slack_messages(
        self, incident: Incident, messages: list[dict[str, Any]], channel_id: str
    ) -> None:
        for m in messages:
            ts = str(m.get("ts"))
            existing = self._db.scalars(
                select(SlackMessage).where(
                    SlackMessage.channel_id == channel_id,
                    SlackMessage.message_ts == ts,
                )
            ).first()
            if existing is not None:
                if existing.incident_id is None:
                    existing.incident_id = incident.id
                continue
            self._db.add(
                SlackMessage(
                    incident_id=incident.id,
                    channel_id=channel_id,
                    message_ts=ts,
                    thread_ts=m.get("thread_ts"),
                    user_id=m.get("user"),
                    text=m.get("text"),
                    raw=None,  # never persist raw payloads wholesale; may contain tokens.
                )
            )
        self._db.commit()
