"""Sentry reference resolver tests (URL formats)."""

from __future__ import annotations

import pytest

from app.core.errors import MissingSentryReferenceError
from app.sentry.resolver import SentryReferenceResolver


@pytest.fixture()
def resolver():
    return SentryReferenceResolver()


class TestSentryURLParsing:
    def test_subdomain_saas_url(self, resolver):
        ref = resolver.resolve("check https://acme.sentry.io/issues/123456/ please")
        assert ref is not None
        assert ref.issue_id == "123456"
        assert ref.host == "acme.sentry.io"
        assert ref.organization_slug == "acme"

    def test_organizations_path_url(self, resolver):
        ref = resolver.resolve("https://sentry.io/organizations/acme/issues/98765/")
        assert ref.issue_id == "98765"
        assert ref.organization_slug == "acme"

    def test_self_hosted_url(self, resolver):
        ref = resolver.resolve("https://sentry.corp.internal/issues/42")
        assert ref is not None
        assert ref.issue_id == "42"
        assert ref.host == "sentry.corp.internal"
        # Single path segment on non-sentry.io hosts is NOT treated as org.
        assert ref.organization_slug is None

    def test_url_with_event_id(self, resolver):
        ref = resolver.resolve(
            "https://acme.sentry.io/issues/123456/events/abcdef1234567890abcdef1234567890/"
        )
        assert ref.issue_id == "123456"
        assert ref.event_id == "abcdef1234567890abcdef1234567890"

    def test_url_in_markdown_link(self, resolver):
        ref = resolver.resolve("<https://acme.sentry.io/issues/777/|View in Sentry>")
        assert ref.issue_id == "777"

    def test_no_sentry_url_returns_none(self, resolver):
        assert resolver.resolve("no links here") is None
        assert resolver.resolve("https://github.com/acme/repo/issues/999") is None

    def test_resolve_from_messages_finds_first_match(self, resolver):
        messages = [
            "nothing relevant",
            "alert https://acme.sentry.io/issues/111/",
            "also https://acme.sentry.io/issues/222/",
        ]
        ref = resolver.resolve_from_messages(messages)
        assert ref.issue_id == "111"

    def test_resolve_from_empty_messages_raises(self, resolver):
        with pytest.raises(MissingSentryReferenceError):
            resolver.resolve_from_messages([])
        with pytest.raises(MissingSentryReferenceError):
            resolver.resolve_from_messages(["no sentry link here"])
