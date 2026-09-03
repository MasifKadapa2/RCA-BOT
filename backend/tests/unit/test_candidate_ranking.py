"""V2 candidate ranking signal tests (deterministic, no LLM)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.change_intelligence.candidate_generator import CandidateGenerator
from app.core.config import Settings
from app.db.base import Base
from app.git import GitIndexService
from app.models.git import GitCommit  # noqa: F401
import app.models  # noqa: F401


NOW = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)


def _make_repo(tmp_path, commits: list[tuple[str, dict[str, str], datetime]]):
    """commits: (message, files, when) oldest first. Returns indexed repo_id + engine factory."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    env = os.environ.copy()
    for message, files, when in commits:
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

    settings = Settings(
        repository_path=str(repo), database_url=f"sqlite:///{tmp_path}/t.db",
        chroma_path=str(tmp_path / "chroma"), _env_file=None,
    )
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    service = GitIndexService(settings)
    with SessionLocal() as db:
        stats, repo_row = service.index(db, repository_path=str(repo))
    return SessionLocal, repo_row.id, settings


def _issue_event(failing_file: str, failing_line: int, symbol: str,
                 exc_value: str, incident_time):
    from app.schemas.sentry import ExceptionInfo, SentryEventData, SentryIssueData, StackFrame

    issue = SentryIssueData(issue_id="1", title=f"TypeError: {exc_value}",
                            last_seen=incident_time)
    event = SentryEventData(event_id="e" * 32, platform="python",
                            exceptions=[ExceptionInfo(type="TypeError", value=exc_value)],
                            frames=[StackFrame(filename=failing_file,
                                               function=symbol,
                                               line_number=failing_line, in_app=True)])
    return issue, event


def test_stack_file_match_increases_score(tmp_path):
    t_relevant = NOW - timedelta(minutes=10)
    t_unrelated = NOW - timedelta(minutes=20)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("unrelated docs change", {"docs/x.md": "doc"}, t_unrelated),
        ("fix checkout bug", {"app/services/order_service.py": "def calculate():\n    pass\n"},
         t_relevant),
    ])
    issue, event = _issue_event("app/services/order_service.py", 8,
                                "calculate_total", "not iterable", NOW)
    with sf() as db:
        gen = CandidateGenerator(db)
        causal, _ = gen.generate(repository_id=repo_id, issue=issue, event=event,
                                 retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    assert causal, "expected at least one candidate"
    top = causal[0]
    assert "stack_file_match" in top.signals
    assert top.score > 0


def test_retrieved_file_match_increases_score(tmp_path):
    t_rel = NOW - timedelta(minutes=30)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("touch serializer", {"app/serializers/employee_serializer.rb": "x\n"}, t_rel),
    ])
    # Stack points elsewhere; retrieval points at the touched file.
    issue, event = _issue_event("other.py", 5, "other_fn", "boom", NOW)
    from app.schemas.retrieval import RetrievalResult

    retrieved = [RetrievalResult(relative_path="app/serializers/employee_serializer.rb",
                                 start_line=1, end_line=5, content="x", score=2.0)]
    with sf() as db:
        causal, _ = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=retrieved, incident_time=NOW, window_hours=24, max_candidates=10)
    assert causal and "retrieved_file_match" in causal[0].signals


def test_symbol_and_line_proximity_signals(tmp_path):
    body = "\n".join([f"line{i}()" for i in range(1, 40)])
    t_rel = NOW - timedelta(minutes=15)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("modify charge method",
         {"app/services/payment_service.py": f"def charge():\n{body}\n"}, t_rel),
    ])
    issue, event = _issue_event("app/services/payment_service.py", 20,
                                "charge", "NoneType not iterable", NOW)
    with sf() as db:
        causal, _ = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    assert causal
    signals = causal[0].signals
    assert "symbol_match" in signals or "line_proximity" in signals


def test_temporal_proximity_closer_is_higher(tmp_path):
    near = NOW - timedelta(minutes=5)
    far = NOW - timedelta(hours=20)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("far commit", {"a/f.py": "a\n"}, far),
        ("near commit", {"b/g.py": "b\n"}, near),
    ])
    # Neither matches stack/retrieval — only temporal + modified-file signals apply.
    issue, event = _issue_event("zzz/none.py", 1, "nothing_fn", "boom boom", NOW)
    with sf() as db:
        causal, _ = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    by_sha = {c.message: c for c in causal}
    if "near commit" in by_sha and "far commit" in by_sha:
        assert by_sha["near commit"].score > by_sha["far commit"].score
        assert "temporal_proximity" in by_sha["near commit"].signals


def test_commits_after_incident_are_context_only(tmp_path):
    after = NOW + timedelta(hours=2)
    before = NOW - timedelta(hours=1)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("before fix", {"app/services/order_service.py": "def calculate():\n    pass\n"}, before),
        ("after fix", {"app/services/order_service.py": "def calculate():\n    return 1\n"}, after),
    ])
    issue, event = _issue_event("app/services/order_service.py", 8,
                                "calculate_total", "not iterable", NOW)
    with sf() as db:
        causal, context_only = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    assert all(not c.context_only for c in causal)
    assert any(c.context_only for c in context_only)


def test_diff_relevance_signal(tmp_path):
    body = "# NoneType handling\nvalue = get_value()\n"
    t_rel = NOW - timedelta(minutes=8)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("handle NoneType in checkout",
         {"app/services/checkout.py": body}, t_rel),
    ])
    issue, event = _issue_event("app/other.py", 3, "other_sym",
                                "'NoneType' object is not iterable", NOW)
    with sf() as db:
        causal, _ = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    assert causal and "diff_relevance" in causal[0].signals


def test_unrelated_commit_ranks_lower_or_zero(tmp_path):
    t_rel = NOW - timedelta(minutes=10)
    sf, repo_id, settings = _make_repo(tmp_path, [
        ("readme update", {"README.md": "docs\n"}, t_rel),  # .md not indexed as source? git still tracks it
    ])
    issue, event = _issue_event("nope/never.py", 99, "no_symbol_here",
                                "totally unrelated failure", NOW)
    with sf() as db:
        causal, zero = CandidateGenerator(db).generate(
            repository_id=repo_id, issue=issue, event=event,
            retrieval_results=[], incident_time=NOW, window_hours=24, max_candidates=10)
    # README.md is tracked by git even though not a supported language; diff
    # relevance should be ~0 so score stays minimal.
    if causal:
        assert causal[0].score <= 1.5  # temporal-only territory
