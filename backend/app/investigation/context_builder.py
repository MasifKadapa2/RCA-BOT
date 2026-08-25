"""Builds normalized incident context from Sentry payloads."""

from __future__ import annotations

from typing import Any

from app.schemas.sentry import SentryEventData, SentryIssueData
from app.sentry.normalization import (
    normalize_event,
    normalize_issue,
)


class IncidentContextBuilder:
    """Converts raw Sentry API responses into normalized structures."""

    def build(
        self, issue_raw: dict, event_raw: dict | None
    ) -> tuple[SentryIssueData, SentryEventData | None, dict[str, Any]]:
        issue_id = str(issue_raw.get("id", ""))
        issue_data = normalize_issue(issue_id, issue_raw)
        event_data = (
            normalize_event(event_raw, fallback_environment=issue_data.environment)
            if event_raw
            else None
        )
        context = self._build_context(issue_data, event_data)
        return issue_data, event_data, context

    def _build_context(
        self, issue: SentryIssueData, event: SentryEventData | None
    ) -> dict[str, Any]:
        exception_dict: dict[str, Any] | None = None
        frames_list: list[dict[str, Any]] = []

        if event is not None:
            primary = event.primary_exception
            if primary is not None:
                exception_dict = {
                    "type": primary.type,
                    "value": primary.value,
                    "label": primary.label,
                }
            frames_list = [
                {
                    "filename": frame.filename,
                    "function": frame.function,
                    "module": frame.module,
                    "line_number": frame.line_number,
                    "column_number": frame.column_number,
                    "context_line": frame.context_line,
                    "in_app": frame.in_app,
                }
                for frame in event.frames[:25]
            ]

        return {
            "exception": exception_dict or {},
            "frames": frames_list,
            "release": event.release if event else issue.release,
            "environment": event.environment if event else issue.environment,
            "platform": event.platform if event else issue.platform,
            "transaction": event.transaction if event else None,
            "tags": dict(event.tags) if event else {},
            "timestamp": event.timestamp.isoformat()
            if event and event.timestamp
            else (issue.last_seen.isoformat() if issue.last_seen else None),
            "relevant_files": [],  # filled after RCA validation
        }
