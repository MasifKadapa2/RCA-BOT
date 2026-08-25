"""Retrieval schemas."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RetrievalMethod(str, Enum):
    EXACT = "exact"
    VECTOR = "vector"


class RetrievalResult(BaseModel):
    """A single retrieved code block with full provenance."""

    source_type: str = "CODE"
    relative_path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    content: str
    score: float = 0.0
    retrieval_method: RetrievalMethod = Field(default=RetrievalMethod.EXACT)

    model_config = {"use_enum_values": True, "extra": "ignore"}


class RetrievalQuery(BaseModel):
    """Queries derived from incident context."""

    semantic_query: str = ""
    exact_filenames: list[str] = Field(default_factory=list)
    exact_symbols: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
