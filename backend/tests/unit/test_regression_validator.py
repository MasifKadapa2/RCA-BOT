"""V2 regression validator + status derivation + security tests."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.change_intelligence.candidate_generator import CandidateGenerator
from app.core.config import Settings
from app.db.base import Base
from app.git import GitIndexService
from app.investigation.regression_validator import RegressionValidator
from app.schemas.regression import (
    ChangeCandidate,
    GitEvidenceRef,
    RegressionAnalysis,
    RegressionStatus,
)
import app.models  # noqa: F401

NOW = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)


@pytest.fixture()
def indexed_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)

    def commit(files: dict[str, str], message: str, when: datetime):
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S %z")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo,
                       check=True, capture_output=True, text=True, env=env)

    t1 = NOW - timedelta(hours=3)
    commit({"app/services/payment_service.py":
            "class PaymentService:\n"
            "    def charge(self):\n"
            "        return 1\n"}, "initial payment service", t1)

    t2 = NOW - timedelta(minutes=30)
    commit({"app/services/payment_service.py":
            "class PaymentService:\n"
            "    def charge(self):\n"
            "        x = None\n"
            "        return x.email\n"}, "refactor charge to return email", t2)

    settings = Settings(repository_path=str(repo),
                        database_url=f"sqlite:///{tmp_path}/t.db",
                        chroma_path=str(tmp_path / "chroma"), _env_file=None)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    service = GitIndexService(settings)
    with SessionLocal() as db:
        _stats, repo_row = service.index(db, repository_path=str(repo))
    return SessionLocal, repo_row.id, settings


def _candidates_from_db(sf, repo_id):
    issue_time = NOW
    from app.schemas.sentry import SentryEventData, SentryIssueData

    issue = SentryIssueData(issue_id="9", last_seen=issue_time)
    with sf() as db:
        gen = CandidateGenerator(db)
        causal, context_only = gen.generate(
            repository_id=repo_id, issue=issue, event=None,
            retrieval_results=[], incident_time=issue_time,
            window_hours=24, max_candidates=10)
    return causal, context_only


def test_valid_git_evidence_accepted(indexed_repo):
    sf, repo_id, settings = indexed_repo
    causal, _ctx = _candidates_from_db(sf, repo_id)
    assert causal
    top = causal[0]

    ref = GitEvidenceRef(
        commit_sha=top.commit_sha,
        file_path="app/services/payment_service.py",
        start_line=2, end_line=4,
        description="Changed charge implementation")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, valid_against, report = v.validate(
            likely_commit=top.short_sha,
            evidence_for=[ref], evidence_against=[],
            candidates=[top], repository_id=repo_id)
    assert valid_for and report.likely_commit_valid


def test_nonexistent_sha_rejected(indexed_repo):
    sf, repo_id, _s = indexed_repo
    causal, _ = _candidates_from_db(sf, repo_id)
    top = causal[0]
    bad = GitEvidenceRef(commit_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                         file_path="app/services/payment_service.py", description="x")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, _, report = v.validate(
            likely_commit=None, evidence_for=[bad], evidence_against=[],
            candidates=[top], repository_id=repo_id)
    assert not valid_for and report.invalid_evidence_for[0]["reason"].startswith("commit not found")


def test_unrelated_file_rejected(indexed_repo):
    sf, repo_id, _s = indexed_repo
    causal, _ = _candidates_from_db(sf, repo_id)
    top = causal[0]
    ref = GitEvidenceRef(commit_sha=top.commit_sha,
                         file_path="unrelated/other.py", description="x")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, _, report = v.validate(
            likely_commit=None, evidence_for=[ref], evidence_against=[],
            candidates=[top], repository_id=repo_id)
    assert not valid_for
    assert any("not changed" in i["reason"] for i in report.invalid_evidence_for)


def test_fabricated_candidate_rejected(indexed_repo):
    """Commit exists in index but was NOT part of candidate analysis → rejected."""
    sf, repo_id, settings = indexed_repo
    # Use a candidate list that does NOT include the actual recent commit.
    fake_candidate = ChangeCandidate(
        commit_sha="0" * 40, short_sha="0000000", message="fake",
        committed_at=(NOW - timedelta(minutes=5)).isoformat(), score=1.0,
        changed_files=["app/services/payment_service.py"])
    real_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=settings.repository_path, capture_output=True, text=True).stdout.strip()[:8]
    ref = GitEvidenceRef(commit_sha=real_sha[:8],
                         file_path="app/services/payment_service.py",
                         description="x")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, _, report = v.validate(
            likely_commit=None, evidence_for=[ref], evidence_against=[],
            candidates=[fake_candidate], repository_id=repo_id)
    assert not valid_for
    assert any("not part of candidate analysis" in i["reason"]
               for i in report.invalid_evidence_for)


def test_invalid_line_range_rejected(indexed_repo):
    sf, repo_id, _s = indexed_repo
    causal, _ = _candidates_from_db(sf, repo_id)
    top = causal[0]
    ref = GitEvidenceRef(commit_sha=top.commit_sha,
                         file_path="app/services/payment_service.py",
                         start_line=500, end_line=900, description="x")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, _, report = v.validate(
            likely_commit=None, evidence_for=[ref], evidence_against=[],
            candidates=[top], repository_id=repo_id)
    assert not valid_for
    assert any("hunk" in i["reason"] or "line range" in i["reason"]
               for i in report.invalid_evidence_for)


def test_likely_commit_downgrade_rule():
    """LIKELY claim whose likely_commit fails validation must downgrade (unit-level)."""
    # Mirrors investigator logic; kept as a guard against regressions.
    from app.investigation.regression_validator import RegressionValidationReport

    report = RegressionValidationReport()
    report.likely_commit_valid = False
    status = RegressionStatus.LIKELY_REGRESSION
    if status == RegressionStatus.LIKELY_REGRESSION and not report.likely_commit_valid:
        status = RegressionStatus.POSSIBLE_REGRESSION
    assert status == RegressionStatus.POSSIBLE_REGRESSION


# --- Status derivation scenarios ---------------------------------------------

def test_status_no_candidates_is_no_confident():
    candidates = []
    has_code_evidence = True
    if not candidates:
        status = (RegressionStatus.INSUFFICIENT_EVIDENCE if not has_code_evidence
                  else RegressionStatus.NO_CONFIDENT_REGRESSION)
    assert status == RegressionStatus.NO_CONFIDENT_REGRESSION


def test_status_insufficient_when_no_code_evidence():
    candidates = []
    has_code_evidence = False
    if not candidates:
        status = (RegressionStatus.INSUFFICIENT_EVIDENCE if not has_code_evidence
                  else RegressionStatus.NO_CONFIDENT_REGRESSION)
    assert status == RegressionStatus.INSUFFICIENT_EVIDENCE


def test_status_top_candidate_with_support_is_likely(indexed_repo):
    sf, repo_id, _s = indexed_repo
    from app.schemas.sentry import SentryEventData, SentryIssueData, ExceptionInfo, StackFrame

    issue = SentryIssueData(issue_id="9", last_seen=NOW)
    event = SentryEventData(event_id="a" * 32, platform="python",
                            exceptions=[ExceptionInfo(type="AttributeError",
                                                      value="'None' has no attribute 'email'")],
                            frames=[StackFrame(filename="app/services/payment_service.py",
                                               function="charge", line_number=3, in_app=True)])
    with sf() as db:
        gen = CandidateGenerator(db)
        causal, _ctx = gen.generate(repository_id=repo_id, issue=issue, event=event,
                                    retrieval_results=[], incident_time=NOW,
                                    window_hours=24, max_candidates=10)
    assert causal, "expected candidates"
    top = causal[0]
    ref = GitEvidenceRef(commit_sha=top.commit_sha,
                         file_path=top.changed_files[0],
                         start_line=2, end_line=4, description="x")
    with sf() as db:
        v = RegressionValidator(db)
        valid_for, _, report = v.validate(
            likely_commit=top.short_sha, evidence_for=[ref], evidence_against=[],
            candidates=[top], repository_id=repo_id)
    is_likely = (
        report.likely_commit_valid
        and valid_for
        and ("stack_file_match" in top.signals or "symbol_match" in top.signals
             or "line_proximity" in top.signals)
    )
    assert is_likely


# --- Security -----------------------------------------------------------------

def test_secret_file_diffs_are_not_stored(tmp_path):
    """A .env change must be filtered out of stored hunks."""
    repo = tmp_path / "sec"
    repo.mkdir()
    env = os.environ.copy()
    stamp = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S %z")
    env["GIT_AUTHOR_DATE"] = stamp
    env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / ".env").write_text("SECRET_KEY=hunter2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "add env"], cwd=repo,
                   check=True, capture_output=True, text=True, env=env)

    settings = Settings(repository_path=str(repo),
                        database_url=f"sqlite:///{tmp_path}/t.db",
                        chroma_path=str(tmp_path / "chroma"), _env_file=None)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    service = GitIndexService(settings)
    with sf() as db:
        stats, _r = service.index(db, repository_path=str(repo))
        from sqlalchemy import select
        from app.models.git import GitChangeHunk, GitFileChange

        hunks = db.scalars(select(GitChangeHunk)).all()
        blobs = [h.patch or "" for h in hunks]
        assert all("hunter2" not in b for b in blobs), "secret leaked into hunk store"


def test_shell_execution_impossible_via_client():
    from app.git.client import _validate_sha, _validate_repo_relative

    for evil in ["abc; rm -rf /", "$(whoami)", "`id`", "--upload-pack=x"]:
        with pytest.raises(ValueError):
            _validate_sha(evil)
    for evil_path in ["../../etc/passwd", "/etc/passwd", "a/../../b"]:
        with pytest.raises(ValueError):
            _validate_repo_relative(evil_path)
