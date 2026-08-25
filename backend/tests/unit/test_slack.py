"""Slack integration unit tests: gateway, thread resolution, mention handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from slack_sdk.errors import SlackApiError

from app.core.errors import SlackAuthenticationError, SlackThreadNotFoundError
from app.sentry.resolver import SentryReferenceResolver
from app.slack.formatter import SlackResponseFormatter
from app.slack.gateway import InMemoryGateway
from app.slack.handlers import MentionHandler
from app.slack.thread_context import ThreadContextService

ALERT_CHANNEL = "C000ALERTS"
THREAD_TS = "1756056000.000100"


@pytest.fixture()
def formatter(settings):
    return SlackResponseFormatter(settings)


@pytest.fixture()
def gateway(slack_thread_payload):
    return InMemoryGateway({(ALERT_CHANNEL, THREAD_TS): slack_thread_payload})


@pytest.fixture()
def started_ids():
    return []


@pytest.fixture()
def handler(settings, db_session_factory, gateway, formatter, started_ids):
    def start(incident_id: int) -> None:
        started_ids.append(incident_id)

    return MentionHandler(
        settings=settings,
        session_factory=db_session_factory,
        gateway=gateway,
        resolver=SentryReferenceResolver(),
        formatter=formatter,
        start_investigation=start,
    )


class TestThreadContextService:
    def test_root_mention_uses_own_ts(self):
        service = ThreadContextService(gateway=None)
        assert service.resolve_thread_ts(None, "100.1") == "100.1"

    def test_thread_mention_uses_parent_ts(self):
        service = ThreadContextService(gateway=None)
        assert service.resolve_thread_ts("99.9", "100.1") == "99.9"

    def test_fetch_returns_full_thread(self, gateway):
        service = ThreadContextService(gateway)
        ctx = service.fetch(ALERT_CHANNEL, THREAD_TS)
        assert len(ctx.messages) == 3
        assert "NoMethodError" in ctx.root_text

    def test_fetch_missing_thread_raises(self):
        service = ThreadContextService(InMemoryGateway(fail_with=SlackThreadNotFoundError("x")))
        with pytest.raises(SlackThreadNotFoundError):
            service.fetch("CX", "123.456")

    def test_non_thread_root_returns_empty_and_handler_uses_trigger_event(self, handler, started_ids):
        # A root-level mention (no thread) still works using the event itself.
        handler.handle({"channel": ALERT_CHANNEL, "ts": "1756056700.002000", "user": "UDEV2"})
        assert len(started_ids) == 1


class TestChannelRestriction:
    def test_mention_outside_alert_channel_is_not_investigated(self, handler, started_ids):
        handler.handle(
            {"channel": "C999OTHER", "ts": "111.1", "user": "U1", "text": "<@UBOT> investigate"}
        )
        assert started_ids == []
        posted = [p for p in handler._gateway.posted if p["channel_id"] == "C999OTHER"]
        assert posted and "not configured" in posted[0]["text"]


class TestMentionHandler:
    event = {
        "channel": ALERT_CHANNEL,
        "ts": "1756056500.000900",
        "thread_ts": THREAD_TS,
        "user": "UDEV1",
        "text": "<@UBOT> investigate",
    }

    def test_valid_mention_creates_incident_and_starts_investigation(
        self, handler, db_session, started_ids
    ):
        handler.handle(dict(self.event))
        assert len(started_ids) == 1

        from app.models import Incident

        incident = db_session.get(Incident, started_ids[0])
        assert incident is not None
        assert incident.status == "PENDING"
        assert incident.triggering_message_ts == self.event["ts"]
        assert incident.slack_thread_ts == THREAD_TS

        progress = [p for p in handler._gateway.posted if p["channel_id"] == ALERT_CHANNEL]
        assert any("investigating" in p["text"] for p in progress)

    def test_thread_messages_are_persisted(self, handler, db_session_factory, started_ids):
        from sqlalchemy import select

        from app.models import SlackMessage

        handler.handle(dict(self.event))
        with db_session_factory() as db:
            messages = db.scalars(select(SlackMessage)).all()
        assert len(messages) >= 3  # thread root + replies + trigger mention
        texts = {m.text or "" for m in messages}
        assert any("NoMethodError" in t for t in texts)

    def test_duplicate_mention_does_not_start_second_investigation(
        self, handler, db_session_factory, started_ids
    ):
        event = {"channel": ALERT_CHANNEL, "ts": "1756056600.001000", "user": "UDEV1"}
        handler.handle(dict(event))
        handler.handle(dict(event))
        assert len(started_ids) == 1
        texts = [p["text"] for p in handler._gateway.posted]
        assert any("already" in t.lower() for t in texts)


class TestGatewayErrors:
    @staticmethod
    def _slack_error(error_code: str) -> SlackApiError:
        return SlackApiError(
            message="api error", response=SimpleNamespace({"error": error_code})
        )

    def test_auth_error_maps_to_authentication_error(self):
        from app.slack.gateway import _ErrorMapper

        translated = _ErrorMapper.translate(self._slack_error("invalid_auth"))
        assert isinstance(translated, SlackAuthenticationError)

    def test_unknown_error_maps_to_generic_api_error(self):
        from app.core.errors import SlackAPIError
        from app.slack.gateway import _ErrorMapper

        translated = _ErrorMapper.translate(self._slack_error("ratelimited"))
        assert isinstance(translated, SlackAPIError)
