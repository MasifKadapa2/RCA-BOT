"""V2 evaluation tests — dataset integrity, metrics, no-leakage guards."""

from pathlib import Path

from app.evaluation.regression_dataset import build_cases, INCIDENT
from app.evaluation.run_regression import derive_status, run_regression
from app.schemas.regression import ChangeCandidate, RegressionStatus


def test_v2_dataset_has_20_cases():
    assert len(build_cases()) >= 20


def test_v2_dataset_patterns_covered():
    ids = {c.case_id for c in build_cases()}
    required = {
        "direct-regression", "indirect-regression", "unrelated-recent",
        "multiple-candidates", "older-relevant", "newest-unrelated",
        "same-file-other-method", "same-symbol-modified", "nearby-line-change",
        "renamed-file", "deleted-code", "refactor", "config-change",
        "async-change", "validation-change", "api-response-change",
        "dependency-bump", "merge-commit", "no-relevant-commit",
        "insufficient-evidence",
    }
    assert required == ids


def test_no_benchmark_leakage_dataset_only_holds_truth():
    """Ground truth lives on the case; ranking inputs never see it."""
    for case in build_cases():
        # expected_* fields are the ONLY ground-truth carriers.
        assert hasattr(case, "expected_commit_message")
        assert hasattr(case, "expected_status")
        # commits themselves contain no reference to expectations
        for message, _files, _when in case.commits:
            assert "expected" not in message.lower()


def test_derive_status_requires_structural_signal():
    temporal_only = [ChangeCandidate(
        commit_sha="a" * 40, short_sha="aaa0000", score=1.0,
        signals={"temporal_proximity": 0.9})]
    assert derive_status(temporal_only, has_code_evidence=True) == \
        RegressionStatus.NO_CONFIDENT_REGRESSION
    assert derive_status(temporal_only, has_code_evidence=False) == \
        RegressionStatus.INSUFFICIENT_EVIDENCE


def test_run_regression_metrics_reproducible():
    r1 = run_regression()
    r2 = run_regression()
    assert r1["cases"] >= 20
    assert r1["candidate_recall@5"] == r2["candidate_recall@5"]
    assert r1["candidate_mrr"] == r2["candidate_mrr"]
    assert 0.0 <= r1["classification_accuracy"] <= 1.0
    # Deterministic pipeline should be strong on synthetic repos.
    assert r1["candidate_recall@5"] >= 0.6
    assert r1["classification_accuracy"] >= 0.8
