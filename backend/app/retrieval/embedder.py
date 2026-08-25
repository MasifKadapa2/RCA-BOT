"""Embedding provider abstraction.

sentence-transformers is imported lazily so the application (and the test
suite) can run without downloading model weights until embeddings are
actually needed.
"""

from typing import Protocol

from app.core.errors import EmbeddingError
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    @property
    def model_name(self) -> str: ...


class SentenceTransformerEmbedder:
    """Local embeddings via sentence-transformers. Runs fully offline."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"loading embedding model model={self._model_name}")
                self._model = SentenceTransformer(self._model_name)
            except Exception as exc:
                raise EmbeddingError(
                    f"Failed to load embedding model {self._model_name}: {type(exc).__name__}"
                ) from exc
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
