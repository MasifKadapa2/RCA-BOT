"""V2 Change Intelligence evaluation runner.

Usage:
    python -m app.evaluation.run_regression
    python -m pytest tests/evaluation -q   (includes V2 checks)

Measures CANDIDATE RETRIEVAL quality (Recall@1/3/5, MRR) and deterministic
regression-status classification. LLM causal reasoning quality is NOT measured
here — the runner exercises the deterministic pipeline only.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.change_intelligence.candidate_generator import CandidateGenerator
from app.core.config import Settings
from app.evaluation.metrics import mrr as compute_mrr
from app.evaluation.regression_dataset import INCIDENT, build_cases, materialize_repo
from app.schemas.regression import RegressionStatus
from app.schemas.sentry import ExceptionInfo, SentryEventData, SentryIssueData, StackFrame


def _run_case(case, tmp_root: Path) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.git import GitIndexService
    from app.ingestion.embeddings import HashEmbeddingProvider  # noqa: F401 (parity with V1 eval)
    import app.models  # noqa: F401

    repo = materialize_repo(tmp_root, case)

    settings = Settings(
        repository_path=str(repo),
        database_url=f"sqlite:///{tmp_root}/{case.case_id}.db",
        chroma_path=f"{tmp_root}/{case.case_id}-chroma",
        _env_file=None,
    )
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    service = GitIndexService(settings)
    with SessionLocal() as db:
        _stats, repo_row = service.index(db, repository_path=str(repo))

    # Build incident inputs exactly like production would.
    issue = SentryIssueData(issue_id=case.case_id,
                            title=f"TypeError: {case.exception_value}",
                            first_seen=INCIDENT, last_seen=INCIDENT)
    frames = []
    if case.failing_file:
        frames.append(StackFrame(filename=case.failing_file,
                                 function=case.failing_symbol,
                                 line_number=case.failing_line, in_app=True))
    event = SentryEventData(event_id="f" * 32, platform="python",
                            timestamp=INCIDENT,
                            exceptions=[ExceptionInfo(type="TypeError",
                                                      value=case.exception_value)],
                            frames=frames)
    from app.schemas.retrieval import RetrievalResult

    retrieved = [RetrievalResult(relative_path=p, start_line=1, end_line=5,
                                 content="", score=1.0) for p in case.retrieved_paths]

    with SessionLocal() as db:
        gen = CandidateGenerator(db)
        causal, context_only = gen.generate(
            repository_id=repo_row.id, issue=issue, event=event,
            retrieval_results=retrieved, incident_time=INCIDENT,
            window_hours=24, max_candidates=10)

    rank = None
    if case.expected_commit_message:
        for idx, cand in enumerate(causal, start=1):
            if cand.message == case.expected_commit_message:
                rank = idx
                break

    status = derive_status(causal, has_code_evidence=bool(frames or retrieved))
    status_ok = status.value == case.expected_status

    return {
        "case_id": case.case_id,
        "pattern": case.pattern,
        "rank": rank,
        "found": rank is not None,
        "status": status.value,
        "expected_status": case.expected_status,
        "status_ok": status_ok,
        "top3": [c.short_sha for c in causal[:3]],
    }


_STRUCTURAL_SIGNALS = {"stack_file_match", "retrieved_file_match",
                       "symbol_match", "line_proximity"}


def derive_status(candidates, has_code_evidence: bool):
    """Deterministic pre-LLM status derivation (mirrors analyzer rules).

    Only candidates with at least one STRONG signal count toward
    POSSIBLE/LIKELY; temporal-only overlap never does.
    """
    strong = [c for c in candidates
              if any(s in _STRUCTURAL_SIGNALS for s in c.signals)]
    if not strong:
        return (RegressionStatus.INSUFFICIENT_EVIDENCE if not has_code_evidence
                else RegressionStatus.NO_CONFIDENT_REGRESSION)
    if candidates[0] in strong and candidates[0].score >= 6.0:
        return RegressionStatus.LIKELY_REGRESSION
    return RegressionStatus.POSSIBLE_REGRESSION


def run_regression() -> dict:
    cases = build_cases()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for case in cases:
            results.append(_run_case(case, root))

    ranks = [r["rank"] for r in results]
    recall_1 = sum(1 for r in ranks if r == 1) / len(ranks)
    recall_3 = sum(1 for r in ranks if r and r <= 3) / len(ranks)
    recall_5 = sum(1 for r in ranks if r and r <= 5) / len(ranks)
    mrr = compute_mrr(ranks)

    status_cases = [r for r in results]
    status_accuracy = (sum(1 for r in status_cases if r["status_ok"]) / len(status_cases))

    return {
        "cases": len(results),
        "candidate_recall@1": round(recall_1, 4),
        "candidate_recall@3": round(recall_3, 4),
        "candidate_recall@5": round(recall_5, 4),
        "candidate_mrr": round(mrr, 4),
        "classification_accuracy": round(status_accuracy, 4),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RCA Bot V2 Regression Evaluation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run_regression()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print("RCA Bot V2 — Candidate Retrieval Evaluation")
    print(f"Cases: {report['cases']}")
    print(f"Candidate Recall@1: {report['candidate_recall@1']*100:.0f}%")
    print(f"Candidate Recall@3: {report['candidate_recall@3']*100:.0f}%")
    print(f"Candidate Recall@5: {report['candidate_recall@5']*100:.0f}%")
    print(f"Candidate MRR:      {report['candidate_mrr']:.2f}")
    print(f"Classification accuracy (deterministic): {report['classification_accuracy']*100:.0f}%")
    print()
    for r in report["results"]:
        rank = f"rank {r['rank']}" if r["rank"] else "not found"
        mark = "✓" if (not r.get("expected_commit_message") or r["found"]) else "✗"
        print(f"  {mark} {r['case_id']}: {rank} | status={r['status']} (expected {r['expected_status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
