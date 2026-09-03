"""Deterministic candidate commit generation and ranking.

Signals (all explainable, weighted):
  A stack_file_match     +3.0  commit touched a stack-trace file
  B retrieved_file_match +2.5  commit touched a V1-retrieved file
  C symbol_match         +2.0  diff patch mentions failing/retrieved symbols
  D line_proximity       +1.5  stack frame line near a hunk in that file
  E temporal_proximity   up to +1.0  closer to first occurrence (never sufficient alone)
  F diff_relevance       up to +1.5  exception tokens appear in patch text
  G change_type M/A boost small preference for Modified files

Commits committed AFTER the incident are context-only (score 0, never causal).
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.git import GitChangeHunk, GitCommit, GitFileChange
from app.schemas.regression import ChangeCandidate
from app.schemas.retrieval import RetrievalResult
from app.schemas.sentry import SentryEventData, SentryIssueData

W_STACK_FILE = 3.0
W_RETRIEVED_FILE = 2.5
W_SYMBOL = 2.0
W_LINE_PROXIMITY = 1.5
W_TEMPORAL_MAX = 1.0
W_DIFF_RELEVANCE_PER = 0.5
W_DIFF_RELEVANCE_CAP = 1.5

_LINE_WINDOW = 15  # hunk proximity window in lines


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _ensure_aware(dt):
    from datetime import timezone

    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text or "")}


class CandidateGenerator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def generate(
        self,
        *,
        repository_id: int,
        issue: SentryIssueData | None,
        event: SentryEventData | None,
        retrieval_results: list[RetrievalResult],
        incident_time,
        window_hours: int,
        max_candidates: int,
    ) -> tuple[list[ChangeCandidate], list[ChangeCandidate]]:
        """Returns (causal_candidates_sorted, context_only_after_incident)."""
        if incident_time is None:
            return [], []

        incident_time = _ensure_aware(incident_time)
        window_start = incident_time - timedelta(hours=window_hours)
        commits = self._db.scalars(
            select(GitCommit)
            .where(
                GitCommit.repository_id == repository_id,
                GitCommit.committed_at >= window_start,
                GitCommit.committed_at <= incident_time,
            )
            .order_by(GitCommit.committed_at.desc())
        ).all()

        after = self._db.scalars(
            select(GitCommit)
            .where(
                GitCommit.repository_id == repository_id,
                GitCommit.committed_at > incident_time,
            )
            .order_by(GitCommit.committed_at.desc())
            .limit(10)
        ).all()

        stack_paths = {
            (f.filename or "").replace("\\", "/"): _basename(f.filename or "")
            for f in (event.frames if event else [])
            if f.filename
        }
        stack_lines = {
            _basename(f.filename): f.line_number
            for f in (event.frames if event else [])
            if f.filename and f.line_number
        }
        retrieved_paths = {r.relative_path: r for r in retrieval_results}
        symbols = set()
        if event:
            symbols |= {f.function for f in event.frames if f.function}
        for r in retrieval_results:
            if r.symbol_name:
                symbols.add(r.symbol_name)
        symbols = {s for s in symbols if s and len(s) >= 4}

        exc_text = " ".join(
            filter(None, [
                issue.title if issue else "",
                " ".join(e.value or "" for e in (event.exceptions if event else [])),
                event.message if event else "",
            ])
        )
        exc_tokens = _tokens(exc_text)

        candidates: list[ChangeCandidate] = []
        context_only: list[ChangeCandidate] = []

        def build(commit: GitCommit, causal: bool) -> ChangeCandidate:
            file_changes = self._db.scalars(
                select(GitFileChange).where(GitFileChange.commit_id == commit.id)
            ).all()
            paths = [fc.file_path for fc in file_changes]
            basenames = {_basename(p) for p in paths}

            signals: dict[str, float] = {}
            score = 0.0
            reasons: list[str] = []

            # A. stack file match
            hit_stack = basenames & set(stack_paths.values())
            if hit_stack:
                signals["stack_file_match"] = W_STACK_FILE
                score += W_STACK_FILE
                reasons.append(f"modified failing file {sorted(hit_stack)[0]}")

            # B. retrieved file match
            hit_retrieved = {
                rp for rp in retrieved_paths
                if _basename(rp) in basenames or any(rp.endswith(p) for p in paths)
            }
            if hit_retrieved:
                signals["retrieved_file_match"] = W_RETRIEVED_FILE
                score += W_RETRIEVED_FILE
                reasons.append(f"modified retrieved code {sorted(hit_retrieved)[0]}")

            # C/D/F need hunks
            hunks = self._db.scalars(
                select(GitChangeHunk).where(GitChangeHunk.commit_id == commit.id)
            ).all()
            symbol_hit = False
            proximity_hit = False
            relevance_tokens: set[str] = set()
            patch_blob = "\n".join(h.patch or "" for h in hunks).lower()

            for token in symbols:
                if token.lower() in patch_blob or any(
                    token.lower() in p.lower() for p in paths
                ):
                    symbol_hit = True
                    break

            for bp, line_no in stack_lines.items():
                matching_files = [h for h in hunks if _basename(h.file_path) == bp]
                for h in matching_files:
                    lo = h.new_start - _LINE_WINDOW
                    hi = h.new_start + h.new_lines + _LINE_WINDOW
                    if lo <= line_no <= hi:
                        proximity_hit = True
                        break

            if symbol_hit:
                signals["symbol_match"] = W_SYMBOL
                score += W_SYMBOL
                reasons.append("changed code touching failing symbol")
            if proximity_hit:
                signals["line_proximity"] = W_LINE_PROXIMITY
                score += W_LINE_PROXIMITY
                reasons.append("changed lines near the failing location")

            # F. diff content relevance vs exception tokens
            for tok in exc_tokens:
                if tok in patch_blob:
                    relevance_tokens.add(tok)
            rel_score = min(W_DIFF_RELEVANCE_PER * len(relevance_tokens), W_DIFF_RELEVANCE_CAP)
            if rel_score:
                signals["diff_relevance"] = round(rel_score, 2)
                score += rel_score
                reasons.append(f"diff mentions {sorted(relevance_tokens)[:3]}")

            # G. modified-file preference (small)
            modified = sum(1 for fc in file_changes if fc.change_type == "M")
            if modified:
                bonus = 0.25 * min(modified, 4)
                signals["modified_files"] = round(bonus, 2)
                score += bonus

            # E. temporal proximity — decay over the window; never sufficient alone
            committed_at = _ensure_aware(commit.committed_at)
            if committed_at is not None and causal and incident_time is not None:
                total_seconds = max(1.0, window_hours * 3600)
                age = (incident_time - committed_at).total_seconds()
                recency = max(0.0, 1.0 - (age / total_seconds))
                t_score = round(W_TEMPORAL_MAX * recency, 2)
                signals["temporal_proximity"] = t_score
                score += t_score
                minutes = int(age // 60)
                reasons.append(f"committed {minutes}m before first observed error")

            return ChangeCandidate(
                commit_sha=commit.commit_sha,
                short_sha=commit.short_sha,
                message=commit.message,
                author=commit.author_name,
                committed_at=committed_at.isoformat() if committed_at else None,
                changed_files=paths,
                score=round(score, 2),
                signals=signals,
                relevance_reason="; ".join(reasons),
                context_only=not causal,
            )

        for commit in commits:
            candidates.append(build(commit, causal=True))
        for commit in after:
            context_only.append(build(commit, causal=False))

        candidates.sort(key=lambda c: c.score, reverse=True)
        top = [c for c in candidates if c.score > 0][:max_candidates]
        zero = [c for c in candidates if c.score <= 0]
        return top, context_only + zero[:5]
