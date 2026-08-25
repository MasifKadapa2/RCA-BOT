"""Phase 7: end-to-end mocked investigation pipeline.

Slack event -> thread retrieval -> Sentry API -> repository retrieval ->
LLM -> RCA validation -> persistence -> Slack response.
No real Slack/Sentry/Ollama credentials are used anywhere in this module.
"""

from __future__ import annotations

import json

import pytest

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


def _index_sample_repo(session, sample_repo_path):
    from app.ingestion.indexer import RepositoryIndexer
    from tests.unit.test_phase4_ingestion import CountingStore

    store = CountingStore()
    indexer = RepositoryIndexer(
        repository_path=sample_repo_path,
        chroma_path="/tmp/unused-chroma",
        embedding_provider=FakeEmbeddingProvider(),
        vector_store_factory=lambda: store,
    )
    indexer.run(session)
    return store


@pytest.fixture()
def wired(sample_repo_path, settings, db_session_factory, db_session, fake_llm, fake_sentry_client):
    """Full wiring of handler + investigator with every external service faked."""
    from tests.conftest import FakeEmbeddingProvider as Embedder  # re-use

    store = _index_sample_repo(db_session, sample_repo_path)

    gateway = InMemoryGateway({
        (ALERT_CHANNEL, THREAD_TS): [
            {"ts": THREAD_TS, "text": f":rotating_light: NoMethodError {ISSUE_URL}"},
            {"ts": "1756056300.000200", "thread_ts": THREAD_TS,
             "text": "seeing 500s on GET /api/v1/employees/:id again"},
        ],
    })
    formatter = SlackResponseFormatter(settings)
    retriever = CodeRetriever(
        db_session_factory=db_session_factory,
        embedding_provider=Embedder(),
        vector_store_factory=lambda: store,
        max_results=5,
        repository_id=None,
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
        "investigator": investigator,
        "llm": fake_llm,
        "sentry": fake_sentry_client,
        "settings": settings,
        "session_factory": db_session_factory,
    }


def _mention_event(ts: str = THREAD_TS) -> dict:
    return {"channel": ALERT_CHANNEL, "ts": ts, "thread_ts": None,
            "user": "UDEV", "text": "<@UBOT> investigate"}


def test_full_pipeline_completes_and_posts_rca_in_same_thread(wired):
    wired["handler"].handle(_mention_event())

    # Incident persisted and completed
    with wired["session_factory"]() as db:
        from sqlalchemy import select

        from app.models import Evidence, Incident, Investigation

        incident = db.scalars(select(Incident)).one()
        assert incident.status == IncidentStatus.COMPLETED
        assert incident.sentry_issue_id == "123456"

        investigation = db.scalars(select(Investigation)).one()
        assert investigation.summary
        assert investigation.confidence == 82
        assert investigation.completed_at is not None

        evidence_rows = db.scalars(select(Evidence)).all()
        assert evidence_rows, "evidence must be persisted"

    # Sentry was actually consulted through the client abstraction
    assert ("get_issue", "123456") in wired["sentry"].calls
    assert any(c[0] == "get_latest_event" for c in wired["sentry"].calls)

    # Slack received progress + final RCA blocks in the SAME thread
    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("investigating this incident" in t for t in texts)
    rca_posts = [
        p for p in wired["gateway"].posted
        if p.get("blocks") and "Root Cause" in json.dumps(p["blocks"])
    ]
    assert rca_posts, "expected Block Kit RCA response"
    assert all(p["thread_ts"] == THREAD_TS for p in rca_posts)
    joined_blocks = json.dumps(rca_posts[0]["blocks"])
    assert "Incident Investigation" in joined_blocks or "Root Cause" in joined_blocks
    # Hallucinated file reference from the canned LLM response was removed
    assert "does/not/exist.rb" not in joined_blocks
    # Disclaimer present
    assert "AI-generated" in joined_blocks


def test_duplicate_trigger_does_not_reinvestigate(wired):
    event = _mention_event()
    wired["handler"].handle(event)

    calls_after_first = len(wired["sentry"].calls)
    wired["handler"].handle(event)

    with wired["session_factory"]() as db:
        from sqlalchemy import func, select

        from app.models import Incident

        count = db.scalar(select(func.count()).select_from(Incident))
    assert count == 1
    assert len(wired["sentry"].calls) == calls_after_first
    # duplicate notice posted
    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("already been triggered" in t or "already running" in t for t in texts)


def test_mention_outside_alert_channel_is_refused(wired):
    event = {"channel": "C_OTHER", "ts": "999.1", "thread_ts": None,
             "user": "UDEV", "text": "<@UBOT> investigate"}
    wired["handler"].handle(event)

    with wired["session_factory"]() as db:
        from sqlalchemy import select

        from app.models import Incident

        incidents = db.scalars(select(Incident)).all()
    assert incidents == []
    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("not configured for RCA investigations" in t for t in texts)


def test_missing_sentry_link_posts_guidance_not_guesses(wired):
    gateway = InMemoryGateway({
        (ALERT_CHANNEL, THREAD_TS): [
            {"ts": THREAD_TS, "text": "alert without any link, sorry"},
            {"ts": "1756056300.000200", "thread_ts": THREAD_TS,
             "text": "<@UBOT> investigate"},
        ],
    })
    wired["gateway"].threads = gateway.threads
    wired["handler"].handle(_mention_event())

    with wired["session_factory"]() as db:
        from sqlalchemy import select

        from app.models import Incident

        incident = db.scalars(select(Incident)).one()
        assert incident.status == IncidentStatus.FAILED

    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("couldn't find a Sentry issue link" in t for t in texts)


def test_llm_failure_marks_incident_failed_and_reports_safely(wired):
    class ExplodingLLM(FakeLLMProvider):
        def generate_structured(self, prompt, schema, system=None):
            raise RuntimeError("boom")

    wired["investigator"]._llm_provider = ExplodingLLM()
    wired["handler"].handle(_mention_event())

    with wired["session_factory"]() as db:
        from sqlalchemy import select

        from app.models import Incident

        incident = db.scalars(select(Incident)).one()
        assert incident.status == IncidentStatus.FAILED
        assert incident.error_message

    texts = [p["text"] for p in wired["gateway"].posted]
    assert any("failed" in t.lower() for t in texts)


def test_progress_and_result_are_thread_scoped(wired):
    wired["handler"].handle(_mention_event())
    for post in wired["gateway"].posted:
        assert post["thread_ts"] == THREAD_TS
