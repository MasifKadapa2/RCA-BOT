"""Groq Cloud provider — free, OpenAI-compatible, no local load.

Uses https://api.groq.com/openai/v1/chat/completions
Free tier: 14.4k req/day, no card. Falls back to Ollama if no key.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import LLMResponseError, LLMUnavailableError
from app.core.logging import get_logger
from app.llm.provider import LLMProvider

logger = get_logger(__name__)

GROQ_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "groq/compound"
TIMEOUT_SECONDS = 60


class GroqProvider(LLMProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.groq_api_key:
            raise LLMResponseError("GROQ_API_KEY is not configured")
        self._settings = settings
        self._model = settings.groq_model or DEFAULT_MODEL
        self._client = httpx.Client(
            base_url=GROQ_BASE,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
        }

        try:
            resp = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Groq unreachable: {type(exc).__name__}") from exc

        if resp.status_code == 401:
            raise LLMResponseError("Groq authentication failed — check GROQ_API_KEY")
        if resp.status_code == 429 or "rate_limit_exceeded" in resp.text:
            raise LLMResponseError(
                "Groq free-tier limit hit (8k tokens/min). "
                "Reduce MAX_LLM_CONTEXT_CHARS in .env or wait a minute."
            )
        if resp.status_code >= 400:
            raise LLMResponseError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"Groq returned unexpected shape: {exc}") from exc

        if not content or not content.strip():
            raise LLMResponseError("Groq returned empty content")
        return str(content)
