"""Mocked end-to-end integration tests.

Full pipeline with REAL SQLite + Chroma (tmp dirs) and FAKE Slack/Sentry/LLM:

    Slack event → thread retrieval → Sentry API (fake) → repository retrieval
    (real index) → LLM (fake) → RCA validation → persistence → dashboard API
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.investigation.investigator import IncidentInvestigator
from app.investigation.validator import RCAValidator
from app.models import IncidentStatus
from app.retrieval.retriever import CodeRetriever
from app.sentry.resolver import SentryReferenceResolver
from app.slack.formatter import SlackResponseFormatter
from app.slack.gateway import InMemoryGateway
from app.slack.handlers import MentionHandler
from app.slack.responder import SlackThreadResponder
from tests.conftest import FakeEmbeddingProvider, FakeLLMProvider

ALERT_CHANNEL = "C000ALERTS"
THREAD_TS = "1756056000.000100"
ISSUE_URL = "https://acme.sentry.io/issues/123456/"


@pytest.fixture()
def chroma_store(tmp_path):
    from app.ingestion.vector_store import ChromaVectorStore

    return ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))


@pytest.fixture()
def wired(sample_repo_path, settings, db_session_factory, fake_llm, fake_sentry_client, chroma_store):
    store = chroma_store
    from app.ingestion.indexer import RepositoryIndexer

    indexer = RepositoryIndexer(
        repository_path=sample_repo_path,
        chroma_path="unused",
        embedding_provider=FakeEmbeddingProvider(),
        vector_store_factory=lambda: store,
    )
    with db_session_factory() as db:
        indexer.run(db)

    gateway = InMemoryGateway({
        (ALERT_CHANNEL, THREAD_TS): [
            {"ts": THREAD_TS, "text": f":rotating_light: NoMethodError {ISSUE_URL}"},
            {
                "ts": "1756056300.000200",
                "thread_ts": THREAD_TS,
                "text": "seeing 500s on GET /api/v1/employees/:id again",
            },
        ]
    })
    formatter = SlackResponseFormatter(settings)
    retriever = CodeRetriever(
        db_session_factory=db_session_factory,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store_factory=lambda: store,
        max_results=5,
        max_context_chars=12000,
    )
    responder = SlackThreadResponder(gateway, formatter)
    investigator = IncidentInvestigator(
        settings=settings,
        session_factory=db_session_factory,
        sentry_client_factory=lambda: fake_sentry_client,
        resolver=SentryReferenceResolver(),
        code_retriever=retriever,
        llm_provider=fake_llm,
        validator=RCAValidator(sample_repo_path),
        responder=responder,
    )
    handler = MentionHandler(
        settings=settings,
        session_factory=db_session_factory,
        gateway=gateway,
        resolver=SentryReferenceResolver(),
        formatter=formatter,
        start_investigation=investigator.run,
    )
    return {
        "handler": handler,
        "gateway": gateway,
        "llm": fake_llm,
        "sentry": fake_sentry_client,
        "settings": settings,
        "session_factory": db_session_factory,
        "store": store,
    }


def test_full_pipeline_with_real_chroma_and_sqlite(wired, tmp_path, monkeypatch, sample_repo_path):
    wired["handler"].handle({"channel": ALERT_CHANNEL, "ts": THREAD_TS, "thread_ts": None,
                             "user": "UDEV", "text": "<@UBOT> investigate"})

    # --- persisted state -----------------------------------------------------
    with wired["session_factory"]() as db:
        from sqlalchemy import select

        from app.models import Evidence, Incident, Investigation

        incident = db.scalars(select(Incident)).one()
        assert incident.status == IncidentStatus.COMPLETED
        assert incident.sentry_issue_id == "123456"
        assert incident.context["frames"], "stack trace must be stored on the incident"

        investigation = db.scalars(select(Investigation)).one()
        assert investigation.confidence == 82
        assert investigation.completed_at is not None

        assert len(db.scalars(select(Evidence)).all()) >= 2

    # --- Slack responses in same thread --------------------------------------
    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("investigating this incident" in t for t in texts)
    rca_posts = [p for p in wired["gateway"].posted if p.get("blocks")]
    assert rca_posts and all(p["thread_ts"] == THREAD_TS for p in rca_posts)
    final_rca = next(
        (p for p in rca_posts if "Incident Investigation" in json.dumps(p["blocks"])),
        None,
    )
    assert final_rca is not None, "expected the Block Kit RCA response"
    blocks_json = json.dumps(final_rca["blocks"])
    assert "does/not/exist.rb" not in blocks_json  # hallucination removed
    assert "AI-generated" in blocks_json

    # --- dashboard API shows the investigation --------------------------------
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    # Reuse the SAME engine by pointing the API at an app that shares it.
    from fastapi import FastAPI

    from app.api import incidents as incidents_api
    from app.db.base import get_db

    def override_get_db():
        yield next(iter([wired["session_factory"]()]))

    api_app = FastAPI()
    api_app.include_router(incidents_api.router)
    api_app.dependency_overrides[get_db] = override_get_db

    client = TestClient(api_app)
    stats = client.get("/api/stats").json()
    assert stats["total_investigations"] == 1
    assert stats["completed_investigations"] == 1
    assert stats["average_confidence"] == 82.0

    listing = client.get("/api/incidents").json()
    assert listing[0]["status"] == "COMPLETED"
    assert listing[0]["sentry_issue_id"] == "123456"

    detail = client.get(f"/api/incidents/{listing[0]['id']}").json()
    assert detail["sentry_title"].startswith("NoMethodError")
    assert detail["investigation"]["root_cause"]
    assert any(ev["source_type"] == "CODE" for ev in detail["evidence"])
    stack_files = [f["filename"] for f in detail["stack_trace"]]
    assert "app/serializers/employee_serializer.rb" in stack_files
    assert any(
        rf["path"] == "app/serializers/employee_serializer.rb" for rf in detail["relevant_files"]
    )

    evidence_endpoint = client.get(f"/api/incidents/{listing[0]['id']}/evidence").json()
    assert evidence_endpoint


def test_chroma_store_roundtrip(chroma_store, tmp_path):
    ids = ["v1", "v2"]
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    docs = ["alpha", "beta"]
    metas = [{"relative_path": "a.rb", "start_line": 1}, {"relative_path": "b.rb", "start_line": 5}]
    chroma_store.add(ids, vectors, docs, metas)
    assert chroma_store.count() == 2

    hits = chroma_store.query(vector=[1.0, 0.05, 0.0], top_k=2)
    assert hits[0]["id"] == "v1"

    chroma_store.delete(ids=["v1"])
    assert chroma_store.count() == 1

    # Persistence across instances (same directory).
    from app.ingestion.vector_store import ChromaVectorStore

    reopened = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"))
    assert reopened.count() == 1


def test_llm_prompt_contains_grounding_rules(wired):
    wired["handler"].handle({"channel": ALERT_CHANNEL, "ts": THREAD_TS, "thread_ts": None,
                             "user": "UDEV", "text": "<@UBOT> investigate"})
    prompt = wired["llm"].last_prompt
    system = wired["llm"].last_system or ""
    assert "NEVER invent source files" in system or "Only use provided evidence" in system
    # Retrieved code blocks are numbered so the model can cite them.
    assert "[C1]" in prompt
    assert "employee_serializer.rb" in prompt
