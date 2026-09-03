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


# --- V2 Change Intelligence ---------------------------------------------------

def regression_blocks(analysis) -> list[dict]:
    """Block Kit for a RegressionAnalysis (V2 Change Intelligence)."""
    from app.schemas.regression import RegressionStatus

    status = analysis.status
    if status == RegressionStatus.LIKELY_REGRESSION:
        headline = "🎯 Likely Regression"
    elif status == RegressionStatus.POSSIBLE_REGRESSION:
        headline = "❓ Possible Regression"
    elif status == RegressionStatus.INSUFFICIENT_EVIDENCE:
        headline = "⚠️ Insufficient evidence for change analysis"
    else:
        headline = "🚫 No confident regression identified"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔍 Change Intelligence"}},
        _section(f"*{headline}*"),
    ]

    likely = getattr(analysis, "likely_commit", None)
    if likely:
        cand = next((c for c in analysis.candidate_commits
                     if c.short_sha.startswith(str(likely)[:7])), None)
        if cand is not None:
            lines = [f"`{cand.short_sha}` — \"{_clip(cand.message or '', 120)}\""]
            if cand.author:
                lines.append(f"Author: {cand.author}")
            if cand.committed_at:
                lines.append(f"Committed: {cand.committed_at}")
            conf = f"Confidence: {analysis.confidence}%"
            blocks.append(_section(f"*Likely Regression*\n" + "\n".join(lines) + f"\n{conf}"))

            why = []
            sig = cand.signals or {}
            label = {"stack_file_match": "Modified the failing file",
                     "retrieved_file_match": "Modified retrieved code",
                     "symbol_match": "Changed the affected symbol",
                     "line_proximity": "Changed lines near the failing location",
                     "temporal_proximity": None,  # rendered with timing below
                     "diff_relevance": "Diff mentions exception terms",
                     "modified_files": None}
            for key, text in label.items():
                if key in sig and text:
                    why.append(f"✓ {text}")
            if "temporal_proximity" in sig and cand.committed_at:
                why.append(f"✓ Commit occurred before first observed error "
                           f"({_clip(cand.relevance_reason.split(';')[-1].strip(), 80)})")
            if why:
                blocks.append(_section("*Why this commit?*\n" + "\n".join(why)))

    if analysis.causal_explanation:
        blocks.append(_section(f"*Causal explanation*\n{_clip(analysis.causal_explanation)}"))

    ev_lines: list[str] = []
    for ref in (analysis.evidence_for or [])[:6]:
        loc = f"`{ref.file_path}`"
        if ref.start_line:
            loc += f":{ref.start_line}" + (f"-{ref.end_line}" if ref.end_line else "")
        ev_lines.append(f"• GIT `{ref.commit_sha[:8]}` {loc} — {_clip(ref.description, 140)}")
    if ev_lines:
        blocks.append(_section("*Git evidence*\n" + "\n".join(ev_lines)))
    against = [
        f"• GIT `{r.commit_sha[:8]}` `{r.file_path}` — {_clip(r.description, 120)}"
        for r in (analysis.evidence_against or [])[:4]
    ]
    if against:
        blocks.append(_section("*Evidence against*\n" + "\n".join(against)))

    if analysis.candidate_commits and status in (
        RegressionStatus.NO_CONFIDENT_REGRESSION,
        RegressionStatus.POSSIBLE_REGRESSION,
    ):
        ranked = [f"{i}. `{c.short_sha}` ({c.score}) {_clip(c.message or '', 60)}"
                  for i, c in enumerate(analysis.candidate_commits[:5], 1)]
        blocks.append(_section("*Candidate commits*\n" + "\n".join(ranked)))

    if analysis.unknowns:
        blocks.append(_section("*Unknowns*\n" + "\n".join(
            f"• {u}" for u in analysis.unknowns[:5])))
    blocks.append(_section("⚠️ Deployment timing is unknown from Git metadata alone."))
    if analysis.recommended_action:
        blocks.append(_section(f"*Suggested action*\n{_clip(analysis.recommended_action, 400)}"))
    return blocks


def change_intel_unavailable_blocks(reason: str) -> list[dict]:
    return [_section(
        "ℹ️ RCA completed, but change intelligence is unavailable: "
        f"{_clip(reason or 'repository is not a Git repository', 160)}"
    )]
