"""ChangeAnalyzer — V2 Change Intelligence orchestrator.

Extends (never replaces) the V1 pipeline. Runs after the V1 RCA has been
persisted; any Git failure degrades gracefully without touching V1 results.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import LLMError
from app.change_intelligence.candidate_generator import CandidateGenerator
from app.git.client import GitClient, NotAGitRepositoryError
from app.git.parser import parse_iso
from app.investigation.regression_validator import RegressionValidator
from app.llm.provider import LLMProvider
from app.models import Repository
from app.models.git import GitChangeHunk, GitCommit
from app.schemas.regression import (
    ChangeCandidate,
    RegressionAnalysis,
    RegressionStatus,
)
from app.schemas.retrieval import RetrievalResult
from app.schemas.sentry import SentryEventData, SentryIssueData

logger = logging.getLogger(__name__)


@dataclass
class ChangeIntelResult:
    analysis: RegressionAnalysis | None = None
    unavailable_reason: str | None = None
    git_detected: bool = False
    latencies: dict[str, int] = field(default_factory=dict)


class ChangeAnalyzer:
    def __init__(
        self,
        settings: Settings,
        llm_provider: LLMProvider,
        session_factory,
    ) -> None:
        self._settings = settings
        self._llm = llm_provider
        self._session_factory = session_factory

    # --- Public ------------------------------------------------------------------

    def analyze(
        self,
        *,
        repository_path: str,
        issue: SentryIssueData | None,
        event: SentryEventData | None,
        retrieval_results: list[RetrievalResult],
        v1_summary: str | None,
    ) -> ChangeIntelResult:
        result = ChangeIntelResult()
        total_start = time.perf_counter()

        # 0. Git detection — never raise into V1 flow.
        try:
            client = GitClient(repository_path)
            result.git_detected = True
        except NotAGitRepositoryError as exc:
            result.unavailable_reason = str(exc)
            return result

        incident_time = self._incident_time(issue, event)

        # 1. Resolve repository row + candidate generation.
        t0 = time.perf_counter()
        with self._session_factory() as db:
            repo_row = db.scalars(
                select(Repository).where(Repository.path == str(client.repo_root))
            ).first()
            if repo_row is None or not self._has_git_data(db, repo_row.id):
                result.unavailable_reason = "Git history is not indexed yet."
                result.latencies["git_candidate_latency_ms"] = int(
                    (time.perf_counter() - t0) * 1000
                )
                return result

            generator = CandidateGenerator(db)
            causal, context_only = generator.generate(
                repository_id=repo_row.id,
                issue=issue,
                event=event,
                retrieval_results=retrieval_results,
                incident_time=incident_time,
                window_hours=self._settings.change_window_hours,
                max_candidates=self._settings.max_change_candidates,
            )
        result.latencies["git_candidate_latency_ms"] = int((time.perf_counter() - t0) * 1000)

        if not causal and not retrieval_results:
            result.analysis = RegressionAnalysis(
                status=RegressionStatus.INSUFFICIENT_EVIDENCE,
                causal_explanation=(
                    "Insufficient Sentry/code evidence to perform change analysis."
                ),
                candidate_commits=context_only[:5],
                unknowns=["No stack frames or retrieved code connected the incident to files."],
                recommended_action="Confirm the Sentry event contains a stack trace with file paths.",
            )
            return result
        if not causal:
            result.analysis = RegressionAnalysis(
                status=RegressionStatus.NO_CONFIDENT_REGRESSION,
                causal_explanation=(
                    "No relevant recent changes identified inside the change window."
                ),
                candidate_commits=context_only[:5],
                unknowns=[
                    "No commit in the change window touched the failing or retrieved files."
                ],
                recommended_action="Widen CHANGE_WINDOW_HOURS or investigate non-code causes.",
            )
            return result

        # 2. Attach bounded diffs to top candidates.
        t1 = time.perf_counter()
        top = causal[: min(5, len(causal))]
        for cand in top:
            cand.hunks = self._hunks_for(client, cand.commit_sha)
        result.latencies["diff_analysis_latency_ms"] = int((time.perf_counter() - t1) * 1000)

        # 3. Causal LLM reasoning over bounded context.
        t2 = time.perf_counter()
        from app.llm.prompts_v2 import SYSTEM_PROMPT_V2, build_regression_prompt

        code_blocks = [
            {"path": r.relative_path, "start_line": r.start_line,
             "end_line": r.end_line, "content": r.content}
            for r in retrieval_results[:3]
        ]
        first_occurrence = (
            issue.last_seen.isoformat() if issue and issue.last_seen
            else (event.timestamp.isoformat() if event and event.timestamp else None)
        )
        prompt = build_regression_prompt(
            incident_lines=self._incident_lines(issue, event),
            first_occurrence=first_occurrence,
            v1_summary=v1_summary,
            code_blocks=code_blocks,
            candidates=top,
            max_diff_chars_total=max(3000, self._settings.max_diff_chars // 2),
        )
        try:
            draft = self._llm.generate_structured(
                prompt,
                schema=RegressionAnalysis,
                system=SYSTEM_PROMPT_V2,
            )
        except LLMError as exc:
            logger.warning("rca_bot.causal_llm_failed", extra={"error": str(exc)})
            result.unavailable_reason = f"Causal LLM analysis failed: {exc}"
            result.latencies["causal_llm_latency_ms"] = int((time.perf_counter() - t2) * 1000)
            result.analysis = self._deterministic_fallback(top, context_only)
            return result
        result.latencies["causal_llm_latency_ms"] = int((time.perf_counter() - t2) * 1000)

        # 4. Strict validation of every Git reference.
        t3 = time.perf_counter()
        with self._session_factory() as db:
            repo_row = db.scalars(
                select(Repository).where(Repository.path == str(client.repo_root))
            ).first()
            validator = RegressionValidator(db)
            valid_for, valid_against, report = validator.validate(
                likely_commit=draft.likely_commit,
                evidence_for=list(draft.evidence_for),
                evidence_against=list(draft.evidence_against),
                candidates=top,
                repository_id=repo_row.id if repo_row else 0,
            )
        result.latencies["regression_validation_latency_ms"] = int(
            (time.perf_counter() - t3) * 1000
        )

        status = draft.status
        likely = draft.likely_commit
        if status == RegressionStatus.LIKELY_REGRESSION and not report.likely_commit_valid:
            status = RegressionStatus.POSSIBLE_REGRESSION
            likely = None
            draft.unknowns.append(
                "Likely-commit claim failed strict validation; downgraded to POSSIBLE_REGRESSION."
            )

        analysis = draft.model_copy(update={
            "status": status,
            "likely_commit": likely if report.likely_commit_valid else None,
            "evidence_for": valid_for,
            "evidence_against": valid_against,
            "candidate_commits": top + context_only[:3],
            "confidence": max(0, min(100, int(draft.confidence))),
        })
        result.analysis = analysis
        result.latencies["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
        logger.info("rca_bot.change_intel_completed",
                    extra={"status": str(status), "candidates": len(top)})
        return result

    # --- Helpers -----------------------------------------------------------------

    def _has_git_data(self, db: Session, repository_id: int) -> bool:
        return db.scalars(
            select(GitCommit.id).where(GitCommit.repository_id == repository_id).limit(1)
        ).first() is not None

    def _hunks_for(self, client: GitClient, sha: str) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            commit = db.scalars(
                select(GitCommit).where(GitCommit.commit_sha == sha)
            ).first()
            if commit is None:
                return []
            hunks = db.scalars(
                select(GitChangeHunk).where(GitChangeHunk.commit_id == commit.id)
            ).all()
        return [
            {
                "file_path": h.file_path,
                "old_start": h.old_start, "old_lines": h.old_lines,
                "new_start": h.new_start, "new_lines": h.new_lines,
                "patch": h.patch,
                "truncated": h.truncated,
            }
            for h in hunks
        ]

    @staticmethod
    def _incident_time(issue: SentryIssueData | None, event: SentryEventData | None):
        if event and event.timestamp:
            return event.timestamp
        if issue and issue.first_seen:
            return issue.first_seen
        return parse_iso(issue.last_seen if issue else None)

    @staticmethod
    def _incident_lines(issue: SentryIssueData | None, event: SentryEventData | None) -> list[str]:
        lines = []
        if issue and issue.title:
            lines.append(f"Sentry: {issue.title}")
        if issue and issue.issue_id:
            lines.append(f"Issue id: {issue.issue_id}")
        for exc in (event.exceptions if event else [])[:2]:
            lines.append(f"Exception: {exc.type}: {exc.value}")
        for frame in (event.frames if event else [])[:6]:
            loc = frame.filename or frame.module or "(unknown)"
            line = f"Stack: {loc}"
            if frame.line_number:
                line += f":{frame.line_number}"
            if frame.function:
                line += f" in {frame.function}"
            lines.append(line)
        return lines or ["(limited incident information)"]

    @staticmethod
    def _deterministic_fallback(top: list[ChangeCandidate],
                                context_only: list[ChangeCandidate]) -> RegressionAnalysis:
        if top:
            return RegressionAnalysis(
                status=RegressionStatus.POSSIBLE_REGRESSION,
                candidate_commits=top + context_only[:3],
                causal_explanation=(
                    "LLM causal analysis was unavailable; candidates are ranked by "
                    "deterministic signals only."
                ),
                unknowns=["Causal reasoning model did not respond."],
                recommended_action="Review diffs of the ranked candidates manually.",
            )
        return RegressionAnalysis(
            status=RegressionStatus.NO_CONFIDENT_REGRESSION,
            candidate_commits=context_only[:3],
            causal_explanation="No relevant recent changes identified.",
            unknowns=["Causal reasoning model did not respond."],
            recommended_action="Widen the change window or inspect recent deployments manually.",
        )


def build_timeline(
    analysis: RegressionAnalysis | None,
    issue_first_seen=None,
    issue_last_seen=None,
    investigation_created_at=None,
    investigation_completed_at=None,
) -> list[dict[str, Any]]:
    """Chronological timeline using ONLY data we actually have.

    Commit entries are always labeled as commits — never as deployments.
    """
    entries: list[dict[str, Any]] = []

    def add(time_val, label: str, detail: str = ""):
        if time_val:
            entries.append({"time": time_val, "label": label, "detail": detail})

    if analysis:
        for cand in analysis.candidate_commits:
            if cand.committed_at and not cand.context_only:
                add(cand.committed_at, f"Commit created {cand.short_sha}",
                    cand.message or "")
    add(issue_first_seen, "First Sentry occurrence")
    add(issue_last_seen, "Last Sentry occurrence")
    add(investigation_created_at, "Investigation started")
    add(investigation_completed_at, "Investigation completed")

    parsed = []
    for e in entries:
        t = e["time"]
        if isinstance(t, str):
            from datetime import datetime

            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                continue
        parsed.append(({**e, "time": t}, t))
    parsed.sort(key=lambda pair: pair[1])
    return [
        {"time": e["time"].isoformat(), "label": e["label"], "detail": e["detail"]}
        for e, _ in parsed
    ]
