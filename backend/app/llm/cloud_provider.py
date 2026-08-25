"""Generic OpenAI-compatible cloud LLM provider.

Works with any OpenAI-shaped /chat/completions endpoint:
- Google Gemini: https://generativelanguage.googleapis.com/v1beta/openai
- Groq:          https://api.groq.com/openai/v1
- OpenRouter, Together, etc.

Configure via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import LLMResponseError, LLMUnavailableError
from app.core.logging import get_logger
from app.llm.provider import LLMProvider

logger = get_logger(__name__)

TIMEOUT_SECONDS = 90


class OpenAICompatProvider(LLMProvider):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.llm_api_key or not settings.llm_base_url:
            raise LLMResponseError("LLM_API_KEY / LLM_BASE_URL not configured")
        if not settings.llm_model:
            raise LLMResponseError("LLM_MODEL is not configured")
        self._settings = settings
        self._model = settings.llm_model
        self._base_url = settings.llm_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    @property
    def model_name(self) -> str:
        return self._model

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
            "response_format": {"type": "json_object"},
        }

        try:
            resp = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cloud LLM unreachable at {self._base_url}: {type(exc).__name__}"
            ) from exc

        if resp.status_code == 401:
            raise LLMResponseError(f"{self._base_url} rejected credentials — check LLM_API_KEY")
        if resp.status_code == 404:
            raise LLMResponseError(f"Model '{self._model}' not found at {self._base_url}")
        if resp.status_code == 429 or "rate_limit_exceeded" in resp.text or "quota" in resp.text.lower():
            raise LLMResponseError(
                "Cloud LLM rate/quota limit hit — wait a minute or lower MAX_LLM_CONTEXT_CHARS."
            )
        if resp.status_code >= 400:
            raise LLMResponseError(f"Cloud LLM HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"Unexpected LLM response shape: {exc}") from exc

        if not content or not content.strip():
            raise LLMResponseError("Cloud LLM returned empty content")
        return str(content)
