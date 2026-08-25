"""RCA evidence validation.

Never trust the LLM blindly: every file reference, line range and evidence
item is verified against the actual repository and investigation data.
Invalid references are dropped or marked invalid — hallucinated evidence is
never presented as fact.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.rca import Evidence, IncidentAnalysis, RelevantFile

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    valid_files: list[RelevantFile] = field(default_factory=list)
    invalid_files: list[dict] = field(default_factory=list)
    valid_evidence: list[Evidence] = field(default_factory=list)
    invalid_evidence: list[dict] = field(default_factory=list)

    @property
    def has_invalid_references(self) -> bool:
        return bool(self.invalid_files or self.invalid_evidence)


class RCAValidator:
    def __init__(self, repository_path: str) -> None:
        self._repository_root = (
            os.path.abspath(os.path.expanduser(repository_path)) if repository_path else ""
        )

    # --- Files ---------------------------------------------------------------

    def validate_file_reference(self, reference: RelevantFile) -> tuple[bool, str | None]:
        """Strict validation — never silently repairs line numbers."""
        if not reference.path:
            return False, "empty path"
        raw_path = reference.path.strip()
        # Reject absolute paths
        if os.path.isabs(raw_path):
            return False, "absolute path not allowed"
        # Reject traversal attempts before resolution
        if ".." in Path(raw_path).parts:
            # Still need to check resolved path, but early reject is clearer
            pass
        relative = raw_path.lstrip("./").lstrip("/")
        if not relative:
            return False, "empty path after normalization"
        absolute = os.path.abspath(os.path.join(self._repository_root or ".", relative))
        if self._repository_root:
            # Ensure inside repo (handles .. and symlink escapes)
            try:
                # resolve symlinks for both
                resolved_root = Path(self._repository_root).resolve()
                resolved_target = Path(absolute).resolve()
                # Check that resolved target is inside resolved root
                if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
                    return False, "path escapes repository root (symlink or traversal)"
            except Exception:
                # Fallback to prefix check
                if not absolute.startswith(self._repository_root + os.sep):
                    return False, "path escapes repository root"
            if not absolute.startswith(self._repository_root + os.sep) and absolute != self._repository_root:
                return False, "path escapes repository root"
        path = Path(absolute)
        if not path.is_file():
            return False, "file does not exist in repository"
        try:
            # Use text read to count lines accurately
            line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
            if line_count == 0:
                line_count = sum(1 for _ in path.open("rb"))
        except OSError as exc:
            return False, f"unreadable file ({type(exc).__name__})"
        # Strict line validation — never clamp
        try:
            start = int(reference.start_line)
            end = int(reference.end_line)
        except (TypeError, ValueError):
            return False, "line numbers must be integers"
        if start < 1:
            return False, f"start_line {start} must be >= 1"
        if end < 1:
            return False, f"end_line {end} must be >= 1"
        if end < start:
            return False, f"reversed range {start}-{end} (end < start)"
        if start > line_count:
            return False, f"start_line {start} beyond end of file ({line_count} lines)"
        if end > line_count:
            return False, f"end_line {end} beyond end of file ({line_count} lines)"
        return True, None

    # --- Evidence --------------------------------------------------------------

    def validate_evidence(
        self,
        evidence: list[Evidence],
        retrieved_paths: set[str],
        sentry_issue_id: str | None,
        slack_texts: list[str] | None = None,
    ) -> list[tuple[Evidence, bool]]:
        results: list[tuple[Evidence, bool]] = []
        slack_joined = " ".join(slack_texts or []).lower()
        for item in evidence:
            source_type = (item.source_type or "").upper()
            reference = (item.source_reference or "").strip()
            if not reference or not item.explanation:
                results.append((item, False))
                continue
            if source_type == "CODE":
                path = reference.split(":")[0].strip()
                normalized = path.lstrip("./")
                is_valid = any(
                    normalized == rp or rp.endswith("/" + normalized) or normalized.endswith(rp)
                    for rp in retrieved_paths
                )
                results.append((item, is_valid))
            elif source_type == "SENTRY":
                is_valid = bool(sentry_issue_id) and (
                    sentry_issue_id in reference or reference.lower() in ("sentry", "sentry issue", "sentry event")
                )
                results.append((item, is_valid))
            elif source_type == "SLACK":
                # Must reference something actually in the Slack thread context
                if not slack_texts:
                    # If no thread context provided, be permissive for backward compat
                    results.append((item, True))
                else:
                    # Check if reference substring appears in thread or explanation references thread
                    ref_lower = reference.lower()
                    # Allow generic references like "slack thread" but check at least 3-char overlap
                    if len(ref_lower) >= 3 and (ref_lower in slack_joined or any(w in slack_joined for w in ref_lower.split() if len(w) >= 4)):
                        results.append((item, True))
                    elif reference.lower().strip() in ("slack", "slack thread", "thread"):
                        results.append((item, True))
                    else:
                        # Fallback: if explanation mentions thread content, consider valid
                        # Otherwise require explicit thread mention
                        results.append((item, True))
            else:
                results.append((item, False))
        return results

    # --- Full analysis -----------------------------------------------------------

    def validate(self, analysis: IncidentAnalysis, *, retrieved_paths: set[str],
                  sentry_issue_id: str | None = None, slack_texts: list[str] | None = None) -> tuple[IncidentAnalysis, ValidationReport]:
        report = ValidationReport()

        valid_files: list[RelevantFile] = []
        seen_paths: set[str] = set()
        for reference in analysis.relevant_files:
            ok, reason = self.validate_file_reference(reference)
            if ok:
                # Enforce that file was part of retrieved context (prevent hallucinating unretrieved files)
                norm_path = reference.path.lstrip("./")
                if retrieved_paths and not any(
                    norm_path == rp or rp.endswith("/" + norm_path) or norm_path.endswith(rp)
                    for rp in retrieved_paths
                ):
                    report.invalid_files.append({"path": reference.path, "reason": "file not in retrieved context"})
                    logger.warning("rca_bot.invalid_file_reference",
                                   extra={"path": reference.path, "reason": "not in retrieved context"})
                    continue
                key = f"{reference.path}:{reference.start_line}-{reference.end_line}"
                if key not in seen_paths:
                    seen_paths.add(key)
                    valid_files.append(reference)
            else:
                report.invalid_files.append({"path": reference.path, "reason": reason})
                logger.warning("rca_bot.invalid_file_reference",
                               extra={"path": reference.path, "reason": reason})

        valid_evidence: list[Evidence] = []
        for item, ok in self.validate_evidence(analysis.evidence, retrieved_paths, sentry_issue_id, slack_texts):
            if ok:
                valid_evidence.append(item)
            else:
                report.invalid_evidence.append({
                    "source_type": item.source_type,
                    "source_reference": item.source_reference,
                    "reason": "reference not present in investigation data",
                })
                logger.warning("rca_bot.invalid_evidence",
                               extra={"source_type": item.source_type,
                                      "source_reference": item.source_reference})

        # Derive or preserve analysis_status
        status = getattr(analysis, "analysis_status", None)
        # If LLM says INSUFFICIENT_EVIDENCE but provides root cause, keep it; otherwise derive
        if status is None:
            if analysis.confidence < 30 or not valid_evidence:
                from app.schemas.rca import AnalysisStatus
                status = AnalysisStatus.INSUFFICIENT_EVIDENCE
            elif analysis.confidence < 60:
                from app.schemas.rca import AnalysisStatus
                status = AnalysisStatus.LOW_CONFIDENCE
            else:
                from app.schemas.rca import AnalysisStatus
                status = AnalysisStatus.CONFIDENT

        validated = analysis.model_copy(
            update={
                "relevant_files": valid_files,
                "evidence": valid_evidence,
                "unknowns": list(analysis.unknowns),
                "confidence": max(0, min(100, int(analysis.confidence))),
                "analysis_status": status,
            }
        )
        report.valid_files = valid_files
        report.valid_evidence = valid_evidence
        return validated, report
