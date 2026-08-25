"""Ollama implementation of LLMProvider (local inference via HTTP)."""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMParseError, LLMProvider, LLMUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 180.0,
        temperature: float = 0.1,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._temperature = temperature

    # -- public -------------------------------------------------------------

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload = {
            "model": self._model,
            "messages": [
                *[{"role": "system", "content": system} for _ in [1] if system],
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "text",
            "options": {"temperature": self._temperature},
        }
        return self._chat(payload)

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        max_retries: int = 2,
    ) -> T:
        last_error: Exception | None = None
        feedback = ""
        for attempt in range(max_retries + 1):
            retry_note = (
                f"\n\nYour previous reply was invalid: {feedback}\n"
                "Reply again with ONLY the corrected JSON object."
                if feedback
                else ""
            )
            payload = {
                "model": self._model,
                "messages": [
                    *([{"role": "system", "content": system}] if system else []),
                    {"role": "user", "content": prompt + retry_note},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": self._temperature},
            }
            text = self._chat(payload)
            try:
                data = json.loads(text)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                feedback = str(exc)[:500]
                logger.warning(
                    "llm_output_validation_failed",
                    extra={"attempt": attempt + 1, "error": type(exc).__name__},
                )
        raise LLMParseError(f"LLM failed to produce valid {schema.__name__}: {last_error}")

    # -- internals -----------------------------------------------------------

    def _chat(self, payload: dict) -> str:
        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMUnavailableError(
                f"Ollama is unreachable at {self._base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama request failed: {type(exc).__name__}") from exc

        if response.status_code == 404:
            raise LLMUnavailableError(
                f"Ollama model '{self._model}' not found. Pull it with: ollama pull {self._model}"
            )
        if response.status_code >= 400:
            raise LLMUnavailableError(f"Ollama returned HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMParseError("Ollama returned a non-JSON response") from exc

        message = body.get("message") or {}
        content = message.get("content") or body.get("response")
        if content is None:
            raise LLMParseError("Ollama response contained no message content")
        return content.strip()


def create_ollama_provider(base_url: str, model: str) -> LLMProvider:
    return OllamaProvider(base_url=base_url, model=model)
