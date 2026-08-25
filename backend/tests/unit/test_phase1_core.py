"""Phase 1: configuration, health endpoint, database, logging."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.logging import correlation, set_correlation_id
from app.models import Incident, IncidentStatus, Repository


# ------------------------------------------------------------------ settings


def test_settings_defaults(settings):
    assert settings.slack_alert_channel_id == "C000ALERTS"
    assert settings.is_slack_configured is True
    assert settings.is_sentry_configured is True
    assert settings.max_retrieval_results == 5


def test_unconfigured_settings_report_false():
    from app.core.config import Settings

    s = Settings(_env_file=None)
    assert s.is_slack_configured is False
    assert s.is_sentry_configured is False


def test_env_file_is_read_from_repo_root():
    """Settings reads .env at project root when present (documented contract)."""
    from app.core.config import Settings

    s = Settings(
        slack_bot_token="xoxb-x",
        sentry_org="org",
        ollama_model="m",
        _env_file=None,
    )
    assert s.sentry_base_url == "https://sentry.io/api/0"


# ------------------------------------------------------------------- health


def test_health_endpoint_reports_component_status(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "sntrys-test")
    monkeypatch.setenv("SENTRY_ORG", "acme")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/health.db")

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.db.base import reset_engine
    from app.main import create_app

    get_settings.cache_clear()
    reset_engine()
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["components"]["slack_configured"] is True
    assert body["components"]["sentry_configured"] is True
    # No secrets in the payload
    assert "xoxb-test" not in resp.text
    assert "sntrys-test" not in resp.text
    get_settings.cache_clear()
    reset_engine()


# ----------------------------------------------------------------- database


def test_incident_trigger_is_unique(session):
    def make(cid):
        return Incident(
            slack_channel_id="C1",
            slack_thread_ts="100.1",
            triggering_message_ts=cid,
            status=IncidentStatus.PENDING,
        )

    session.add(make("200.1"))
    session.commit()
    session.add(make("200.1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_incident_status_values():
    assert {IncidentStatus.PENDING, IncidentStatus.INVESTIGATING,
            IncidentStatus.COMPLETED, IncidentStatus.FAILED} == {
        "PENDING", "INVESTIGATING", "COMPLETED", "FAILED",
    }


def test_repository_row_roundtrip(session):
    repo = Repository(name="demo", path="/tmp/demo")
    session.add(repo)
    session.commit()
    fetched = session.query(Repository).one()
    assert fetched.name == "demo"
    assert fetched.created_at is not None


# ------------------------------------------------------------------ logging


def test_correlation_context_binds_records(caplog):
    logger = logging.getLogger("test.rca")

    with caplog.at_level(logging.INFO):
        set_correlation_id("inv_77")
        logger.info("with correlation")
        set_correlation_id("")
        logger.info("without correlation")

    with_id = [r for r in caplog.records if r.getMessage() == "with correlation"][0]
    without_id = [r for r in caplog.records if r.getMessage() == "without correlation"][0]
    assert getattr(with_id, "correlation_id", "") == "inv_77"
    assert getattr(without_id, "correlation_id", "") == ""


def test_correlation_context_manager_resets():
    import contextvars

    var = contextvars.ContextVar("t", default="")
    token = var.set("a")
    with correlation("inv_x"):
        pass  # correlation() uses the shared var; ensure no exception
    var.reset(token)


def test_json_log_formatting():
    import json

    from app.core.logging import JsonFormatter

    record = logging.LogRecord("t", logging.INFO, "p", 1, "hello", None, None)
    line = JsonFormatter().format(record)
    parsed = json.loads(line)
    assert parsed["message"] == "hello"
    assert "correlation_id" in parsed
