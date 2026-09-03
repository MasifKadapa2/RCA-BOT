"""Incremental Git history indexer (V2 Change Intelligence)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.git.client import GitClient, NotAGitRepositoryError
from app.git.parser import parse_iso, parse_unified_diff
from app.ingestion.filters import FileFilter  # V1 secret/ignore patterns reused
from app.models import Repository
from app.models.git import GitChangeHunk, GitCommit, GitFileChange, GitIndexState

logger = logging.getLogger(__name__)

_sensitive_filter = FileFilter()


def _is_sensitive_path(path: str) -> bool:
    """Secrets must never enter the diff store (V1 filter rules reused)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if _sensitive_filter.is_ignored_file(name):
        return True
    lowered = path.lower()
    return any(marker in lowered for marker in (".env", "secret", "credential"))


@dataclass
class GitIndexStats:
    commits_indexed: int = 0
    new_commits: int = 0
    skipped_commits: int = 0
    files_changed: int = 0
    hunks_indexed: int = 0
    truncated_diffs: int = 0


def get_or_create_git_client(repository_path: str) -> GitClient:
    return GitClient(repository_path)


class GitIndexService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def index(self, db: Session, repository_path: str | None = None) -> tuple[GitIndexStats, Repository]:
        repo_path = repository_path or self._settings.repository_path
        if not repo_path:
            from app.core.errors import RepositoryNotFoundError

            raise RepositoryNotFoundError("REPOSITORY_PATH is not configured")

        client = get_or_create_git_client(repo_path)

        repo_row = db.scalars(
            select(Repository).where(Repository.path == str(client.repo_root))
        ).first()
        if repo_row is None:
            repo_row = Repository(name=client.repo_root.name, path=str(client.repo_root))
            db.add(repo_row)
            db.flush()
        repository_id = repo_row.id

        existing_shas = set(
            db.scalars(
                select(GitCommit.commit_sha).where(GitCommit.repository_id == repository_id)
            )
        )

        stats = GitIndexStats()
        raw_commits = client.fetch_commits(
            since_days=self._settings.git_history_days,
            max_commits=self._settings.git_max_commits,
        )
        stats.commits_indexed = len(raw_commits)

        max_commit_diff = self._settings.max_commit_diff_chars
        max_patch = self._settings.max_patch_chars

        for raw in raw_commits:
            if raw.sha in existing_shas:
                stats.skipped_commits += 1
                continue

            commit_row = GitCommit(
                repository_id=repository_id,
                commit_sha=raw.sha,
                short_sha=raw.short_sha,
                author_name=raw.author_name,
                author_email=raw.author_email,
                authored_at=parse_iso(raw.authored_at),
                committed_at=parse_iso(raw.committed_at),
                message=raw.message,
                parent_sha=" ".join(raw.parents) or None,
                is_merge_commit=len(raw.parents) > 1,
                files_changed_count=len(raw.file_changes),
            )
            db.add(commit_row)
            db.flush()

            # Bounded per-commit diff for hunk storage.
            diff_text = ""
            try:
                diff_text = client.commit_diff(raw.sha, max_commit_diff)
            except Exception as exc:  # never fail whole index on one commit
                logger.warning("git_diff_failed", extra={"sha": raw.short_sha, "error": str(exc)})
            diff_result = parse_unified_diff(diff_text, max_patch) if diff_text else None
            if diff_result and diff_result.truncated:
                stats.truncated_diffs += 1

            hunks_by_file: dict[str, list] = {}
            if diff_result:
                for hunk in diff_result.hunks:
                    hunks_by_file.setdefault(hunk.file_path, []).append(hunk)

            for change in raw.file_changes:
                # Security: never persist metadata or diffs for secret files.
                if (_is_sensitive_path(change["path"])
                        or (change.get("old_path") and _is_sensitive_path(change["old_path"]))):
                    continue
                file_row = GitFileChange(
                    commit_id=commit_row.id,
                    file_path=change["path"],
                    old_path=change.get("old_path"),
                    change_type=change["change_type"],
                    additions=None,
                    deletions=None,
                )
                db.add(file_row)
                db.flush()
                stats.files_changed += 1

                for hunk in hunks_by_file.get(change["path"], []):
                    db.add(
                        GitChangeHunk(
                            commit_id=commit_row.id,
                            file_change_id=file_row.id,
                            file_path=hunk.file_path,
                            old_start=hunk.old_start,
                            old_lines=hunk.old_lines,
                            new_start=hunk.new_start,
                            new_lines=hunk.new_lines,
                            patch=hunk.patch,
                            truncated=hunk.truncated,
                        )
                    )
                    stats.hunks_indexed += 1

            stats.new_commits += 1

        state = db.scalars(
            select(GitIndexState).where(GitIndexState.repository_id == repository_id)
        ).first()
        total = (
            db.scalar(
                select(GitCommit.id).where(GitCommit.repository_id == repository_id).limit(1)
            )
            is not None
        )
        count_existing = len(existing_shas) + stats.new_commits
        if state is None:
            state = GitIndexState(repository_id=repository_id)
            db.add(state)
        state.last_indexed_at = datetime.now(UTC)
        state.commits_indexed = count_existing
        state.head_sha = client.head_sha() if total or stats.new_commits else client.head_sha()
        db.commit()

        logger.info("rca_bot.git_index_completed", extra=stats.__dict__)
        return stats, repo_row

    def purge_removed(self, db: Session, repository_path: str) -> int:
        """Remove commits no longer reachable (e.g., after rebase/history rewrite)."""
        client = get_or_create_git_client(repository_path)
        repo_row = db.scalars(
            select(Repository).where(Repository.path == str(client.repo_root))
        ).first()
        if repo_row is None:
            return 0
        reachable = {c.sha for c in client.fetch_commits(
            since_days=self._settings.git_history_days * 4,
            max_commits=self._settings.git_max_commits,
        )}
        rows = db.scalars(
            select(GitCommit).where(GitCommit.repository_id == repo_row.id)
        ).all()
        removed = 0
        for row in rows:
            if row.commit_sha not in reachable:
                db.delete(row)
                removed += 1
        if removed:
            db.commit()
        return removed


__all__ = ["GitIndexService", "GitIndexStats", "get_or_create_git_client",
           "NotAGitRepositoryError"]
