"""Slack Block Kit response formatting."""

from __future__ import annotations

import textwrap

from app.core.config import Settings
from app.schemas.rca import AnalysisStatus, IncidentAnalysis
from app.schemas.sentry import SentryEventData, SentryIssueData

MAX_TEXT_CHARS = 2900  # Slack section text limit is 3000.

# Single source of truth for user-facing Slack copy.
PROGRESS_TEXT = "🔎 RCA Bot is investigating this incident..."
CHANNEL_NOT_CONFIGURED_TEXT = "This channel is not configured for RCA investigations."
DUPLICATE_REQUEST_TEXT = (
    "An investigation for this message has already been triggered "
    "(it may still be running) ✅"
)
NO_SENTRY_LINK_TEXT = (
    "I couldn't find a Sentry issue link in this thread. "
    "Please include the Sentry issue link and mention @rca-bot investigate again."
)
FAILURE_PREFIX = "❌ RCA investigation failed."
INSUFFICIENT_EVIDENCE_TEXT = "⚠️ Insufficient evidence — unable to establish a root cause from the available evidence."


def format_error_blocks(reason: str) -> list[dict]:
    """Standalone failure formatting used by the responder."""
    safe = _clip(reason or "Unknown error", 500)
    return [
        _section("\u274C RCA investigation failed."),
        _section(f"*Reason:*\n{safe}"),
    ]



def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _clip(text)}}


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return textwrap.shorten(text, width=limit, placeholder=" …")


class SlackResponseFormatter:
    """Formats a validated RCA as Slack Block Kit blocks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def progress_blocks(self) -> list[dict]:
        return [_section(PROGRESS_TEXT)]

    def missing_sentry_url_blocks(self) -> list[dict]:
        return [_section(NO_SENTRY_LINK_TEXT)]

    def channel_not_allowed_blocks(self) -> list[dict]:
        return [_section(CHANNEL_NOT_CONFIGURED_TEXT)]

    def already_investigating_blocks(self) -> list[dict]:
        return [_section(DUPLICATE_REQUEST_TEXT)]

    def failure_blocks(self, reason: str) -> list[dict]:
        safe_reason = _clip(reason or "Unknown error", 500)
        return [
            _section(FAILURE_PREFIX),
            _section(f"*Reason:*\n{safe_reason}"),
        ]

    def rca_blocks(
        self,
        analysis: IncidentAnalysis,
        sentry_issue: SentryIssueData | None = None,
        event: SentryEventData | None = None,
        investigation_id: int | None = None,
    ) -> list[dict]:
        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": "🔴 AI Incident Investigation"}}
        ]
        # Explicit insufficient evidence state
        if getattr(analysis, "analysis_status", None) == AnalysisStatus.INSUFFICIENT_EVIDENCE:
            blocks.append(_section(f"{INSUFFICIENT_EVIDENCE_TEXT}"))
            blocks.append(_section(f"*Summary*\n{analysis.summary}"))
            if analysis.unknowns:
                unknown_lines = [f"• {u}" for u in analysis.unknowns[:5]]
                blocks.append(_section("*Unknowns*\n" + "\n".join(unknown_lines)))
            blocks.append(_section(f"*Confidence*\n{analysis.confidence}%"))
            footer_bits = ["⚠️ AI-generated analysis. Verify before applying changes."]
            if sentry_issue:
                footer_bits.insert(0, f"Sentry issue `{sentry_issue.issue_id}`")
            blocks.append({"type": "divider"})
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(footer_bits)}]})
            return blocks

        blocks.append(_section(f"*Summary*\n{analysis.summary}"))
        blocks.append(_section(f"*Root Cause*\n{analysis.root_cause}"))

        evidence_lines: list[str] = []
        for ev in analysis.evidence:
            ref = ev.source_reference
            if ev.source_type == "CODE":
                ref = f"`{ref}`"
            evidence_lines.append(f"• {ev.source_type}: {ref} — {ev.explanation}")
        if evidence_lines:
            blocks.append(_section("*Evidence*\n" + "\n".join(evidence_lines[:10])))

        if analysis.suggested_fix:
            blocks.append(_section(f"*Suggested Fix*\n{analysis.suggested_fix}"))

        if analysis.relevant_files:
            file_lines = []
            for rf in analysis.relevant_files[:8]:
                location = f"{rf.path}:{rf.start_line}" + (
                    f"-{rf.end_line}" if rf.end_line and rf.end_line != rf.start_line else ""
                )
                file_lines.append(f"• `{location}`")
            blocks.append(_section("*Relevant Code*\n" + "\n".join(file_lines)))

        confidence_note = ""
        status = getattr(analysis, "analysis_status", None)
        if status == AnalysisStatus.LOW_CONFIDENCE:
            confidence_note = " _(low confidence — limited evidence)_"
        elif status == AnalysisStatus.INSUFFICIENT_EVIDENCE:
            confidence_note = " _(insufficient evidence)_"
        elif self._settings.confidence_threshold and analysis.confidence < self._settings.confidence_threshold:
            confidence_note = " _(low confidence — limited evidence)_"
        status_line = f" ({status.value})" if status else ""
        blocks.append(_section(f"*Confidence*\n{analysis.confidence}%{confidence_note}{status_line}"))

        if analysis.unknowns:
            unknown_lines = [f"• {u}" for u in analysis.unknowns[:5]]
            blocks.append(_section("*Unknowns*\n" + "\n".join(unknown_lines)))

        footer_bits = ["⚠️ AI-generated analysis. Verify before applying changes."]
        if sentry_issue:
            title = sentry_issue.title or "Sentry issue"
            footer_bits.insert(0, f"Sentry issue `{sentry_issue.issue_id}` — {_clip(title, 80)}")
        blocks.append({"type": "divider"})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(footer_bits)}]})
        return blocks
