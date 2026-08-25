"""Sentry Web API client.

All Sentry access flows through :class:`SentryClient`; no other module calls
the Sentry HTTP API. The auth token never leaves this class and is never
logged.
"""

from __future__ import annotations

import httpx

from app.core.errors import (
    SentryAPIError,
    SentryAuthenticationError,
    SentryEventNotFoundError,
    SentryIssueNotFoundError,
    SentryNotFoundError,
)

__all__ = [
    "SentryClient",
    "SentryAPIError",
    "SentryAuthenticationError",
    "SentryEventNotFoundError",
    "SentryIssueNotFoundError",
    "SentryNotFoundError",
]
from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 30


class SentryClient:
    def __init__(
        self,
        auth_token: str,
        organization: str,
        base_url: str = "https://sentry.io/api/0",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._organization = organization
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "User-Agent": "rca-bot/1.0",
        }
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # --- Public API ----------------------------------------------------------

    def get_issue(self, issue_id: str) -> dict:
        return self._request("GET", f"/issues/{issue_id}/", not_found_cls=SentryIssueNotFoundError)

    def get_issue_events(self, issue_id: str, limit: int = 25) -> list[dict]:
        data = self._request(
            "GET",
            f"/issues/{issue_id}/events/",
            params={"perPage": min(limit, 100), "full": "true"},
            not_found_cls=SentryIssueNotFoundError,
        )
        if isinstance(data, list):
            return data
        return []

    def get_latest_event(self, issue_id: str) -> dict:
        return self._request(
            "GET",
            f"/organizations/{self._organization}/issues/{issue_id}/events/latest/",
            not_found_cls=SentryEventNotFoundError,
        )

    def get_event(self, event_id: str, issue_id: str | None = None) -> dict:
        if issue_id:
            path = f"/organizations/{self._organization}/issues/{issue_id}/events/{event_id}/"
        else:
            # Without an issue id the event can only be fetched per project;
            # try the org-scoped lookup first.
            path = f"/organizations/{self._organization}/events/{event_id}/"
        return self._request("GET", path, not_found_cls=SentryEventNotFoundError)

    def extract_stacktrace(self, event: dict) -> list[dict]:
        """Return raw frame dicts from the most relevant stacktrace entry."""
        from app.sentry.normalization import extract_raw_frames

        return extract_raw_frames(event)

    def extract_exception(self, event: dict) -> dict | None:
        from app.sentry.normalization import extract_primary_exception

        return extract_primary_exception(event)

    def extract_metadata(self, event: dict) -> dict:
        from app.sentry.normalization import extract_event_metadata

        return extract_event_metadata(event)

    # --- Internals -------------------------------------------------------------

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 not_found_cls=Exception) -> dict | list:
        logger.info("rca_bot.sentry_request", extra={"method": method, "path": path})
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            raise SentryAPIError(f"Sentry request failed: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            raise SentryAuthenticationError(
                f"Sentry rejected credentials (HTTP {response.status_code}). "
                "Check SENTRY_AUTH_TOKEN and its scopes."
            )
        if response.status_code == 404:
            raise not_found_cls(f"Sentry resource not found: {path}")
        if response.status_code == 429:
            raise SentryAPIError("Sentry rate limit exceeded; retry later.")
        if response.status_code >= 400:
            raise SentryAPIError(f"Sentry API error HTTP {response.status_code}")

        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise SentryAPIError("Sentry returned invalid JSON") from exc
