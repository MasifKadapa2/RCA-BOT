"""Vector (semantic) retrieval backed by Chroma + SQLite chunk lookup."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RetrievalError
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.vector_store import VectorStore
from app.models import CodeChunk, IndexedFile
from app.schemas.retrieval import RetrievalMethod, RetrievalQuery, RetrievalResult

logger = logging.getLogger(__name__)


class VectorRetriever:
    def __init__(self, db: Session, embedding_provider: EmbeddingProvider,
                 vector_store: VectorStore, repository_id: int | None = None) -> None:
        self._db = db
        self._embedder = embedding_provider
        self._vector_store = vector_store
        self._repository_id = repository_id

    def retrieve(self, query: RetrievalQuery, limit: int = 10) -> list[RetrievalResult]:
        text = query.semantic_query.strip()
        if not text:
            return []
        try:
            vector = self._embedder.embed_texts([text])[0]
        except Exception as exc:
            raise RetrievalError(f"Embedding failed: {type(exc).__name__}") from exc

        where: dict[str, Any] | None = None
        if self._repository_id is not None:
            where = {"repository_id": self._repository_id}
        try:
            hits = self._vector_store.query(vector=vector, top_k=limit, where=where)
        except Exception as exc:
            raise RetrievalError(f"Vector search failed: {type(exc).__name__}") from exc

        results: list[RetrievalResult] = []
        for hit in hits:
            metadata = hit.get("metadata") or {}
            result = self._hydrate(hit, metadata)
            if result is not None:
                results.append(result)
        return results

    def _hydrate(self, hit: dict, metadata: dict[str, Any]) -> RetrievalResult | None:
        relative_path = metadata.get("relative_path")
        start_line = int(metadata.get("start_line") or 0)
        end_line = int(metadata.get("end_line") or 0)
        document = hit.get("document") or ""
        if not relative_path or start_line <= 0:
            return None
        # Prefer DB content when available so Slack/dashboard always match SQLite.
        stmt = (
            select(CodeChunk)
            .join(IndexedFile, IndexedFile.id == CodeChunk.indexed_file_id)
            .where(
                IndexedFile.relative_path == relative_path,
                CodeChunk.start_line == start_line,
                CodeChunk.end_line == end_line,
            )
            .limit(1)
        )
        chunk = self._db.scalars(stmt).first()
        if chunk is not None:
            indexed_file = chunk.indexed_file
            return RetrievalResult(
                relative_path=(indexed_file.relative_path if indexed_file else str(relative_path)),
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                symbol_name=chunk.symbol_name,
                symbol_type=chunk.symbol_type,
                content=chunk.content,
                score=float(hit.get("similarity", 0.0)),
                retrieval_method=RetrievalMethod.VECTOR,
            )
        # Fall back to stored document text.
        return RetrievalResult(
            relative_path=str(relative_path),
            start_line=start_line,
            end_line=end_line,
            symbol_name=metadata.get("symbol_name") or None,
            symbol_type=metadata.get("symbol_type") or None,
            content=document,
            score=float(hit.get("similarity", 0.0)),
            retrieval_method=RetrievalMethod.VECTOR,
        )
