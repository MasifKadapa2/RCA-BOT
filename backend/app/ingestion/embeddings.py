"""Embedding providers.

Production uses sentence-transformers locally (no network calls at inference
time). Tests use a deterministic hash-based provider so no model download is
ever required.
"""

from __future__ import annotations

import hashlib
import math
import threading
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import EmbeddingError


class EmbeddingProvider(Protocol):
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class SentenceTransformerProvider:
    """Local embeddings via sentence-transformers (lazy loaded, cached)."""

    _models: dict[str, object] = {}
    _lock = threading.Lock()

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    @classmethod
    def _load(cls, model_name: str):
        with cls._lock:
            if model_name not in cls._models:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:  # pragma: no cover
                    raise EmbeddingError(
                        "sentence-transformers is not installed"
                    ) from exc
                cls._models[model_name] = SentenceTransformer(model_name)
            return cls._models[model_name]

    @property
    def dimension(self) -> int:
        if self._model is None:
            # Standard MiniLM dimension; computed properly after load.
            return 384 if "mini" in self.model_name.lower() else 768
        return int(getattr(self._model, "get_sentence_embedding_dimension", lambda: 384)())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            model = self._model or self._load(self.model_name)
            self._model = model
            vectors = model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return [list(map(float, vector)) for vector in vectors]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc


class HashEmbeddingProvider:
    """Deterministic bag-of-words hashing embedding for tests and offline use."""

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        import re

        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            tokens = self._tokenize(text)[:512]
            for token in tokens:
                digest = hashlib.md5(token.encode()).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(component * component for component in vector)) or 1.0
            vectors.append([component / norm for component in vector])
        return vectors


_provider_cache: dict[str, EmbeddingProvider] = {}


def get_embedding_provider(model_name: str | None = None) -> EmbeddingProvider:
    name = model_name or get_settings().embedding_model
    if name == "hash":
        return HashEmbeddingProvider()
    if name not in _provider_cache:
        _provider_cache[name] = SentenceTransformerProvider(name)
    return _provider_cache[name]
