"""Slack app_mention event handling.

The Bolt listener stays thin: it normalizes the event and delegates to
:class:`MentionHandler`, which owns validation, idempotency and persistence,
then kicks off an investigation in a background thread so Socket Mode acks
immediately.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import SlackError
from app.core.logging import get_logger
from app.services.incident_repository import (
    DuplicateInvestigationError,
    IncidentRepository,
)
from app.slack.formatter import (
    FAILURE_PREFIX,
    PROGRESS_TEXT,
    SlackResponseFormatter,
)
from app.slack.thread_context import ThreadContextService

logger = get_logger(__name__)

StartInvestigationFn = Callable[[int], None]


def spawn_thread_starter(run_fn: StartInvestigationFn) -> StartInvestigationFn:
    """Wrap an investigation runner so it executes on a background thread."""

    def starter(incident_id: int) -> None:
        thread = threading.Thread(target=run_fn, args=(incident_id,), daemon=True)
        thread.start()

    return starter


class MentionHandler:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        gateway,
        resolver,  # SentryReferenceResolver
        formatter: SlackResponseFormatter,
        start_investigation: StartInvestigationFn | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._gateway = gateway
        self._resolver = resolver
        self._formatter = formatter
        self._thread_service = ThreadContextService(gateway)
        self._start_investigation = start_investigation or (lambda incident_id: None)

    @property
    def alert_channel(self) -> str:
        return self._settings.slack_alert_channel_id

    def handle(self, event: dict[str, Any]) -> None:
        """Process one ``app_mention`` event end-to-end (except the LLM work)."""
        channel_id = str(event.get("channel", ""))
        message_ts = str(event.get("ts", ""))
        user_id = str(event.get("user", ""))

        if not self._is_allowed_channel(channel_id):
            logger.info(
                "rca_bot.mention_ignored_channel", extra={"channel": channel_id, "user": user_id}
            )
            self._safe_post(channel_id, str(event.get("thread_ts") or message_ts),
                            self._formatter.channel_not_allowed_blocks())
            return

        if not message_ts:
            logger.warning("rca_bot.mention_missing_ts")
            return

        thread_ts = self._thread_service.resolve_thread_ts(event.get("thread_ts"), message_ts)
        logger.info(
            "rca_bot.mention_received",
            extra={"channel": channel_id, "thread_ts": thread_ts, "user": user_id},
        )

        with self._session_factory() as db:
            repo = IncidentRepository(db)
            existing = repo.find_by_trigger(channel_id, message_ts)
            if existing is not None:
                logger.info("rca_bot.duplicate_mention", extra={"incident_id": existing.id})
                self._post_blocks(channel_id, thread_ts, self._formatter.already_investigating_blocks())
                return

        try:
            thread_ctx = self._thread_service.fetch(channel_id, thread_ts, triggering_event=event)
        except SlackError as exc:
            logger.error("rca_bot.thread_fetch_failed", extra={"error": str(exc)})
            self._post_failure(channel_id, thread_ts, str(exc))
            return

        try:
            with self._session_factory() as db:
                repo = IncidentRepository(db)
                incident = repo.create_incident(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    triggering_message_ts=message_ts,
                )
                repo.persist_slack_messages(incident, thread_ctx.messages, channel_id)
                incident_id = incident.id
        except DuplicateInvestigationError:
            self._post_blocks(channel_id, thread_ts, self._formatter.already_investigating_blocks())
            return

        self._post_blocks(
            channel_id,
            thread_ts,
            self._formatter.progress_blocks(),
            fallback=PROGRESS_TEXT,
        )

        self._start_investigation(incident_id)
        logger.info(
            "rca_bot.investigation_started",
            extra={"correlation_id": f"inv_{incident_id}", "incident_id": incident_id},
        )

    # --- internals ----------------------------------------------------------

    def _is_allowed_channel(self, channel_id: str) -> bool:
        return bool(self.alert_channel) and channel_id == self.alert_channel

    def _first_section_text(self, blocks: list[dict] | None) -> str:
        for block in blocks or []:
            if block.get("type") == "section":
                text = block.get("text", {})
                if isinstance(text, dict):
                    return str(text.get("text", ""))
        return ""

    def _post_blocks(self, channel_id: str, thread_ts: str, blocks: list[dict],
                     fallback: str | None = None) -> None:
        try:
            self._gateway.post_reply(
                channel_id,
                thread_ts,
                text=fallback or self._first_section_text(blocks),
                blocks=blocks,
            )
        except SlackError:
            logger.exception("rca_bot.slack_post_failed")

    def _post_failure(self, channel_id: str, thread_ts: str, reason: str) -> None:
        self._post_blocks(
            channel_id,
            thread_ts,
            self._formatter.failure_blocks(reason),
            fallback=f"{FAILURE_PREFIX}\nReason:\n{reason}",
        )

    def _safe_post(self, channel_id: str, thread_ts: str, blocks: list[dict]) -> None:
        try:
            self._post_blocks(channel_id, thread_ts, blocks)
        except Exception:  # pragma: no cover - defensive
            logger.exception("rca_bot.unexpected_post_error")
