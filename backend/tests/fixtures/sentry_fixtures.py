"""Shared Sentry/Slack fixtures used across test modules."""

SENTRY_ISSUE_URL = "https://acme.sentry.io/issues/123456/"

SLACK_ALERT_MESSAGE = (
    f"[Acme] NoMethodError in production\n"
    f"{SENTRY_ISSUE_URL}\n"
    "2 events in the last hour"
)

RAW_SENTRY_ISSUE = {
    "id": 123456,
    "shortId": "ACME-1A2",
    "title": "NoMethodError: undefined method `email' for nil",
    "culprit": "app/serializers/employee_serializer.rb in `serialize`",
    "level": "error",
    "status": "unresolved",
    "project": {"slug": "backend", "name": "Backend"},
    "metadata": {"type": "NoMethodError", "value": "undefined method `email' for nil"},
    "count": "42",
    "userCount": 3,
    "firstSeen": "2026-08-23T10:00:00Z",
    "lastSeen": "2026-08-24T09:30:00Z",
    "permalink": SENTRY_ISSUE_URL,
}

_RAW_FRAME_CRASH = {
    "filename": "app/serializers/employee_serializer.rb",
    "absPath": "/srv/app/app/serializers/employee_serializer.rb",
    "function": "serialize",
    "module": "EmployeeSerializer",
    "lineno": 42,
    "inApp": True,
    "contextLine": '      email = employee.user.email',
    "preContext": ["    def serialize(employee)", "      return {} if employee.nil?"],
    "postContext": ["    end"],
}

_RAW_FRAME_GEM = {
    "filename": "gems/activesupport-7.1/lib/active_support/notifications.rb",
    "absPath": "/usr/local/bundle/gems/activesupport-7.1/lib/active_support/notifications.rb",
    "function": "instrument",
    "module": "ActiveSupport::Notifications",
    "lineno": 308,
    "inApp": False,
}

RAW_SENTRY_EVENT = {
    "event_id": "deadbeefdeadbeefdeadbeefdeadbeef",
    "dateCreated": "2026-08-24T09:29:58Z",
    "platform": "ruby",
    "environment": "production",
    "release": "web@2026.34.1",
    "transaction": "/api/v1/employees",
    "level": "error",
    "tags": [
        {"key": "environment", "value": "production"},
        {"key": "browser", "value": "Chrome 128"},
    ],
    "entries": [
        {
            "type": "exception",
            "data": {
                "values": [
                    {
                        "type": "NoMethodError",
                        "value": "undefined method `email' for nil",
                        "module": "",
                        "mechanism": {"type": "runtime_error"},
                        "stacktrace": {"frames": [_RAW_FRAME_GEM, _RAW_FRAME_CRASH]},
                    }
                ]
            },
        }
    ],
}


def sentry_issue_payload() -> dict:
    import copy

    return copy.deepcopy(RAW_SENTRY_ISSUE)


def sentry_event_payload() -> dict:
    import copy

    return copy.deepcopy(RAW_SENTRY_EVENT)
