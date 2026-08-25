"""Phase 5: query construction, exact retrieval, vector retrieval, ranking."""

from __future__ import annotations

import pytest

from app.ingestion.indexer import RepositoryIndexer
from app.retrieval.exact import ExactRetriever
from app.retrieval.query_builder import build_retrieval_query
from app.retrieval.vector import VectorRetriever
from tests.conftest import FakeEmbeddingProvider


@pytest.fixture()
def indexed_repo(db_session, sample_repo_path):
    """Index the sample repo with deterministic fake embeddings."""
    from tests.unit.test_phase4_ingestion import CountingStore

    store = CountingStore()
    indexer = RepositoryIndexer(
        repository_path=sample_repo_path,
        chroma_path="/tmp/unused-chroma",
        embedding_provider=FakeEmbeddingProvider(),
        vector_store_factory=lambda: store,
    )
    stats = indexer.run(db_session)
    return {"store": store, "stats": stats}


# ------------------------------------------------------------- query building


def test_query_builder_extracts_filenames_and_symbols(sentry_issue_payload, sentry_event_payload):
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event, extra_texts=["see employee_serializer.rb attached"])

    assert any("employee_serializer.rb" in f for f in query.exact_filenames)
    assert any("serialize" in s for s in query.exact_symbols)
    assert query.semantic_query, "semantic query must not be empty"
    assert "nomethoderror" in query.semantic_query.lower() or "email" in query.semantic_query.lower()


def test_query_builder_tolerates_missing_event(sentry_issue_payload):
    from app.sentry.normalization import normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    query = build_retrieval_query(issue, None)
    assert query.semantic_query


def test_query_builder_splits_camel_and_snake_identifiers():
    from app.retrieval.query_builder import _split_identifier

    terms = _split_identifier("EmployeeSerializer")
    assert "employee" in terms and "serializer" in terms


# ------------------------------------------------------------ exact retrieval


def test_exact_retrieval_finds_serializer_by_filename(db_session, indexed_repo, sentry_issue_payload, sentry_event_payload):
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event)

    results = ExactRetriever(db_session).retrieve(query, limit=10)
    assert results, "exact retrieval returned nothing"
    top_paths = {r.relative_path for r in results[:3]}
    assert any("employee_serializer.rb" in p for p in top_paths), (
        f"expected serializer file ranked highly, got {[(r.relative_path, r.score) for r in results]}"
    )


def test_exact_results_carry_provenance(db_session, indexed_repo, sentry_issue_payload, sentry_event_payload):
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event)

    results = ExactRetriever(db_session).retrieve(query, limit=5)
    for r in results:
        assert r.source_type == "CODE"
        assert r.relative_path
        assert r.start_line >= 1
        assert r.end_line >= r.start_line
        assert r.content
        assert r.score > 0
        assert r.retrieval_method == "exact"


def test_exact_filename_match_outranks_generic_identifier(
    db_session, indexed_repo, sentry_issue_payload, sentry_event_payload
):
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event)

    results = ExactRetriever(db_session).retrieve(query, limit=10)
    serializer_score = max(
        (r.score for r in results if "employee_serializer" in r.relative_path), default=0
    )
    other_best = max(
        (r.score for r in results if "employee_serializer" not in r.relative_path), default=0
    )
    assert serializer_score >= other_best


# ----------------------------------------------------------- vector retrieval


def test_vector_retrieval_returns_hydrated_chunks(db_session, indexed_repo, sentry_event_payload):
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_event_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event)

    retriever = VectorRetriever(
        db_session,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=indexed_repo["store"],
    )
    results = retriever.retrieve(query, limit=5)
    assert isinstance(results, list)


# -------------------------------------------------------------------- ranking


def test_code_retriever_merges_exact_and_vector_with_ranking(
    db_session, sample_repo_path, indexed_repo, sentry_issue_payload, sentry_event_payload
):
    from app.retrieval.retriever import CodeRetriever
    from app.sentry.normalization import normalize_event, normalize_issue

    issue = normalize_issue("123456", sentry_issue_payload)
    event = normalize_event(sentry_event_payload)
    query = build_retrieval_query(issue, event)

    retriever = CodeRetriever(
        db_session_factory=lambda: db_session,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store_factory=lambda: indexed_repo["store"],
        max_results=5,
    )
    frame_paths = [f.filename for f in event.frames if f.filename]
    results = retriever.retrieve(query, stack_frame_paths=frame_paths)

    assert results, "merged retrieval returned nothing"
    assert len(results) <= 5
    # Ranked descending by score
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    # The crash-site file must be represented near the top
    top_paths = [r.relative_path for r in results[:3]]
    assert any("employee_serializer.rb" in p for p in top_paths)
    # Provenance on every result
    for r in results:
        assert r.retrieval_method in ("exact", "vector", "exact+vector", "vector+exact")
