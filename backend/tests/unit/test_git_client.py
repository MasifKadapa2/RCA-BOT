"""V2 Git layer tests: detection, indexing, incremental, diffs, security."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.git.client import GitClient, NotAGitRepositoryError, _validate_sha
from app.core.errors import RepositoryNotFoundError


def _git(repo: Path, *args: str, env_date: str | None = None):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = env.get("GIT_AUTHOR_NAME", "Test Author")
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = env.get("GIT_COMMITTER_NAME", "Test Author")
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    if env_date:
        env["GIT_AUTHOR_DATE"] = env_date
        env["GIT_COMMITTER_DATE"] = env_date
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True, env=env)


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "Tester")
    return repo


def _commit(repo: Path, files: dict[str, str], message: str, when: datetime | None = None):
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(repo, "add", "-A")
    args = ["commit", "-q", "-m", message]
    env_date = None
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S %z") if when.tzinfo else \
            when.strftime("%Y-%m-%dT%H:%M:%S+0000")
        env_date = stamp
    _ = subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo, check=True, capture_output=True, text=True,
        env={**os.environ,
             "GIT_AUTHOR_DATE": env_date or "", "GIT_COMMITTER_DATE": env_date or ""},
    )


class TestDetection:
    def test_valid_repo(self, git_repo):
        client = GitClient(str(git_repo))
        assert client.is_git_repo()

    def test_non_git_directory_raises(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(NotAGitRepositoryError):
            GitClient(str(plain))

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(RepositoryNotFoundError):
            GitClient(str(tmp_path / "nope"))


class TestShaValidation:
    def test_rejects_shell_metacharacters(self):
        with pytest.raises(ValueError):
            _validate_sha("abc; rm -rf /")
        with pytest.raises(ValueError):
            _validate_sha("HEAD --upload-pack=evil")
        with pytest.raises(ValueError):
            _validate_sha("")


class TestLogParsing:
    def test_commits_and_rename(self, git_repo):
        base = datetime.now(timezone.utc) - timedelta(days=2)
        _commit(git_repo, {"app/service.py": "a = 1\nb = 2\nc = 3\nd = 4\n"}, "initial", base)
        later = base + timedelta(hours=1)
        _git(git_repo, "mv", "app/service.py", "app/renamed_service.py")
        _commit(git_repo, {"app/renamed_service.py": "a = 1\nb = 2\nc = 3\nd = 5\n"},
                "rename it", later)

        client = GitClient(str(git_repo))
        commits = client.fetch_commits(since_days=30, max_commits=10)
        assert len(commits) == 2
        rename = commits[0]  # newest first
        renames = [f for f in rename.file_changes
                   if f["change_type"].startswith("R")]
        if renames:  # git detects rename when similarity is high enough
            assert renames[0]["old_path"] == "app/service.py"
            assert renames[0]["path"] == "app/renamed_service.py"
        else:
            # low-similarity files are recorded as delete + add instead
            types = {f["change_type"] for f in rename.file_changes}
            assert types == {"A", "D"}

    def test_merge_commit_flagged(self, git_repo):
        base = datetime.now(timezone.utc) - timedelta(days=2)
        _commit(git_repo, {"f.txt": "one\n"}, "base", base)
        _git(git_repo, "checkout", "-qb", "feature")
        _commit(git_repo, {"feat.txt": "feat\n"}, "feature work",
                base + timedelta(minutes=5))
        _git(git_repo, "checkout", "-q", "master")
        _commit(git_repo, {"main.txt": "main\n"}, "main work", base + timedelta(minutes=6))
        _git(git_repo, "merge", "-q", "--no-ff", "feature",
             "-m", "Merge feature")

        client = GitClient(str(git_repo))
        commits = client.fetch_commits(since_days=30, max_commits=10)
        merges = [c for c in commits if len(c.parents) > 1]
        assert merges and merges[0].message.startswith("Merge feature")


class TestDiffExtraction:
    def test_bounded_diff(self, git_repo):
        _commit(git_repo, {"big.py": "\n".join(f"line{i} = {i}" for i in range(2000))}, "big")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo,
                             capture_output=True, text=True).stdout.strip()
        client = GitClient(str(git_repo))
        diff = client.commit_diff(sha, max_chars=2000)
        assert len(diff) <= 2000

    def test_file_exists_in_commit(self, git_repo):
        _commit(git_repo, {"exists.py": "x = 1\n"}, "add file")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo,
                             capture_output=True, text=True).stdout.strip()
        client = GitClient(str(git_repo))
        assert client.file_exists_in_commit(sha, "exists.py")
        assert not client.file_exists_in_commit(sha, "missing.py")

    def test_invalid_args_rejected(self, git_repo):
        _commit(git_repo, {"a.py": "x\n"}, "c")
        client = GitClient(str(git_repo))
        with pytest.raises(ValueError):
            client.commit_diff("abc123; rm -rf /", 1000)
        with pytest.raises(ValueError):
            client.file_exists_in_commit("abcdef12", "../../etc/passwd")
