"""Deterministic retrieval metrics: Recall@k and MRR."""

from __future__ import annotations


def recall_at_k(ranks: list[int | None], k: int) -> float:
    """ranks: 1-indexed rank of expected file, or None if not found."""
    if not ranks:
        return 0.0
    hits = sum(1 for r in ranks if r is not None and r <= k)
    return hits / len(ranks)


def mrr(ranks: list[int | None]) -> float:
    """Mean Reciprocal Rank."""
    if not ranks:
        return 0.0
    total = 0.0
    for r in ranks:
        if r is not None and r > 0:
            total += 1.0 / r
    return total / len(ranks)


def compute_metrics(results: list[dict]) -> dict:
    """
    results: list of {case_id, rank, found, expected_file}
    Returns dict with Recall@5, Recall@10, MRR
    """
    ranks = [r.get("rank") for r in results]
    return {
        "cases": len(results),
        "recall_at_5": round(recall_at_k(ranks, 5), 4),
        "recall_at_10": round(recall_at_k(ranks, 10), 4),
        "mrr": round(mrr(ranks), 4),
    }
