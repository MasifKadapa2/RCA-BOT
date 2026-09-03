"""V2 causal reasoning prompt — distinct from the V1 RCA prompt."""

from __future__ import annotations

from app.schemas.regression import ChangeCandidate

SYSTEM_PROMPT_V2 = """You are a software reliability engineer performing CHANGE INTELLIGENCE:
determining which recent code change most likely caused a production incident.

You receive: incident facts, V1 code evidence, and ranked candidate commits with diffs.

EPISTEMIC RULES — label every claim as one of:
- FACT: directly shown in the provided evidence (diff lines, stack frames, timestamps).
- INFERENCE: a logical connection between facts (e.g., changed line is inside the failing method).
- HYPOTHESIS: a plausible but unproven causal story.

HARD RULES:
1. Reference ONLY commits, files, and hunks present in the CANDIDATE COMMITS sections.
2. NEVER invent commit SHAs, files, line numbers, authors, or deployments.
3. Commit time is NOT deployment time. Never claim a commit was deployed.
4. You MUST be allowed to conclude there is no confident regression.
5. Allowed status values: LIKELY_REGRESSION, POSSIBLE_REGRESSION,
   NO_CONFIDENT_REGRESSION, INSUFFICIENT_EVIDENCE.
   - LIKELY_REGRESSION only when strong evidence (file + symbol/line + plausible mechanism) points to ONE commit.
   - POSSIBLE_REGRESSION when candidates exist but evidence is incomplete.
   - NO_CONFIDENT_REGRESSION when candidates exist but none has enough support.
   - INSUFFICIENT_EVIDENCE when even the incident/code evidence is too thin to analyze.
6. Use wording like "Commit abc1234 is the strongest regression candidate because…"
   NOT "commit abc1234 caused the incident" unless evidence is strong; prefer
   "is consistent with causing" for anything short of LIKELY.
7. Include evidence AGAINST your hypothesis when present (e.g., failing code predates the commit).
8. confidence reflects strength of the causal case, not severity of the bug.

Respond with a single JSON object:
{
  "status": "LIKELY_REGRESSION|POSSIBLE_REGRESSION|NO_CONFIDENT_REGRESSION|INSUFFICIENT_EVIDENCE",
  "likely_commit": "<short_sha|null>",
  "causal_explanation": "...",
  "evidence_for": [{"commit_sha":"...","file_path":"...","start_line":10,"end_line":20,"description":"..."}],
  "evidence_against": [{"commit_sha":"...","file_path":"...","description":"..."}],
  "confidence": 0-100,
  "unknowns": ["..."],
  "recommended_action": "human review step, prose only"
}
"""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def build_regression_prompt(
    *,
    incident_lines: list[str],
    first_occurrence: str | None,
    v1_summary: str | None,
    code_blocks: list[dict],
    candidates: list[ChangeCandidate],
    max_diff_chars_total: int = 9000,
) -> str:
    parts: list[str] = ["## INCIDENT"]
    parts.extend(incident_lines)
    if first_occurrence:
        parts.append(f"First observed error: {first_occurrence}")
    if v1_summary:
        parts.append(f"V1 RCA summary: {v1_summary}")

    parts.append("")
    parts.append("## V1 CODE EVIDENCE")
    if not code_blocks:
        parts.append("(none)")
    for i, block in enumerate(code_blocks[:3], start=1):
        parts.append(f"[C{i}] {block['path']}:{block['start_line']}-{block['end_line']}")
        parts.append("```")
        parts.append(_clip(block.get("content", ""), 700))
        parts.append("```")

    parts.append("")
    parts.append("## CANDIDATE COMMITS (ranked deterministically by the system)")
    budget = max_diff_chars_total
    for i, cand in enumerate(candidates[:5], start=1):
        parts.append(f"[G{i}] {cand.short_sha} score={cand.score}")
        parts.append(f'Message: "{cand.message or ""}"')
        if cand.author:
            parts.append(f"Author: {cand.author}")
        if cand.committed_at:
            parts.append(f"Committed at: {cand.committed_at}")
        parts.append(f"Changed files: {', '.join(cand.changed_files[:8]) or '(none)'}")
        parts.append(f"System signals: {', '.join(cand.signals.keys()) or 'none'}")

        remaining_hunks = [h for h in cand.hunks if h.get("patch")]
        if remaining_hunks and budget > 300:
            parts.append("Diff:")
            for h in remaining_hunks[:3]:
                patch = _clip(h["patch"], min(1200, max(300, budget // 3)))
                budget -= len(patch)
                parts.append(f"--- {h['file_path']} @@ -{h['old_start']},{h['old_lines']} +{h['new_start']},{h['new_lines']}")
                parts.append("```")
                parts.append(patch)
                parts.append("```")
                if budget <= 300:
                    break
        else:
            parts.append("Diff: (not available / filtered)")
        parts.append("")

    parts.append(
        "Analyze causality using ONLY the above. Label FACT/INFERENCE/HYPOTHESIS inside "
        "your explanation where useful. Respond with the JSON object now."
    )
    return "\n".join(parts)


__all__ = ["SYSTEM_PROMPT_V2", "build_regression_prompt"]
