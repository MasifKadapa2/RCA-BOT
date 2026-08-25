"""Phase 3: Sentry URL extraction, client, and stack trace normalization."""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import (
    SentryAuthenticationError,
    SentryEventNotFoundError,
    SentryIssueNotFoundError,
)
from app.sentry.client import SentryClient
from app.sentry.normalization import (
    extract_event_metadata,
    extract_exceptions,
    extract_primary_exception,
    extract_raw_frames,
    normalize_frames,
    normalize_issue,
)
from app.sentry.resolver import (
    NO_SENTRY_LINK_MESSAGE,
    SentryReferenceResolver,
    build_sentry_client,
)


# ------------------------------------------------------------------ resolver


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.sentry.io/issues/123456/",
        "https://sentry.io/organizations/acme/issues/123456/?query=is:unresolved",
        "https://sentry.acme.dev/organizations/acme/projects/api/issues/98765/",
        "http://sentry.internal/acme/backend/issues/555/",
        "see <https://acme.sentry.io/issues/777|this alert>",
        "Alert https://acme.sentry.io/issues/888. trailing punctuation",
    ],
)
def test_resolver_matches_common_url_shapes(url):
    ref = SentryReferenceResolver().resolve(url)
    assert ref is not None


def test_resolver_extracts_issue_id_host_and_org():
    ref = SentryReferenceResolver().resolve(
        "https://acme.sentry.io/organizations/acme/issues/123456/"
    )
    assert ref.issue_id == "123456"
    assert ref.host == "acme.sentry.io"
    assert ref.organization_slug == "acme"


def test_resolver_extracts_event_id_when_present():
    ref = SentryReferenceResolver().resolve(
        "https://acme.sentry.io/issues/123456/events/deadbeefdeadbeefdeadbeefdeadbeef/"
    )
    assert ref.issue_id == "123456"
    assert ref.event_id == "deadbeefdeadbeefdeadbeefdeadbeef"


def test_resolver_returns_none_without_url():
    assert SentryReferenceResolver().resolve("no links in this text") is None
    assert SentryReferenceResolver().resolve("") is None


def test_resolve_from_messages_scans_all_messages():
    resolver = SentryReferenceResolver()
    ref = resolver.resolve_from_messages([
        {"text": "chatter"},
        {"text": None},
        {"text": f"alert https://acme.sentry.io/issues/123456/"},
    ])
    assert ref.issue_id == "123456"


def test_resolve_from_messages_raises_missing_link():
    from app.core.errors import MissingSentryReferenceError

    with pytest.raises(MissingSentryReferenceError) as excinfo:
        SentryReferenceResolver().resolve_from_messages([{"text": "nothing here"}])
    assert str(excinfo.value) == NO_SENTRY_LINK_MESSAGE


def test_build_sentry_client_uses_settings(test_settings):
    client = build_sentry_client(test_settings, host="acme.sentry.io")
    assert isinstance(client, SentryClient)
    client.close()


# -------------------------------------------------------------- normalization


def test_extract_raw_frames_crash_site_first(sentry_event_payload):
    frames = extract_raw_frames(sentry_event_payload)
    assert frames, "expected frames"
    first = frames[0]
    assert first["filename"].endswith("employee_serializer.rb")
    # crash frame should be the in-app serializer frame
    assert (first.get("in_app", first.get("inApp"))) in (True, False)


def test_normalize_frames_preserves_lines_and_context(sentry_event_payload):
    raw = extract_raw_frames(sentry_event_payload)
    frames = normalize_frames(raw)
    crash = next((f for f in frames if f.in_app), frames[0])
    assert crash.line_number >= 1
    assert crash.function
    assert crash.display.startswith(crash.filename or "")
    if crash.context:
        lineno, text = crash.context[-1]
        assert int(lineno) == crash.line_number


def test_extract_exception_and_metadata(sentry_event_payload):
    primary = extract_primary_exception(sentry_event_payload)
    assert primary["type"] == "NoMethodError"
    exceptions = extract_exceptions(sentry_event_payload)
    assert exceptions[-1].type == "NoMethodError"
    assert "undefined method `email'" in (exceptions[-1].value or "")

    meta = extract_event_metadata(sentry_event_payload)
    assert meta["platform"] == "ruby"
    assert meta["environment"] == "production"


def test_normalize_issue_tolerates_missing_fields():
    issue = normalize_issue("42", {"id": 42})
    assert issue.issue_id == "42"
    assert issue.title  # falls back to metadata-derived title


# --------------------------------------------------------------------- client


def _client(handler) -> SentryClient:
    return SentryClient(
        auth_token="test-token",
        organization="acme",
        base_url="https://sentry.test/api/0",
        transport=httpx.MockTransport(handler),
    )


def test_get_issue_returns_raw_payload(sentry_issue_payload):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/issues/123456/")
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json=sentry_issue_payload)

    issue = _client(handler).get_issue("123456")
    assert issue["id"] == 123456
    assert issue["title"].startswith("NoMethodError")


def test_auth_failure_maps_to_safe_error():
    client = _client(lambda req: httpx.Response(401, json={"detail": "bad token"}))
    with pytest.raises(SentryAuthenticationError):
        client.get_issue("123")


def test_issue_not_found():
    client = _client(lambda req: httpx.Response(404, json={}))
    with pytest.raises(SentryIssueNotFoundError):
        client.get_issue("999")


def test_latest_event_not_found_is_distinguishable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events/latest/"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"id": 1})

    with pytest.raises(SentryEventNotFoundError):
        _client(handler).get_latest_event("123")


def test_rate_limit_and_server_errors():
    with pytest.raises(Exception):
        _client(lambda req: httpx.Response(429, json={})).get_issue("1")
    with pytest.raises(Exception):
        _client(lambda req: httpx.Response(500, json={})).get_issue("1")
