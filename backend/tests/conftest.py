"""Shared fixtures: fake Sentry payloads, sample repository, in-memory infra."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES_DIR / "sample_repo"

# Ensure backend package is importable regardless of invocation directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name: str) -> dict:
    with (FIXTURES_DIR / f"{name}.json").open() as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def sample_repo_path() -> str:
    return str(SAMPLE_REPO)


@pytest.fixture()
def sentry_issue_payload() -> dict:
    return _load("sentry_issue")


@pytest.fixture()
def sentry_event_payload() -> dict:
    return _load("sentry_event")


@pytest.fixture()
def slack_thread_payload() -> list[dict]:
    return _load("slack_thread")


@pytest.fixture()
def engine(tmp_path):
    """A fresh SQLite engine per test."""
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app import models  # noqa: F401 - register mappers

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session_factory(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db_session(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


# Aliases used by phase tests.
@pytest.fixture()
def session(db_session):
    return db_session


@pytest.fixture()
def test_settings(settings):
    return settings


@pytest.fixture()
def fake_embedder():
    return FakeEmbeddingProvider()


@pytest.fixture()
def settings(db_session, tmp_path, sample_repo_path):
    from app.core.config import Settings

    return Settings(
        slack_bot_token="xoxb-test-token",
        slack_app_token="xapp-test-token",
        slack_alert_channel_id="C000ALERTS",
        sentry_auth_token="sntrys-test-token",
        sentry_org="acme",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2:3b",
        embedding_model="hash",
        database_url=f"sqlite:///{tmp_path}/test.db",
        chroma_path=str(tmp_path / "chroma"),
        repository_path=sample_repo_path,
        log_level="WARNING",
        max_retrieval_results=5,
        max_llm_context_chars=12000,
        confidence_threshold=60,
        _env_file=None,
    )


class FakeEmbeddingProvider:
    dimension = 64

    def embed_texts(self, texts):
        import hashlib
        import math
        import re

        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())[:256]:
                digest = hashlib.md5(token.encode()).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


class FakeLLMProvider:
    """Returns a canned valid RCA; records the last prompt for assertions."""

    def __init__(self):
        self.last_prompt = ""
        self.last_system = ""
        self.response: dict | None = None

    def generate(self, prompt: str, system: str | None = None) -> str:
        import json as _json

        self.last_prompt = prompt
        self.last_system = system or ""
        return _json.dumps(self.response or DEFAULT_RCA)

    def generate_structured(self, prompt: str, schema, system: str | None = None):
        from app.llm.provider import parse_structured_output

        raw = self.generate(prompt, system=system)
        return parse_structured_output(raw, schema)


DEFAULT_RCA = {
    "summary": "EmployeeSerializer crashes when an employee has no associated User record.",
    "root_cause": (
        "EmployeeSerializer#serialize accesses @employee.user.email without checking "
        "whether user is nil. For employees created without a user account "
        "(e.g. Bob), user is nil and calling .email raises NoMethodError."
    ),
    "evidence": [
        {
            "source_type": "SENTRY",
            "source_reference": "123456",
            "explanation": "NoMethodError: undefined method `email' for nil",
        },
        {
            "source_type": "CODE",
            "source_reference": "app/serializers/employee_serializer.rb:27-32",
            "explanation": "serialize accesses @employee.user.email directly.",
        },
        {
            "source_type": "SLACK",
            "source_reference": "thread discussion mentions the API response failing",
            "explanation": "Developer reported 500 responses on the employees endpoint.",
        },
    ],
    "affected_components": ["EmployeeSerializer", "EmployeesController"],
    "suggested_fix": (
        "Guard the association before access, e.g. use @employee.user&.email or handle nil explicitly."
    ),
    "confidence": 82,
    "unknowns": [
        "The exact reason the User association is missing could not be established."
    ],
    "relevant_files": [
        {
            "path": "app/serializers/employee_serializer.rb",
            "start_line": 21,
            "end_line": 32,
            "reason": "Contains the crashing serialize method.",
        },
        {
            "path": "does/not/exist.rb",
            "start_line": 1,
            "end_line": 5,
            "reason": "Hallucinated file that validation must reject.",
        },
    ],
}


@pytest.fixture()
def fake_llm():
    provider = FakeLLMProvider()
    provider.response = json.loads(json.dumps(DEFAULT_RCA))
    return provider


@pytest.fixture()
def fake_sentry_client(sentry_issue_payload, sentry_event_payload):
    class FakeSentryClient:
        def __init__(self):
            self.calls: list[tuple] = []
            self.issue_not_found_ids: set[str] = set()

        def get_issue(self, issue_id):
            self.calls.append(("get_issue", issue_id))
            if issue_id in self.issue_not_found_ids:
                from app.core.errors import SentryIssueNotFoundError

                raise SentryIssueNotFoundError(f"Issue {issue_id} not found")
            payload = dict(sentry_issue_payload)
            payload["id"] = issue_id
            return payload

        def get_latest_event(self, issue_id):
            self.calls.append(("get_latest_event", issue_id))
            return dict(sentry_event_payload)

        def get_issue_events(self, issue_id, limit=25):
            self.calls.append(("get_issue_events", issue_id))
            return [dict(sentry_event_payload)]

    return FakeSentryClient()


os.environ.setdefault("PYTEST_CURRENT_TEST", "1")
