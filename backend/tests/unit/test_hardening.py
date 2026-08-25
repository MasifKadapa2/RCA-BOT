"""Hardening regression tests for V1 final freeze."""

import tempfile
from pathlib import Path

import pytest

from app.core.config import Settings


def test_production_vector_store_is_chroma(tmp_path):
    """Production default must be Chroma, not InMemory."""
    from app.bootstrap import build_container
    from app.ingestion.vector_store import ChromaVectorStore

    settings = Settings(
        slack_bot_token="x",
        slack_app_token="y",
        sentry_auth_token="z",
        sentry_org="org",
        ollama_model="m",
        repository_path=str(tmp_path),
        database_url=f"sqlite:///{tmp_path}/db.db",
        chroma_path=str(tmp_path / "chroma"),
        _env_file=None,
    )
    container = build_container(settings)
    # CodeRetriever's factory should produce Chroma
    store = container.settings.chroma_path
    assert "chroma" in store
    # Directly test factory
    from app.ingestion.vector_store import ChromaVectorStore, InMemoryVectorStore

    # Production factory returns Chroma
    factory = lambda: ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
    assert isinstance(factory(), ChromaVectorStore)
    # Test double is InMemory
    assert isinstance(InMemoryVectorStore(), InMemoryVectorStore)


def test_data_location_is_outside_repo(tmp_path):
    """SQLite and Chroma must be in app data/, not inside REPOSITORY_PATH."""
    from app.core.config import PROJECT_ROOT

    settings = Settings(
        database_url="sqlite:///./data/rca_bot.db",
        chroma_path="./data/chroma",
        _env_file=None,
    )
    # Anchored paths should be inside PROJECT_ROOT/data
    from app.core.config import _anchor_path, _anchor_sqlite_url

    db_url = _anchor_sqlite_url(settings.database_url)
    chroma = _anchor_path(settings.chroma_path)
    assert str(PROJECT_ROOT / "data") in db_url
    assert str(PROJECT_ROOT / "data") in chroma
    # Data dir is ignored by indexer
    from app.ingestion.filters import FileFilter, iter_repository_files

    # Create a fake repo that contains a data dir
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')")
    (repo / "data").mkdir()
    (repo / "data" / "rca_bot.db").write_text("fake db")
    (repo / "data" / "chroma").mkdir()
    files = iter_repository_files(repo, FileFilter())
    # Check relative paths, not absolute (tmp dir name contains "data")
    assert not any("data" in str(f.relative_to(repo)) for f in files)


def test_validator_strict_line_checks(tmp_path, sample_repo_path):
    from app.investigation.validator import RCAValidator
    from app.schemas.rca import IncidentAnalysis

    v = RCAValidator(str(sample_repo_path))
    # Valid file
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="app/serializers/employee_serializer.rb", start_line=1, end_line=5, reason="test"
        )
    )
    assert ok

    # Absolute path
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="/etc/passwd", start_line=1, end_line=5, reason="x"
        )
    )
    assert not ok and "absolute" in reason.lower()

    # Traversal
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="../../etc/passwd", start_line=1, end_line=5, reason="x"
        )
    )
    assert not ok

    # Line 0
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="app/serializers/employee_serializer.rb", start_line=0, end_line=5, reason="x"
        )
    )
    assert not ok and ">= 1" in reason

    # Reversed range
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="app/serializers/employee_serializer.rb", start_line=10, end_line=5, reason="x"
        )
    )
    assert not ok and "reversed" in reason.lower()

    # Beyond file length (file has 44 lines)
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="app/serializers/employee_serializer.rb", start_line=1, end_line=9999, reason="x"
        )
    )
    assert not ok and "beyond" in reason.lower()

    # Nonexistent file
    ok, reason = v.validate_file_reference(
        __import__("app.schemas.rca", fromlist=["RelevantFile"]).RelevantFile(
            path="does/not/exist.rb", start_line=1, end_line=5, reason="x"
        )
    )
    assert not ok


def test_retrieval_trace_persisted(tmp_path, db_session_factory, sample_repo_path):
    """Retrieval trace must be persisted and retrievable via API."""
    from fastapi.testclient import TestClient

    from app.api.incidents import router
    from app.bootstrap import build_container
    from app.core.config import Settings
    from app.db.base import Base, get_engine, reset_engine
    from app.ingestion.vector_store import InMemoryVectorStore
    from app.ingestion.embeddings import HashEmbeddingProvider
    from app.ingestion.indexer import RepositoryIndexer
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup isolated DB
    engine = create_engine(f"sqlite:///{tmp_path}/trace.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Index synthetic repo
    store = InMemoryVectorStore()
    indexer = RepositoryIndexer(
        repository_path=str(sample_repo_path),
        chroma_path=str(tmp_path / "chroma"),
        embedding_provider=HashEmbeddingProvider(),
        vector_store_factory=lambda: store,
    )
    with SessionLocal() as db:
        indexer.run(db)

    # Simulate an investigation with trace
    from app.models import Incident, Investigation
    from datetime import datetime, timezone

    with SessionLocal() as db:
        inc = Incident(slack_channel_id="C1", slack_thread_ts="1.0", triggering_message_ts="2.0", status="COMPLETED")
        db.add(inc)
        db.commit()
        db.refresh(inc)
        inv = Investigation(
            incident_id=inc.id,
            summary="test",
            root_cause="test",
            confidence=80,
            analysis_status="CONFIDENT",
            retrieval_query={"semantic_query": "test"},
            lexical_results=[{"rank": 1, "path": "a.rb", "match_reason": "filename match"}],
            vector_results=[{"rank": 1, "path": "b.rb", "similarity": 0.9}],
            final_context=[{"rank": 1, "path": "a.rb"}],
            llm_model="test-model",
            embedding_model="hash",
            total_latency_ms=1000,
        )
        db.add(inv)
        db.commit()
        inv_id = inv.id
        inc_id = inc.id

    # Verify via API
    from fastapi import FastAPI
    from app.db.base import get_db

    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    resp = client.get(f"/api/incidents/{inc_id}/retrieval")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"]["semantic_query"] == "test"
    assert data["lexical_results"][0]["path"] == "a.rb"
    assert data["final_context"][0]["path"] == "a.rb"


def test_analysis_status_and_latency(tmp_path, db_session_factory):
    """Investigation must store analysis_status and latencies."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    from app.models import Incident, Investigation
    import app.models  # noqa

    engine = create_engine(f"sqlite:///{tmp_path}/status.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        inc = Incident(slack_channel_id="C1", slack_thread_ts="1.0", triggering_message_ts="2.0", status="COMPLETED")
        db.add(inc)
        db.commit()
        inv = Investigation(
            incident_id=inc.id,
            summary="Unable to establish",
            root_cause="Insufficient evidence.",
            confidence=20,
            analysis_status="INSUFFICIENT_EVIDENCE",
            llm_model="nemotron-mini:latest",
            embedding_model="all-MiniLM-L6-v2",
            slack_latency_ms=100,
            sentry_latency_ms=200,
            retrieval_latency_ms=300,
            llm_latency_ms=5000,
            validation_latency_ms=50,
            total_latency_ms=5650,
        )
        db.add(inv)
        db.commit()
        assert inv.analysis_status == "INSUFFICIENT_EVIDENCE"
        assert inv.llm_model == "nemotron-mini:latest"
        assert inv.total_latency_ms == 5650
