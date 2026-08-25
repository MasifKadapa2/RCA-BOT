"""Incremental repository indexer.

Pipeline: walk -> filter -> hash -> (skip unchanged) -> chunk -> embed ->
persist to Chroma + SQLite. Re-runs are safe and only re-embed changed files.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryNotFoundError
from app.ingestion.chunker import TreeSitterChunker, chunk_source
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.filters import (
    FileFilter,
    file_content_hash,
    iter_repository_files,
    read_source,
)
from app.ingestion.vector_store import VectorStore
from app.models import CodeChunk, IndexedFile, Repository

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32


class RepositoryIndexer:
    def __init__(
        self,
        repository_path: str,
        chroma_path: str,
        embedding_provider: EmbeddingProvider,
        vector_store_factory=None,
        max_file_size_bytes: int = 512_000,
        extra_ignore_dirs: set[str] | None = None,
    ) -> None:
        self._root = Path(repository_path).expanduser().resolve()
        self._chroma_path = chroma_path
        self._embedding_provider = embedding_provider
        self._vector_store_factory = vector_store_factory or self._default_vector_store
        self._file_filter = FileFilter(
            extra_ignore_dirs=extra_ignore_dirs,
            max_file_size_bytes=max_file_size_bytes,
        )
        self._semantic_chunker = TreeSitterChunker()

    def _default_vector_store(self) -> VectorStore:
        from app.ingestion.vector_store import ChromaVectorStore

        return ChromaVectorStore(persist_directory=self._chroma_path)

    # --- Public ----------------------------------------------------------------

    def run(self, db: Session) -> dict:
        started = time.monotonic()
        if not self._root.is_dir():
            raise RepositoryNotFoundError(f"Repository path does not exist: {self._root}")

        repository = self._upsert_repository(db)
        vector_store = self._vector_store_factory()

        files = iter_repository_files(self._root, self._file_filter)
        existing_files = {
            f.relative_path: f for f in db.scalars(
                select(IndexedFile).where(IndexedFile.repository_id == repository.id)
            )
        }

        stats = {
            "repository_id": repository.id,
            "files_scanned": len(files),
            "files_indexed": 0,
            "files_skipped": 0,
            "chunks_created": 0,
            "chunks_deleted": 0,
            "files_removed": 0,
        }
        seen_relative: set[str] = set()

        for path in files:
            relative = path.relative_to(self._root).as_posix()
            seen_relative.add(relative)

            content = read_source(path)
            if content is None:
                stats["files_skipped"] += 1
                continue
            content_hash = file_content_hash(content)

            existing = existing_files.get(relative)
            if existing is not None and existing.content_hash == content_hash:
                stats["files_skipped"] += 1  # unchanged: no re-embedding
                continue

            from app.ingestion.filters import SUPPORTED_EXTENSIONS

            language = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
            chunks = chunk_source(content, relative, language or "text", self._semantic_chunker)

            if existing is not None:
                removed_vectors = self._delete_file_chunks(db, vector_store, existing)
                stats["chunks_deleted"] += removed_vectors

            indexed_file = self._upsert_indexed_file(db, repository.id, relative, content_hash, language)
            vector_ids = self._store_chunks(db, vector_store, indexed_file, chunks)
            stats["files_indexed"] += 1
            stats["chunks_created"] += len(vector_ids)

        # Remove index entries for deleted files.
        for relative in set(existing_files) - seen_relative:
            stale = existing_files[relative]
            stats["chunks_deleted"] += self._delete_file_chunks(db, vector_store, stale)
            db.delete(stale)
            stats["files_removed"] += 1

        db.commit()
        stats["duration_seconds"] = round(time.monotonic() - started, 2)
        logger.info("rca_bot.index_completed", extra=stats)
        return stats

    # --- Internals ---------------------------------------------------------------

    def _upsert_repository(self, db: Session) -> Repository:
        repo = db.scalars(select(Repository).where(Repository.path == str(self._root))).first()
        if repo is None:
            repo = Repository(name=self._root.name, path=str(self._root))
            db.add(repo)
        else:
            repo.updated_at = datetime.now(UTC)
        db.flush()
        return repo

    def _upsert_indexed_file(
        self, db: Session, repository_id: int, relative: str, content_hash: str, language: str | None
    ) -> IndexedFile:
        row = db.scalars(
            select(IndexedFile).where(
                IndexedFile.repository_id == repository_id,
                IndexedFile.relative_path == relative,
            )
        ).first()
        if row is None:
            row = IndexedFile(repository_id=repository_id, relative_path=relative)
            db.add(row)
        row.content_hash = content_hash
        row.language = language
        row.indexed_at = datetime.now(UTC)
        db.flush()
        return row

    def _delete_file_chunks(self, db: Session, vector_store: VectorStore, indexed_file: IndexedFile) -> int:
        chunks = db.scalars(select(CodeChunk).where(CodeChunk.indexed_file_id == indexed_file.id)).all()
        vector_ids = [c.vector_id for c in chunks if c.vector_id]
        if vector_ids:
            try:
                vector_store.delete(ids=vector_ids)
            except Exception as exc:  # keep indexing resilient to vector store hiccups
                logger.warning("vector_delete_failed", extra={"error": str(exc)})
        for chunk in chunks:
            db.delete(chunk)
        db.flush()
        return len(vector_ids)

    def _store_chunks(
        self,
        db: Session,
        vector_store: VectorStore,
        indexed_file: IndexedFile,
        chunks,
    ) -> list[str]:
        if not chunks:
            return []
        contents = [chunk.content for chunk in chunks]
        vectors: list[list[float]] = []
        for start in range(0, len(contents), EMBED_BATCH_SIZE):
            batch = contents[start : start + EMBED_BATCH_SIZE]
            vectors.extend(self._embedding_provider.embed_texts(batch))

        vector_ids: list[str] = []
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for chunk, vector in zip(chunks, vectors):
            digest = file_content_hash(f"{indexed_file.id}:{chunk.relative_path}:{chunk.start_line}:{chunk.content}")
            vector_id = f"chk_{digest[:24]}"
            vector_ids.append(vector_id)
            ids.append(vector_id)
            documents.append(chunk.content)
            metadatas.append(
                {
                    "repository_id": indexed_file.repository_id,
                    "relative_path": chunk.relative_path,
                    "language": chunk.language,
                    "symbol_name": chunk.symbol_name or "",
                    "symbol_type": chunk.symbol_type or "",
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "content_hash": chunk.content_hash,
                }
            )
            db.add(
                CodeChunk(
                    indexed_file_id=indexed_file.id,
                    symbol_name=chunk.symbol_name,
                    symbol_type=chunk.symbol_type,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    vector_id=vector_id,
                )
            )

        try:
            vector_store.add(ids=ids, vectors=vectors, documents=documents, metadatas=metadatas)
        except Exception as exc:
            db.rollback()
            raise RuntimeError(f"Failed to persist vectors: {exc}") from exc
        db.flush()
        return vector_ids
