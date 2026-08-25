"""LLM provider abstraction.

The application never talks to Ollama directly — it uses `LLMProvider`
so alternative local providers can be added without touching the
investigation logic.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMParseError(LLMError):
    """Raised when the model keeps returning malformed/unvalidatable output."""


class LLMProvider(Protocol):
    def generate(self, prompt: str, system: str | None = None) -> str: ...

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        max_retries: int = 2,
    ) -> T: ...
