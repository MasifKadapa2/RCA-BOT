"""Ollama implementation of LLMProvider (local HTTP, no paid APIs)."""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import LLMUnavailableError, LLMResponseError
from app.core.logging import get_logger
from app.llm.provider import LLMProvider

logger = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 300


class OllamaProvider(LLMProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.ollama_model:
            raise LLMResponseError("OLLAMA_MODEL is not configured")
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {
            "model": self._settings.ollama_model,
            "messages": [],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2,
                "num_ctx": 8192,
            },
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})

        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Ollama unreachable at {self._settings.ollama_base_url}: {type(exc).__name__}"
            ) from exc

        if response.status_code == 404:
            raise LLMResponseError(
                f"Ollama model '{self._settings.ollama_model}' not found. "
                "Run: ollama pull <model>"
            )
        if response.status_code >= 400:
            raise LLMResponseError(f"Ollama returned HTTP {response.status_code}")

        data = response.json()
        message = data.get("message") or {}
        content = message.get("content") or data.get("response") or ""
        if not content.strip():
            raise LLMResponseError("Ollama returned an empty response")
        return str(content)
