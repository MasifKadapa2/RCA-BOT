"""Vector store abstraction backed by ChromaDB (local persistent client)."""

from typing import Any, Protocol

from app.core.errors import VectorStoreError
from app.core.logging import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "code_chunks"


class VectorStore(Protocol):
    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    def delete(self, ids: list[str]) -> None: ...

    def query(
        self, embedding: list[float], n_results: int, where: dict | None = None
    ) -> list[dict]: ...

    def count(self) -> int: ...


class ChromaVectorStore:
    """ChromaDB persistent store. One collection holds all repositories;
    repository scoping is done through metadata `where` filters."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._client = None
        self._collection = None

    def _ensure_collection(self):
        if self._collection is None:
            try:
                import chromadb

                self._client = chromadb.PersistentClient(path=self._path)
                self._collection = self._client.get_or_create_collection(COLLECTION_NAME)
            except Exception as exc:
                raise VectorStoreError(
                    f"Could not initialise Chroma at {self._path}: {type(exc).__name__}"
                ) from exc
        return self._collection

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        if not ids:
            return
        collection = self._ensure_collection()
        try:
            collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        except Exception as exc:
            raise VectorStoreError(f"Chroma upsert failed: {type(exc).__name__}") from exc

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            collection = self._ensure_collection()
            collection.delete(ids=ids)
        except Exception as exc:
            raise VectorStoreError(f"Chroma delete failed: {type(exc).__name__}") from exc

    def query(self, embedding, n_results, where: dict | None = None) -> list[dict]:
        collection = self._ensure_collection()
        kwargs: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": max(1, min(n_results, 200)),
            "include": ["metadatas", "documents", "distances"],
        }
        if where:
            kwargs["where"] = where
        try:
            result = collection.query(**kwargs)
        except Exception as exc:
            raise VectorStoreError(f"Chroma query failed: {type(exc).__name__}") from exc

        hits: list[dict] = []
        ids = result.get("ids") or [[]]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        for i, vector_id in enumerate(ids[0]):
            distance = float(distances[i]) if i < len(distances) else 1.0
            hits.append(
                {
                    "id": vector_id,
                    "score": 1.0 - distance,  # cosine similarity for normalised vectors
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    "document": documents[i] if i < len(documents) else "",
                }
            )
        return hits

    def count(self) -> int:
        collection = self._ensure_collection()
        return collection.count()
