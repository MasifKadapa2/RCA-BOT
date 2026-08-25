"""Abstraction over the Slack Web API.

The rest of the application depends on :class:`SlackGateway` (a small
protocol) rather than ``slack_sdk`` directly, which keeps services unit
testable without real credentials.
"""

from __future__ import annotations

from typing import Any, Protocol

from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient

from app.core.errors import (
    SlackAPIError,
    SlackAuthenticationError,
    SlackThreadNotFoundError,
)


class SlackGateway(Protocol):
    """Minimal Slack operations used by RCA Bot."""

    def fetch_thread_messages(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]: ...

    def post_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


class _ErrorMapper:
    KNOWN_ERRORS = (
        "invalid_auth",
        "account_inactive",
        "token_revoked",
        "not_authed",
        "thread_not_found",
        "message_not_found",
    )

    @classmethod
    def translate(cls, exc: SlackApiError) -> SlackAPIError:
        error = cls._extract_error(exc)
        if error in {"invalid_auth", "account_inactive", "token_revoked", "not_authed"}:
            return SlackAuthenticationError(f"Slack authentication failed: {error}")
        if error in {"thread_not_found", "message_not_found"}:
            return SlackThreadNotFoundError("Slack thread not found")
        return SlackAPIError(f"Slack API error: {error or exc}")

    @staticmethod
    def _extract_error(exc: SlackApiError) -> str:
        response = getattr(exc, "response", None)
        getter = getattr(response, "get", None)
        if callable(getter):
            value = getter("error", "")
            if value:
                return str(value)
        data = getattr(response, "data", None)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
        text = str(exc)
        for known in _ErrorMapper.KNOWN_ERRORS:
            if known in text:
                return known
        return ""


class WebClientGateway:
    """Production gateway backed by slack_sdk's WebClient."""

    def __init__(self, client: WebClient, max_thread_messages: int = 200) -> None:
        self._client = client
        self._max = max_thread_messages

    def fetch_thread_messages(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor: str | None = None
        try:
            while True:
                response = self._client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=min(200, self._max - len(messages)) if self._max > len(messages) else 1,
                    cursor=cursor,
                )
                data = response.data if hasattr(response, "data") else dict(response)  # type: ignore
                batch = data.get("messages", []) or []
                messages.extend(batch)
                if len(messages) >= self._max:
                    messages = messages[: self._max]
                    break
                cursor = (data.get("response_metadata") or {}).get("next_cursor")
                if not cursor or not data.get("has_more"):
                    break
        except SlackApiError as exc:
            raise _ErrorMapper.translate(exc) from exc
        # De-duplicate on ts while keeping order.
        seen: set[str] = set()
        unique = []
        for m in messages:
            ts = str(m.get("ts"))
            if ts not in seen:
                seen.add(ts)
                unique.append(m)
        return unique

    def post_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "thread_ts": thread_ts,
            "text": text,
            "unfurl_links": False,
        }
        if blocks:
            kwargs["blocks"] = blocks
        try:
            resp = self._client.chat_postMessage(**kwargs)
            data = resp.data if hasattr(resp, "data") else dict(resp)  # type: ignore
            return dict(data)
        except SlackApiError as exc:
            raise _ErrorMapper.translate(exc) from exc


class InMemoryGateway:
    """Test double recording posted replies.

    Unknown threads return an empty list, mirroring conversations.replies
    behaviour for non-thread root messages.
    """

    def __init__(self, threads: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
                 fail_with: Exception | None = None) -> None:
        self.threads = threads or {}
        self.fail_with = fail_with
        self.posted: list[dict[str, Any]] = []

    def fetch_thread_messages(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        if self.fail_with is not None:
            raise self.fail_with
        return [dict(m) for m in self.threads.get((channel_id, thread_ts), [])]

    def post_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "text": text,
            "blocks": blocks,
        }
        self.posted.append(payload)
        return {"ok": True, **payload}
