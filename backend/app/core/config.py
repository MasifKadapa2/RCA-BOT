"""Application configuration.

All configuration comes from environment variables (optionally a `.env` file).
The `.env` is discovered at the repository root OR the backend directory so
processes behave identically no matter where they are started from. Relative
storage paths (sqlite URL, CHROMA_PATH) are anchored to the repository root
for the same reason.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# backend/app/core/config.py -> parents[2] = backend/, parents[3] = repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SQLITE_PREFIX = "sqlite:///"


def _anchor_sqlite_url(url: str) -> str:
    """Anchor relative sqlite file URLs to the project root."""
    if url.startswith(_SQLITE_PREFIX):
        remainder = url[len(_SQLITE_PREFIX):]
        if remainder.startswith("."):
            resolved = (PROJECT_ROOT / remainder).resolve()
            return f"{_SQLITE_PREFIX}{resolved}"
    return url


def _anchor_path(value: str) -> str:
    """Anchor a relative filesystem path to the project root."""
    if not value:
        return value
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_alert_channel_id: str = ""

    # Sentry
    sentry_auth_token: str = ""
    sentry_org: str = ""
    sentry_base_url: str = "https://sentry.io/api/0"

    # Ollama / LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""
    # Optional cloud LLM (free) — when set, takes precedence over Ollama to avoid local load
    groq_api_key: str = ""
    groq_model: str = "groq/compound"
    huggingface_token: str = ""
    # Generic OpenAI-compatible cloud LLM (Google Gemini recommended — 1M TPM free)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # Embeddings
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    # Storage
    database_url: str = "sqlite:///./data/rca_bot.db"
    chroma_path: str = "./data/chroma"

    # Repository
    repository_path: str = ""

    # Tuning
    log_level: str = "INFO"
    max_retrieval_results: int = 10
    max_llm_context_chars: int = 24000
    confidence_threshold: int = 60

    # Indexing tuning (not required by spec but keeps laptops happy)
    max_file_size_bytes: int = 512_000
    max_chunk_chars: int = 4_000

    # Slack behaviour
    slack_max_thread_messages: int = 200

    @property
    def is_slack_configured(self) -> bool:
        return bool(self.slack_bot_token and self.slack_app_token)

    @property
    def is_sentry_configured(self) -> bool:
        return bool(self.sentry_auth_token and self.sentry_org)

    @property
    def is_ollama_configured(self) -> bool:
        return bool(self.ollama_model)

    @property
    def is_groq_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def is_cloud_llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url)

    @property
    def is_llm_configured(self) -> bool:
        return (
            self.is_cloud_llm_configured or self.is_groq_configured or self.is_ollama_configured
        )

    @property
    def effective_llm_model(self) -> str:
        if self.is_cloud_llm_configured:
            return self.llm_model
        if self.is_groq_configured:
            return self.groq_model or "groq/compound"
        return self.ollama_model


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
