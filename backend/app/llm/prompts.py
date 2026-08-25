"""Prompts for RCA generation with strict grounding rules."""

from __future__ import annotations

import json

from app.schemas.rca import IncidentAnalysis
from app.schemas.retrieval import RetrievalResult
from app.schemas.sentry import SentryEventData, SentryIssueData

SYSTEM_PROMPT = """You are an expert software reliability engineer performing root cause analysis.

STRICT GROUNDING RULES - violating any of these makes your answer useless:
1. Use ONLY the evidence provided in this prompt. Do not use prior knowledge about the codebase.
2. NEVER invent source files. Only reference files that appear in the RETRIEVED CODE sections.
3. NEVER invent line numbers. Only cite line numbers shown in the retrieved context.
4. NEVER invent commits, pull requests, deployments or people.
5. NEVER claim code was executed or a fix was tested.
6. Clearly separate DIRECT EVIDENCE from INFERENCE in your explanations.
7. If the evidence is insufficient to determine a root cause, set analysis_status to INSUFFICIENT_EVIDENCE, summary to "Unable to establish the root cause from the available evidence.", root_cause to "Insufficient evidence.", and list what is missing under "unknowns". Do NOT fabricate a root cause.
8. Confidence must reflect evidence quality: high (80+) only when stack trace and code clearly align; medium (50-79) when partially aligned; low (<50) when mostly inference.
9. Every item in "relevant_files" MUST come from the numbered code blocks [C1], [C2], ... provided below.
10. Evidence source_reference for CODE must be "path:start_line-end_line" exactly as provided.
11. Evidence source_reference for SENTRY should be the Sentry issue id. Evidence source_reference for SLACK should be a short quote reference.
12. Set analysis_status to CONFIDENT when evidence strongly supports the RCA, LOW_CONFIDENCE when likely but incomplete, INSUFFICIENT_EVIDENCE when cannot establish root cause.

Respond with a single JSON object matching this schema:
{
  "summary": "one paragraph summary",
  "root_cause": "detailed root cause explanation grounded in the evidence",
  "evidence": [{"source_type": "SENTRY|SLACK|CODE", "source_reference": "...", "explanation": "..."}],
  "affected_components": ["component names"],
  "suggested_fix": "concrete fix suggestion referencing the relevant code",
  "confidence": 0-100,
  "analysis_status": "CONFIDENT | LOW_CONFIDENCE | INSUFFICIENT_EVIDENCE",
  "unknowns": ["things that could not be determined"],
  "relevant_files": [{"path": "...", "start_line": 1, "end_line": 10, "reason": "..."}]
}
"""


def format_sentry_context(issue: SentryIssueData | None, event: SentryEventData | None) -> str:
    lines = ["## INCIDENT INFORMATION"]
    if issue:
        lines.append(f"Sentry issue id: {issue.issue_id}")
        if issue.title:
            lines.append(f"Title: {issue.title}")
        if issue.culprit:
            lines.append(f"Culprit: {issue.culprit}")
        if issue.level:
            lines.append(f"Level: {issue.level}")
        if issue.environment:
            lines.append(f"Environment: {issue.environment}")
        if issue.release:
            lines.append(f"Release: {issue.release}")

    if event:
        lines.append("")
        lines.append("## EXCEPTION")
        for exc in event.exceptions[:3]:
            exc_type = exc.type or "Unknown"
            value = exc.value or "(no message)"
            lines.append(f"- {exc_type}: {value}")
        if event.message:
            lines.append(f"Message: {event.message}")
        if event.platform:
            lines.append(f"Platform: {event.platform}")
        if event.transaction:
            lines.append(f"Transaction: {event.transaction}")
        if event.timestamp:
            lines.append(f"Timestamp: {event.timestamp.isoformat()}")
        if event.tags:
            tag_items = ", ".join(f"{k}={v}" for k, v in list(event.tags.items())[:12])
            lines.append(f"Tags: {tag_items}")

        lines.append("")
        lines.append("## STACK TRACE (in-app frames first)")
        if event.frames:
            for index, frame in enumerate(event.frames[:15], start=1):
                location = frame.filename or frame.module or "(unknown file)"
                line = f"  at {location}"
                if frame.line_number:
                    line += f":{frame.line_number}"
                if frame.function:
                    line += f" in `{frame.function}`"
                lines.append(line)
        else:
            lines.append("(no frames available)")

    return "\n".join(lines)


def format_code_context(results: list[RetrievalResult]) -> str:
    lines = ["", "## RETRIEVED CODE"]
    if not results:
        lines.append(
            "(no code could be retrieved from the repository index — "
            "say so in unknowns and lower confidence)"
        )
        return "\n".join(lines)
    # Keep prompt small enough for free-tier token limits (~8k TPM):
    # max 6 blocks, 900 chars each.
    for index, result in enumerate(results[:6], start=1):
        symbol = f" ({result.symbol_name})" if result.symbol_name else ""
        lines.append(
            f"[C{index}] {result.relative_path}:{result.start_line}-{result.end_line}{symbol}"
        )
        lines.append("```")
        content = result.content
        if len(content) > 900:
            content = content[:900] + "\n… (truncated)"
        lines.append(content)
        lines.append("```")
    return "\n".join(lines)


def format_slack_context(thread_messages: list[str]) -> str:
    if not thread_messages:
        return ""
    lines = ["", "## SLACK THREAD CONTEXT (developer discussion)"]
    for message in thread_messages[:5]:
        text = message.strip().replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "…"
        # Strip bot link formatting noise.
        if text.startswith("<http"):
            continue
        lines.append(f"- {text}")
    if len(lines) == 2:
        return ""
    return "\n".join(lines)


def build_rca_user_prompt(
    issue: SentryIssueData | None,
    event: SentryEventData | None,
    retrieval_results: list[RetrievalResult],
    slack_messages: list[str] | None = None,
) -> str:
    parts = [
        format_sentry_context(issue, event),
        format_slack_context(slack_messages or []),
        format_code_context(retrieval_results),
        "",
        "Analyze the incident above and respond with the JSON object now.",
    ]
    return "\n".join(parts)


def schema_hint() -> str:
    return json.dumps(IncidentAnalysis.model_json_schema(), indent=2)


__all__ = [
    "SYSTEM_PROMPT",
    "build_rca_user_prompt",
    "format_sentry_context",
    "format_code_context",
    "format_slack_context",
]
