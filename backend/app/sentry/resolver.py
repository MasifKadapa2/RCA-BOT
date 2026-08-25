"""Sentry reference extraction from Slack thread content."""

from __future__ import annotations

import re

from app.core.config import Settings
from app.core.errors import MissingSentryReferenceError
from app.schemas.sentry import SentryReference

# Matches Sentry issue URLs across SaaS, custom hosts and self-hosted installs:
#   https://org.sentry.io/issues/123456/
#   https://sentry.io/organizations/org/issues/123456/
#   https://sentry.acme.dev/organizations/acme/projects/api/issues/123456/
#   https://sentry.acme.dev/acme/backend/issues/123456/
#   .../issues/123456/events/abcdef1234567890abcdef1234567890/
_SENTRY_ISSUE_URL = re.compile(
    r"https?://(?P<host>[A-Za-z0-9.\-_]+)(?::\d+)?"
    r"(?P<path>/[^\s<>|]*)?"
    r"/issues/(?P<issue_id>\d+)"
    r"(?:/events/(?P<event_id>[0-9a-fA-F]{16,64}))?",
    re.IGNORECASE,
)

_NO_ORG_HOSTS = {"sentry.io", "www.sentry.io"}

NO_SENTRY_LINK_MESSAGE = (
    "I couldn't find a Sentry issue link in this thread. "
    "Please include the Sentry issue link and mention @rca-bot investigate again."
)

# Public alias used by the investigator/orchestrator.
ResolvedReference = SentryReference


class SentryReferenceResolver:
    """Extracts the first Sentry issue reference from Slack thread text."""

    def resolve_from_messages(self, messages) -> SentryReference:
        """Scan thread messages (dicts or strings, oldest first) for a URL.

        Raises MissingSentryReferenceError when nothing is found - V1 never
        guesses issues from error text.
        """
        for message in messages or []:
            if isinstance(message, dict):
                text = message.get("text") or ""
            else:
                text = str(message or "")
            reference = self.resolve(text)
            if reference is not None:
                return reference
        raise MissingSentryReferenceError(NO_SENTRY_LINK_MESSAGE)

    def resolve(self, text: str) -> SentryReference | None:
        match = _SENTRY_ISSUE_URL.search(_unwrap_slack_markup(text or ""))
        if not match:
            return None
        host = match.group("host").lower()
        raw_path = (match.group("path") or "").rstrip("/")
        segments = [seg for seg in raw_path.split("/") if seg]

        # Only treat the link as Sentry when host or path shape is Sentry-like.
        # This avoids matching e.g. https://github.com/acme/repo/issues/999.
        is_sentry_host = (
            host == "sentry.io"
            or host.endswith(".sentry.io")
            or "sentry" in host.split(".")
        )
        has_org_path = "organizations" in segments
        if not (is_sentry_host or has_org_path):
            return None

        org_slug: str | None = None
        if has_org_path:
            idx = segments.index("organizations")
            org_slug = segments[idx + 1] if len(segments) > idx + 1 else None
        elif host.endswith(".sentry.io"):
            org_slug = host.rsplit(".sentry.io", 1)[0] or None
        elif segments and host not in _NO_ORG_HOSTS:
            # Legacy layout: https://<host>/<org>/<project>/issues/...
            org_slug = segments[0]

        return SentryReference(
            issue_id=match.group("issue_id"),
            url=f"https://{host}/issues/{match.group('issue_id')}/",
            host=host,
            organization_slug=org_slug,
            event_id=match.group("event_id"),
        )


def _unwrap_slack_markup(text: str) -> str:
    """<http://url|label> -> http://url so links inside Slack markup match."""
    return re.sub(r"<([^<>]+)>", lambda m: m.group(1).split("|", 1)[0], text)


def build_sentry_client(settings: Settings, host: str = "") -> "SentryClient":
    """Build a SentryClient bound to the configured org/base URL.

    The host parsed from the Slack link is informational in V1: credentials,
    base URL and organization come exclusively from server configuration.
    """
    from app.sentry.client import SentryClient

    return SentryClient(
        auth_token=settings.sentry_auth_token,
        organization=settings.sentry_org,
        base_url=(settings.sentry_base_url or "https://sentry.io/api/0").rstrip("/"),
    )
