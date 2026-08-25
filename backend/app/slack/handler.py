"""Bolt wiring for ``app_mention`` events + Socket Mode entrypoint.

The listener stays thin: it normalizes the event and delegates to
:class:`MentionHandler`, which owns validation, idempotency and persistence,
then kicks off an investigation on a background thread so Socket Mode acks
immediately.
"""

from __future__ import annotations

import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.bootstrap import Container, run_investigation
from app.core.logging import get_logger, setup_logging
from app.db.base import init_db
from app.slack.formatter import (
    CHANNEL_NOT_CONFIGURED_TEXT,
    DUPLICATE_REQUEST_TEXT,
    FAILURE_PREFIX,
    NO_SENTRY_LINK_TEXT,
    PROGRESS_TEXT,
)
from app.slack.gateway import WebClientGateway
from app.slack.handlers import MentionHandler, spawn_thread_starter

logger = get_logger(__name__)

__all__ = [
    "CHANNEL_NOT_CONFIGURED_TEXT",
    "DUPLICATE_REQUEST_TEXT",
    "FAILURE_PREFIX",
    "NO_SENTRY_LINK_TEXT",
    "PROGRESS_TEXT",
    "create_bolt_app",
    "start_socket_mode",
]


def create_bolt_app(container: Container) -> App:
    """Create a Bolt App with the app_mention listener wired to production deps."""
    settings = container.settings
    if not settings.is_slack_configured:
        raise SystemExit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be configured.")

    app = App(token=settings.slack_bot_token, process_before_response=True)

    gateway = WebClientGateway(
        client=app.client,
        max_thread_messages=settings.slack_max_thread_messages,
    )
    handler = MentionHandler(
        settings=settings,
        session_factory=container.session_factory,
        gateway=gateway,
        resolver=container.resolver,
        formatter=container.formatter,
        start_investigation=spawn_thread_starter(
            lambda incident_id: run_investigation(container, incident_id)
        ),
    )

    @app.event("app_mention")
    def handle_app_mention(event):
        try:
            handler.handle(event)
        except Exception:  # never crash the Slack process
            logger.exception("rca_bot.unhandled_event_error")

    return app


def start_socket_mode(container: Container) -> None:
    """Blocking Socket Mode loop."""
    setup_logging(container.settings.log_level)
    init_db()

    if not container.settings.slack_alert_channel_id:
        raise SystemExit(
            "SLACK_ALERT_CHANNEL_ID is required (the channel Sentry alerts arrive in)."
        )

    bolt_app = create_bolt_app(container)
    logger.info(
        "rca_bot.socket_mode_starting",
        extra={"channel": container.settings.slack_alert_channel_id},
    )
    SocketModeHandler(bolt_app, container.settings.slack_app_token).start()
