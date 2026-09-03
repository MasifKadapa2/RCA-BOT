"""Strict Git evidence validation for V2.

Philosophy identical to V1: the LLM cannot create evidence.
Every Git reference is checked against the indexed repository data and the
candidate set that was actually sent to the LLM. Invalid references are
rejected — never silently repaired.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.git import GitChangeHunk, GitCommit, GitFileChange
from app.schemas.regression import ChangeCandidate, GitEvidenceRef

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass
class RegressionValidationReport:
    valid_evidence_for: list[GitEvidenceRef] = field(default_factory=list)
    invalid_evidence_for: list[dict] = field(default_factory=list)
    valid_evidence_against: list[GitEvidenceRef] = field(default_factory=list)
    invalid_evidence_against: list[dict] = field(default_factory=list)
    likely_commit_valid: bool = False

    @property
    def has_invalid(self) -> bool:
        return bool(self.invalid_evidence_for or self.invalid_evidence_against)


class RegressionValidator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def validate(
        self,
        *,
        likely_commit: str | None,
        evidence_for: list[GitEvidenceRef],
        evidence_against: list[GitEvidenceRef],
        candidates: list[ChangeCandidate],
        repository_id: int,
    ) -> tuple[list[GitEvidenceRef], list[GitEvidenceRef], RegressionValidationReport]:
        report = RegressionValidationReport()
        candidate_shas = {c.commit_sha for c in candidates}
        candidate_shorts = {c.short_sha for c in candidates}

        def _resolve(sha: str) -> GitCommit | None:
            cleaned = (sha or "").strip().lower()
            if not _SHA_RE.match(cleaned):
                return None
            by_full = self._db.scalars(
                select(GitCommit).where(
                    GitCommit.repository_id == repository_id,
                    GitCommit.commit_sha == cleaned,
                )
            ).first()
            if by_full is not None:
                return by_full
            by_short = self._db.scalars(
                select(GitCommit).where(
                    GitCommit.repository_id == repository_id,
                    GitCommit.short_sha == cleaned[:7],
                )
            ).first()
            if by_short is not None:
                return by_short
            # Prefix match (8+ hex chars) within this repository.
            if len(cleaned) >= 8:
                rows = self._db.scalars(
                    select(GitCommit).where(GitCommit.repository_id == repository_id)
                ).all()
                for row in rows:
                    if row.commit_sha.startswith(cleaned):
                        return row
            return None

        def _validate_ref(ref: GitEvidenceRef) -> tuple[bool, str | None]:
            commit = _resolve(ref.commit_sha)
            if commit is None:
                return False, "commit not found in indexed repository"
            if candidate_shas and commit.commit_sha not in candidate_shas \
                    and commit.short_sha not in candidate_shorts:
                return False, "commit was not part of candidate analysis"
            fc = self._db.scalars(
                select(GitFileChange).where(
                    GitFileChange.commit_id == commit.id,
                    GitFileChange.file_path == ref.file_path.strip().lstrip("./"),
                )
            ).first()
            if fc is None:
                # allow renamed target path
                fc = self._db.scalars(
                    select(GitFileChange).where(
                        GitFileChange.commit_id == commit.id,
                        GitFileChange.old_path == ref.file_path.strip().lstrip("./"),
                    )
                ).first()
            if fc is None:
                return False, f"file {ref.file_path!r} not changed in this commit"

            if ref.start_line is not None or ref.end_line is not None:
                hunks = self._db.scalars(
                    select(GitChangeHunk).where(
                        GitChangeHunk.commit_id == commit.id,
                        GitChangeHunk.file_change_id == fc.id,
                    )
                ).all()
                if not hunks:
                    return False, "no indexed hunk for this file/commit"
                start = ref.start_line or ref.end_line
                end = ref.end_line or ref.start_line
                inside = any(
                    h.new_start - 5 <= start and end <= h.new_start + h.new_lines + 5
                    for h in hunks
                )
                if not inside:
                    return False, "line range outside indexed diff hunks"
            return True, None

        for ref in evidence_for or []:
            # reject obvious path traversal early
            if ".." in Path_parts(ref.file_path):
                report.invalid_evidence_for.append(_invalid(ref, "path traversal"))
                continue
            ok, reason = _validate_ref(ref)
            if ok:
                report.valid_evidence_for.append(ref)
            else:
                report.invalid_evidence_for.append(_invalid(ref, reason))
                logger.warning("rca_bot.invalid_git_evidence",
                               extra={"sha": ref.commit_sha, "path": ref.file_path,
                                      "reason": reason})

        for ref in evidence_against or []:
            if ".." in Path_parts(ref.file_path):
                report.invalid_evidence_against.append(_invalid(ref, "path traversal"))
                continue
            ok, reason = _validate_ref(ref)
            if ok:
                report.valid_evidence_against.append(ref)
            else:
                report.invalid_evidence_against.append(_invalid(ref, reason))

        if likely_commit:
            lc_clean = likely_commit.strip().lower()
            report.likely_commit_valid = (
                lc_clean in candidate_shas or lc_clean in candidate_shorts
            ) and any(
                r.commit_sha.lower().startswith(lc_clean[:7])
                or lc_clean.startswith(r.commit_sha[:7])
                for r in report.valid_evidence_for
            )
        return report.valid_evidence_for, report.valid_evidence_against, report


def Path_parts(path: str) -> list[str]:
    from pathlib import PurePosixPath

    return list(PurePosixPath((path or "").replace("\\", "/")).parts)


def _invalid(ref: GitEvidenceRef, reason: str | None) -> dict:
    return {
        "commit_sha": ref.commit_sha,
        "file_path": ref.file_path,
        "start_line": ref.start_line,
        "end_line": ref.end_line,
        "reason": reason or "invalid",
    }
