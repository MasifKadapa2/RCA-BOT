"""LLM abstraction.

The application only talks to :class:`LLMProvider`; Ollama is one
implementation and can be swapped without touching investigation logic.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMMalformedOutputError, LLMResponseError

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Return raw completion text."""

    def generate_structured(self, prompt: str, schema: type[T], system: str | None = None) -> T:
        """Generate and validate a Pydantic model from JSON output.

        Retries once with corrective feedback when the first reply is
        malformed, which materially improves small local models.
        """
        feedback = ""
        last_error: Exception | None = None
        for attempt in range(2):
            attempt_prompt = prompt
            if feedback:
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply was invalid: {feedback}\n"
                    "Respond again with ONLY the corrected JSON object."
                )
            raw = self.generate(attempt_prompt, system=system)
            try:
                return parse_structured_output(raw, schema)
            except LLMMalformedOutputError as exc:
                last_error = exc
                feedback = str(exc)[:400]
        raise last_error or LLMMalformedOutputError("LLM output was invalid")


def parse_structured_output(raw: str, schema: type[T]) -> T:
    payload = extract_json_object(raw)
    if payload is None:
        raise LLMMalformedOutputError("LLM output did not contain a JSON object")
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise LLMMalformedOutputError(f"LLM JSON failed schema validation: {exc.error_count()} errors") from exc


def extract_json_object(raw: str) -> dict | None:
    """Extract the first JSON object from an LLM response (tolerates fences)."""
    text = (raw or "").strip()
    if not text:
        return None
    # Strip markdown fences if present.
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fallback: find outermost braces.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


__all__ = [
    "LLMProvider",
    "LLMResponseError",
    "parse_structured_output",
    "extract_json_object",
]
