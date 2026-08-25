"""Sentry client + normalization tests (fully mocked HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import (
    SentryAuthenticationError,
    SentryEventNotFoundError,
    SentryIssueNotFoundError,
    SentryAPIError,
)
from app.schemas.sentry import SentryEventData, SentryIssueData
from app.sentry.client import SentryClient
from app.sentry.normalization import (
    extract_event_metadata,
    extract_exceptions,
    extract_raw_frames,
    normalize_frame,
    normalize_frames,
)


def _mock_transport(handler) -> httpx.BaseTransport:
    return httpx.MockTransport(handler)


def make_client(handler, org="acme") -> SentryClient:
    return SentryClient(
        auth_token="sntrys-test",
        organization=org,
        base_url="https://sentry.io/api/0",
        transport=_mock_transport(handler),
    )


class TestSentryClient:
    def test_get_issue_success(self, sentry_issue_payload):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/issues/123456/")
            assert request.headers["Authorization"] == "Bearer sntrys-test"
            return httpx.Response(200, json=sentry_issue_payload)

        client = make_client(handler)
        issue = client.get_issue("123456")
        assert issue["id"] == 123456
        client.close()

    def test_auth_failure_raises_authentication_error(self):
        client = make_client(lambda req: httpx.Response(401, json={"detail": "invalid"}))
        with pytest.raises(SentryAuthenticationError):
            client.get_issue("1")

    def test_missing_issue_raises_not_found(self):
        client = make_client(lambda req: httpx.Response(404, json={"detail": "not found"}))
        with pytest.raises(SentryIssueNotFoundError):
            client.get_issue("999999")

    def test_latest_event_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/issues/123/events/latest/" in request.url.path
            return httpx.Response(404, json={"detail": "no events"})

        client = make_client(handler)
        with pytest.raises(SentryEventNotFoundError):
            client.get_latest_event("123")

    def test_server_error_maps_to_api_error(self):
        client = make_client(lambda req: httpx.Response(500, text="boom"))
        with pytest.raises(SentryAPIError):
            client.get_issue("1")

    def test_rate_limit_maps_to_api_error(self):
        client = make_client(lambda req: httpx.Response(429, json={"detail": "slow down"}))
        with pytest.raises(SentryAPIError):
            client.get_issue("1")


class TestStacktraceExtraction:
    def test_extract_raw_frames_from_exception_entry(self, sentry_event_payload):
        frames = extract_raw_frames(sentry_event_payload)
        assert len(frames) == 3
        functions = [f["function"] for f in frames]
        assert "serialize" in functions

    def test_normalize_frame_fields(self, sentry_event_payload):
        frames = extract_raw_frames(sentry_event_payload)
        crash = next(f for f in frames if f["function"] == "serialize")
        normalized = normalize_frame(crash)
        assert normalized.filename == "app/serializers/employee_serializer.rb"
        assert normalized.line_number == 24
        assert normalized.column_number == 14
        assert normalized.in_app is True
        assert normalized.context_line == "email: @employee.user.email"

    def test_in_app_frames_prioritized_first(self, sentry_event_payload):
        frames = normalize_frames(extract_raw_frames(sentry_event_payload))
        assert all(frame.in_app for frame in frames[:2])
        assert not frames[-1].in_app

    def test_missing_fields_tolerated(self):
        normalized = normalize_frame({"function": "weird"})
        assert normalized.filename is None
        assert normalized.line_number is None

    def test_snake_case_payload_supported(self):
        normalized = normalize_frame(
            {
                "filename": "a.rb",
                "lineno": 10,
                "colno": 3,
                "context_line": "x = 1",
                "in_app": True,
            }
        )
        assert normalized.line_number == 10
        assert normalized.column_number == 3
        assert normalized.context_line == "x = 1"

    def test_empty_event_returns_no_frames(self):
        assert extract_raw_frames({}) == []


class TestExceptionAndMetadata:
    def test_extract_exceptions(self, sentry_event_payload):
        exceptions = extract_exceptions(sentry_event_payload)
        assert exceptions[0].type == "NoMethodError"
        assert "undefined method" in (exceptions[0].value or "")

    def test_extract_metadata(self, sentry_event_payload):
        metadata = extract_event_metadata(sentry_event_payload)
        assert metadata["platform"] == "ruby"
        assert metadata["environment"] == "production"
        assert metadata["release"] == "api@1.42.0"
        assert metadata["transaction"] == "EmployeesController#show"
        assert metadata["tags"]["server_name"] == "web-7f3b"
        # Only non-identifying user info is kept.
        assert "ip_address" not in str(metadata.get("user"))

    def test_context_builder_produces_normalized_context(
        self, sentry_issue_payload, sentry_event_payload
    ):
        from app.investigation.context_builder import IncidentContextBuilder

        issue_data, event_data, context = IncidentContextBuilder().build(
            sentry_issue_payload, sentry_event_payload
        )
        assert isinstance(issue_data, SentryIssueData)
        assert isinstance(event_data, SentryEventData)
        assert issue_data.issue_id == "123456"
        assert issue_data.title.startswith("NoMethodError")
        assert context["exception"]["type"] == "NoMethodError"
        filenames = [f["filename"] for f in context["frames"]]
        assert "app/serializers/employee_serializer.rb" in filenames

    def test_json_fixtures_are_loadable(self, sentry_issue_payload, sentry_event_payload):
        assert json.dumps(sentry_issue_payload)  # smoke: valid JSON round trip
