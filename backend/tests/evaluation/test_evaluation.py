"""Evaluation suite: dataset sanity + retrieval metrics."""

from app.evaluation.dataset import EVALUATION_CASES
from app.evaluation.metrics import recall_at_k, mrr, compute_metrics


def test_dataset_has_20_cases():
    assert len(EVALUATION_CASES) >= 20


def test_dataset_covers_required_patterns():
    ids = {c["id"] for c in EVALUATION_CASES}
    required = {
        "nil-dereference",
        "missing-key",
        "wrong-method-call",
        "incorrect-conditional",
        "wrong-argument",
        "serialization-failure",
        "database-lookup-failure",
        "authentication-failure",
        "type-mismatch",
        "boundary-condition",
        "missing-association",
        "incorrect-configuration",
        "parsing-failure",
        "validation-error",
        "wrong-return-type",
        "async-race",
        "exception-propagation",
        "incorrect-api-response",
        "file-path-issue",
        "insufficient-evidence",
    }
    assert required.issubset(ids)


def test_metrics_computation():
    # Simple sanity
    assert recall_at_k([1, 2, 6, None], 5) == 0.5  # 2 of 4 in top5
    assert recall_at_k([1, 10, 3], 10) == 1.0
    assert abs(mrr([1, 2, None]) - (1 + 0.5) / 3) < 1e-6
    assert compute_metrics([{"rank": 1}, {"rank": None}])["recall_at_5"] == 0.5


def test_retrieval_evaluation_deterministic():
    from app.evaluation.run import evaluate

    report = evaluate(use_chroma=False)
    metrics = report["metrics"]
    assert metrics["cases"] >= 20
    # Must be reproducible and reasonably good on synthetic repo
    assert 0.0 <= metrics["recall_at_5"] <= 1.0
    assert 0.0 <= metrics["recall_at_10"] <= 1.0
    assert 0.0 <= metrics["mrr"] <= 1.0
    # Our synthetic repo should achieve high recall
    assert metrics["recall_at_5"] >= 0.8
    assert metrics["mrr"] >= 0.7
