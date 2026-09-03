"""Posts investigation outcomes back to the originating Slack thread."""

from __future__ import annotations

from app.schemas.rca import IncidentAnalysis
from app.schemas.sentry import SentryEventData, SentryIssueData
from app.slack.formatter import (
    FAILURE_PREFIX,
    NO_SENTRY_LINK_TEXT,
    PROGRESS_TEXT,
    SlackResponseFormatter,
)


class SlackThreadResponder:
    def __init__(self, gateway, formatter: SlackResponseFormatter) -> None:
        self._gateway = gateway
        self._formatter = formatter

    def post_progress(self, channel_id: str, thread_ts: str) -> None:
        self._post(channel_id, thread_ts,
                   self._formatter.progress_blocks(),
                   fallback=PROGRESS_TEXT)

    def post_missing_sentry_url(self, channel_id: str, thread_ts: str) -> None:
        self._post(
            channel_id,
            thread_ts,
            self._formatter.missing_sentry_url_blocks(),
            fallback=NO_SENTRY_LINK_TEXT,
        )

    def post_failure(self, channel_id: str, thread_ts: str, reason: str) -> None:
        blocks = self._formatter.failure_blocks(reason)
        fallback = f"{FAILURE_PREFIX}\nReason:\n{reason}"
        self._post(channel_id, thread_ts, blocks, fallback=fallback)

    def post_rca(
        self,
        channel_id: str,
        thread_ts: str,
        analysis: IncidentAnalysis,
        issue: SentryIssueData | None = None,
        event: SentryEventData | None = None,
        investigation_id: int | None = None,
    ) -> None:
        blocks = self._formatter.rca_blocks(analysis, issue, event, investigation_id)
        first_text = next(
            (b["text"]["text"] for b in blocks if b.get("type") == "section"),
            "AI Incident Investigation",
        )
        self._post(channel_id, thread_ts, blocks, fallback=first_text)

    def _post(self, channel_id: str, thread_ts: str, blocks, fallback: str | None = None) -> None:
        self._gateway.post_reply(
            channel_id, thread_ts, text=fallback or "", blocks=blocks or None
        )

    # --- V2 — Change Intelligence ---------------------------------------------

    def post_regression(self, channel_id: str, thread_ts: str, analysis) -> None:
        from app.slack.formatter import regression_blocks

        blocks = regression_blocks(analysis)
        first = next((b["text"]["text"] for b in blocks if b.get("type") == "section"), "Change Intelligence")
        self._post(channel_id, thread_ts, blocks, fallback=first)

    def post_change_intel_unavailable(self, channel_id: str, thread_ts: str, reason: str) -> None:
        from app.slack.formatter import change_intel_unavailable_blocks

        self._post(channel_id, thread_ts, change_intel_unavailable_blocks(reason),
                   fallback="ℹ️ Change intelligence unavailable.")
