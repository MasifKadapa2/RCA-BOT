"""Vector store abstraction.

Production uses ChromaDB (local persistence). Tests use an in-memory store.
"""

from __future__ import annotations

import math
import threading
from typing import Any, Protocol

from app.core.errors import VectorStoreError

COLLECTION_NAME = "rca_code_chunks"


class VectorStore(Protocol):
    def add(
        self,
        ids: list[str],
        vectors: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    def delete(self, where: dict[str, Any] | None = None, ids: list[str] | None = None) -> None: ...

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def count(self) -> int: ...


class ChromaVectorStore:
    """ChromaDB-backed vector store with cosine similarity."""

    def __init__(self, persist_directory: str) -> None:
        self._persist_directory = persist_directory
        self._collection = None
        self._lock = threading.Lock()

    def _get_collection(self):
        if self._collection is None:
            try:
                import chromadb

                client = chromadb.PersistentClient(path=self._persist_directory)
                self._collection = client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as exc:
                raise VectorStoreError(f"Chroma unavailable: {type(exc).__name__}") from exc
        return self._collection

    def add(self, ids, vectors, documents, metadatas) -> None:
        if not ids:
            return
        try:
            collection = self._get_collection()
            with self._lock:
                # Upsert semantics keep re-indexing idempotent even if deletes lag.
                collection.upsert(ids=ids, embeddings=vectors, documents=documents, metadatas=metadatas)
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Chroma add failed: {type(exc).__name__}") from exc

    def delete(self, where=None, ids=None) -> None:
        try:
            collection = self._get_collection()
            kwargs: dict[str, Any] = {}
            if where:
                kwargs["where"] = where
            if ids:
                kwargs["ids"] = ids
            if not kwargs:
                return
            with self._lock:
                collection.delete(**kwargs)
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Chroma delete failed: {type(exc).__name__}") from exc

    def query(self, vector, top_k=10, where=None) -> list[dict[str, Any]]:
        try:
            collection = self._get_collection()
            result = collection.query(
                query_embeddings=[vector],
                n_results=max(1, min(top_k, collection.count() or 1)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Chroma query failed: {type(exc).__name__}") from exc

        hits: list[dict[str, Any]] = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for i, hit_id in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 1.0
            similarity = max(0.0, 1.0 - distance)  # cosine distance -> similarity
            hits.append(
                {
                    "id": hit_id,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "similarity": similarity,
                }
            )
        return hits

    def count(self) -> int:
        try:
            return int(self._get_collection().count())
        except VectorStoreError:
            return 0


class InMemoryVectorStore:
    """Brute-force cosine store for tests."""

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._docs: dict[str, str] = {}
        self._metas: dict[str, dict[str, Any]] = {}

    @property
    def dimension(self) -> int:
        for vector in self._vectors.values():
            return len(vector)
        return 0

    def add(self, ids, vectors, documents, metadatas) -> None:
        for chunk_id, vector, document, metadata in zip(ids, vectors, documents, metadatas):
            self._vectors[chunk_id] = list(vector)
            self._docs[chunk_id] = document
            self._metas[chunk_id] = dict(metadata)

    def delete(self, where=None, ids=None) -> None:
        to_remove: set[str] = set()
        if ids:
            to_remove.update(i for i in ids if i in self._vectors)
        if where:
            for chunk_id, metadata in list(self._metas.items()):
                if all(metadata.get(k) == v for k, v in where.items()):
                    to_remove.add(chunk_id)
        for chunk_id in to_remove:
            self._vectors.pop(chunk_id, None)
            self._docs.pop(chunk_id, None)
            self._metas.pop(chunk_id, None)

    def _similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    def query(self, vector, top_k=10, where=None) -> list[dict[str, Any]]:
        scored = []
        for chunk_id, stored in self._vectors.items():
            metadata = self._metas[chunk_id]
            if where and not all(metadata.get(k) == v for k, v in where.items()):
                continue
            scored.append((self._similarity(vector, stored), chunk_id))
        scored.sort(reverse=True)
        return [
            {
                "id": chunk_id,
                "document": self._docs[chunk_id],
                "metadata": self._metas[chunk_id],
                "similarity": score,
            }
            for score, chunk_id in scored[:top_k]
        ]

    def count(self) -> int:
        return len(self._vectors)
