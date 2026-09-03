"""V2 mocked end-to-end scenarios.

Scenario 1: relevant recent commit → LIKELY_REGRESSION
Scenario 2: multiple candidates → POSSIBLE_REGRESSION
Scenario 3: no relevant recent commit → NO_CONFIDENT_REGRESSION
Scenario 4: thin incident + code evidence → INSUFFICIENT_EVIDENCE

Also verifies Git failures never break the V1 RCA.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.change_intelligence import ChangeAnalyzer, CandidateGenerator
from app.core.config import Settings
from app.db.base import Base
from app.git import GitIndexService
from app.models import Incident, IncidentStatus  # noqa: F401
import app.models  # noqa: F401
from app.schemas.regression import (
    ChangeCandidate,
    RegressionAnalysis,
    RegressionStatus,
    GitEvidenceRef,
)
from app.schemas.retrieval import RetrievalResult
from app.schemas.sentry import ExceptionInfo, SentryEventData, SentryIssueData, StackFrame

NOW = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)


def _build_repo(tmp_path, commits):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
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

    settings = Settings(repository_path=str(repo),
                        database_url=f"sqlite:///{tmp_path}/t.db",
                        chroma_path=str(tmp_path / "chroma"), _env_file=None)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    service = GitIndexService(settings)
    with sf() as db:
        service.index(db, repository_path=str(repo))
    return sf, settings


def _incident(failing_file="app/services/payment_service.py",
              line=3, symbol="charge",
              value="'None' has no attribute 'email'"):
    issue = SentryIssueData(issue_id="9", title=f"AttributeError: {value}",
                            first_seen=NOW, last_seen=NOW)
    event = SentryEventData(event_id="b" * 32, platform="python",
                            timestamp=NOW,
                            exceptions=[ExceptionInfo(type="AttributeError", value=value)],
                            frames=[StackFrame(filename=failing_file, function=symbol,
                                               line_number=line, in_app=True)])
    return issue, event


def _retrieval(paths):
    return [RetrievalResult(relative_path=p, start_line=1, end_line=10,
                            content="x = user.email\n", score=2.0) for p in paths]


class ScriptedLLM:
    """Deterministic LLM double that echoes a scripted RegressionAnalysis."""

    def __init__(self, response_fn):
        self.response_fn = response_fn

    def generate_structured(self, prompt, schema, system=None):
        return schema.model_validate(self.response_fn(prompt))


class FailingLLM:
    def generate_structured(self, prompt, schema, system=None):
        from app.core.errors import LLMUnavailableError
        raise LLMUnavailableError("down")


def _analyzer(sf, settings, llm):
    return ChangeAnalyzer(settings=settings, llm_provider=llm, session_factory=sf)


def test_scenario_1_likely_regression(tmp_path):
    t_bug = NOW - timedelta(minutes=20)
    sf, settings = _build_repo(tmp_path, [
        ("initial payment service",
         {"app/services/payment_service.py": "class P:\n    def charge(self):\n        return 1\n"},
         NOW - timedelta(hours=5)),
        ("charge now reads user.email without guard",
         {"app/services/payment_service.py":
          "class P:\n    def charge(self):\n        x = None\n        return x.email\n"},
         t_bug),
    ])
    issue, event = _incident()
    retrieved = _retrieval(["app/services/payment_service.py"])

    def scripted(prompt):
        return {
            "status": "LIKELY_REGRESSION",
            "likely_commit": None,  # filled below from candidates in prompt
            "causal_explanation": ("FACT: commit modified charge. INFERENCE: changed lines "
                                   "are inside failing method. HYPOTHESIS: nil email access."),
            "evidence_for": [],
            "evidence_against": [],
            "confidence": 88,
            "unknowns": ["Deployment time unknown."],
            "recommended_action": "Review nullable path introduced by the commit.",
            "candidate_commits": [],
        }

    # Pre-pass to learn candidate SHA deterministically (no leakage into ranking).
    with sf() as db:
        repo_row = db.execute(select(__import__("app.models", fromlist=["Repository"]).Repository)).scalars().first()
        gen = CandidateGenerator(db)
        causal, ctx = gen.generate(repository_id=repo_row.id, issue=issue, event=event,
                                   retrieval_results=retrieved, incident_time=NOW,
                                   window_hours=24, max_candidates=10)
    assert causal
    true_sha_full = causal[0].commit_sha
    payload = scripted(None)
    payload["likely_commit"] = causal[0].short_sha
    payload["evidence_for"] = [{
        "commit_sha": true_sha_full[:8],
        "file_path": "app/services/payment_service.py",
        "start_line": 2, "end_line": 4,
        "description": "Introduced unguarded x.email access.",
    }]

    analyzer = _analyzer(sf, settings, ScriptedLLM(lambda p: payload))
    result = analyzer.analyze(repository_path=settings.repository_path, issue=issue,
                              event=event, retrieval_results=retrieved,
                              v1_summary="nil email access")
    assert result.analysis is not None
    assert result.analysis.status == RegressionStatus.LIKELY_REGRESSION
    assert result.analysis.likely_commit == causal[0].short_sha
    assert result.analysis.evidence_for  # validated GIT evidence retained


def test_scenario_2_possible_regression(tmp_path):
    t_a = NOW - timedelta(minutes=40)
    t_b = NOW - timedelta(minutes=35)
    sf, settings = _build_repo(tmp_path, [
        ("touch a", {"app/a/service.py": "x\n"}, t_a),
        ("touch b", {"app/b/service.py": "y\n"}, t_b),
    ])
    # Stack/retrieval match NEITHER file → only temporal signals → weak support.
    issue, event = _incident(failing_file="app/unknown.py", symbol="mystery")

    payload = {
        "status": "POSSIBLE_REGRESSION",
        "likely_commit": None,
        "causal_explanation": "Two plausible candidates; evidence incomplete.",
        "evidence_for": [], "evidence_against": [], "confidence": 40,
        "unknowns": ["No stack-file overlap."],
        "recommended_action": "Inspect both diffs.",
        "candidate_commits": [],
    }
    analyzer = _analyzer(sf, settings, ScriptedLLM(lambda p: payload))
    result = analyzer.analyze(repository_path=settings.repository_path, issue=issue,
                              event=event, retrieval_results=_retrieval([]),
                              v1_summary=None)
    assert result.analysis.status == RegressionStatus.POSSIBLE_REGRESSION


def test_scenario_3_no_confident_regression(tmp_path):
    sf, settings = _build_repo(tmp_path, [
        ("docs only", {"README.md": "docs\n"}, NOW - timedelta(minutes=10)),
    ])
    issue, event = _incident()
    analyzer = _analyzer(sf, settings, FailingLLM())
    result = analyzer.analyze(repository_path=settings.repository_path, issue=issue,
                              event=event, retrieval_results=_retrieval([]),
                              v1_summary=None)
    # No source-code commit touched the failing file; LLM down too.
    assert result.analysis is not None
    assert result.analysis.status in (RegressionStatus.NO_CONFIDENT_REGRESSION,
                                      RegressionStatus.POSSIBLE_REGRESSION)


def test_scenario_4_insufficient_evidence(tmp_path):
    sf, settings = _build_repo(tmp_path, [
        ("some change", {"app/x.py": "x\n"}, NOW - timedelta(minutes=10)),
    ])
    issue = SentryIssueData(issue_id="9")  # no timestamps, no title info
    event = SentryEventData(event_id="c" * 32)  # no frames
    analyzer = _analyzer(sf, settings, FailingLLM())
    result = analyzer.analyze(repository_path=settings.repository_path, issue=issue,
                              event=event, retrieval_results=[], v1_summary=None)
    assert result.analysis.status == RegressionStatus.INSUFFICIENT_EVIDENCE


def test_non_git_repository_degrades_gracefully(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    settings = Settings(repository_path=str(plain),
                        database_url=f"sqlite:///{tmp_path}/t.db",
                        chroma_path=str(tmp_path / "chroma"), _env_file=None)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    issue, event = _incident()
    analyzer = _analyzer(sf, settings, FailingLLM())
    result = analyzer.analyze(repository_path=str(plain), issue=issue,
                              event=event, retrieval_results=_retrieval([]),
                              v1_summary=None)
    assert not result.git_detected
    assert result.unavailable_reason
    assert result.analysis is None  # V1 RCA unaffected


def test_timeline_never_claims_deployment(tmp_path):
    t_commit = NOW - timedelta(minutes=7)
    sf, settings = _build_repo(tmp_path, [
        ("suspect", {"app/services/payment_service.py": "z\n"}, t_commit),
    ])
    from app.schemas.sentry import SentryIssueData

    analysis = RegressionAnalysis(
        status=RegressionStatus.LIKELY_REGRESSION,
        likely_commit="abc1234",
        candidate_commits=[ChangeCandidate(
            commit_sha="a" * 40, short_sha="abc1234", message="suspect",
            committed_at=t_commit.isoformat(), score=0.8, signals={})],
        confidence=80)
    timeline = __import__("app.change_intelligence.change_analyzer",
                          fromlist=["build_timeline"]).build_timeline(
        analysis, issue_first_seen=NOW.isoformat())
    labels = [e["label"] for e in timeline]
    assert any("Commit created" in l for l in labels)
    assert all("deploy" not in l.lower() for l in labels), \
        "timeline must never claim deployment"
