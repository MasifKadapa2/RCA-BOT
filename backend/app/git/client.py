"""Safe Git access layer.

Rules enforced here (and nowhere else):
- executable is the fixed system ``git`` binary
- arguments are always structured lists (never shell strings)
- repository path is validated to exist and contain ``.git``
- every invocation has a timeout and bounded stdout
- no LLM-provided or user-provided command strings ever reach this layer
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import RepositoryNotFoundError


class NotAGitRepositoryError(RepositoryNotFoundError):
    """The configured path exists but is not a Git working tree."""


class GitCommandError(RuntimeError):
    pass


@dataclass
class CommitRaw:
    sha: str
    short_sha: str
    author_name: str | None
    author_email: str | None
    authored_at: str | None  # ISO-8601 strings; parsed later by parser.py
    committed_at: str | None
    parents: list[str] = field(default_factory=list)
    message: str = ""
    file_changes: list[dict] = field(default_factory=list)  # {change_type,path,old_path}


class GitClient:
    def __init__(self, repository_path: str) -> None:
        self._repo_root = Path(repository_path).expanduser().resolve()
        if not self._repo_root.is_dir():
            raise RepositoryNotFoundError(f"Repository path does not exist: {self._repo_root}")
        if not (self._repo_root / ".git").exists():
            raise NotAGitRepositoryError(
                f"Not a Git repository (no .git found): {self._repo_root}"
            )

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    # --- Low-level safe execution ----------------------------------------------

    def _run(self, args: list[str], timeout: int | None = None,
             max_output_chars: int = 4_000_000) -> str:
        settings = get_settings()
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(self._repo_root),
                capture_output=True,
                text=True,
                timeout=timeout or settings.git_timeout_seconds,
                shell=False,  # never a shell
                env={"GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(f"git timed out after {exc.timeout}s") from exc
        except FileNotFoundError as exc:  # pragma: no cover - git missing
            raise GitCommandError("git binary not found on PATH") from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or "")[:500]
            raise GitCommandError(f"git {' '.join(args[:2])}… failed: {stderr}")
        return (completed.stdout or "")[:max_output_chars]

    # --- High-level operations ---------------------------------------------------

    def is_git_repo(self) -> bool:  # convenience for callers that already hold a client
        return True

    def head_sha(self) -> str | None:
        out = self._run(["rev-parse", "HEAD"])
        return out.strip() or None

    def fetch_commits(self, since_days: int, max_commits: int) -> list[CommitRaw]:
        """Commits newest-first within the window, with name-status file lists."""
        record_sep = "\x00"   # git emits these bytes via %x00 / %x1f in the format
        unit_sep = "\x1f"
        fmt = (
            f"%x00%H%x1f%h%x1f%an%x1f%ae"
            f"%x1f%aI%x1f%cI%x1f%P%x1f%s"
        )
        args = [
            "log",
            f"-n{int(max_commits)}",
            f"--since={int(since_days)}.days",
            "--name-status",
            "-M",  # detect renames
            f"--pretty=format:{fmt}",
        ]
        raw = self._run(args)
        commits: list[CommitRaw] = []
        for block in raw.split(record_sep):
            if not block.strip():
                continue
            lines = [ln for ln in block.splitlines() if ln.strip()]
            if not lines or unit_sep not in lines[0]:
                continue
            parts = lines[0].split(unit_sep)
            sha, short, an, ae, aI, cI, parents, subject = (parts + [""] * 8)[:8]
            files: list[dict] = []
            for line in lines[1:]:
                pieces = line.split("\t")
                if len(pieces) >= 2:
                    change_type = pieces[0][:1]
                    if change_type in ("R", "C") and len(pieces) >= 3:
                        files.append({"change_type": change_type,
                                      "old_path": pieces[1], "path": pieces[2]})
                    else:
                        files.append({"change_type": change_type,
                                      "old_path": None, "path": pieces[1]})
            commits.append(
                CommitRaw(
                    sha=sha.strip(), short_sha=short.strip(),
                    author_name=an or None, author_email=ae or None,
                    authored_at=aI or None, committed_at=cI or None,
                    parents=[p for p in parents.split() if p],
                    message=subject, file_changes=files,
                )
            )
        return commits

    def commit_diff(self, sha: str, max_chars: int) -> str:
        """Unified diff for one commit against its first parent, bounded."""
        safe_sha = _validate_sha(sha)
        diff = self._run(
            ["show", "--format=", "--unified=3", "-M", safe_sha],
            max_output_chars=max_chars * 2,
        )
        return diff[:max_chars]

    def file_exists_in_commit(self, sha: str, path: str) -> bool:
        safe_sha = _validate_sha(sha)
        safe_path = _validate_repo_relative(path)
        out = self._run(["ls-tree", "--name-only", safe_sha, "--", safe_path])
        return bool(out.strip())


def _validate_sha(sha: str) -> str:
    cleaned = (sha or "").strip()
    if not cleaned or len(cleaned) > 64 or not all(c in "0123456789abcdefABCDEF" for c in cleaned):
        raise ValueError(f"Invalid commit SHA: {sha!r}")
    return cleaned


def _validate_repo_relative(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or ".." in Path(cleaned).parts:
        raise ValueError(f"Invalid repository-relative path: {path!r}")
    return cleaned
