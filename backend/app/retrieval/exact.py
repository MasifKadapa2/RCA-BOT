"""Exact / lexical retrieval over indexed chunk metadata (SQLite)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CodeChunk, IndexedFile
from app.schemas.retrieval import RetrievalMethod, RetrievalQuery, RetrievalResult

_FILENAME_WEIGHT = 4.0
_SYMBOL_WEIGHT = 3.0
_IDENTIFIER_WEIGHT = 0.5
_IDENTIFIER_CAP = 2.5


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


class LexicalRetriever:
    """Finds chunks by filename, symbol name and distinctive identifiers.

    All scoring is deterministic; exact filename matches receive the strongest
    boost, then exact symbol matches, then identifier occurrences.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def retrieve(self, query: RetrievalQuery, limit: int = 20) -> list[RetrievalResult]:
        scores: dict[int, float] = {}
        rows: dict[int, tuple[CodeChunk, str]] = {}

        def register(chunk: CodeChunk, relative_path: str, points: float) -> None:
            scores[chunk.id] = scores.get(chunk.id, 0.0) + points
            rows[chunk.id] = (chunk, relative_path)

        wanted_filenames = {name.lower() for name in query.exact_filenames}
        if wanted_filenames:
            from sqlalchemy import or_

            stmt = (
                select(CodeChunk, IndexedFile)
                .join(IndexedFile, IndexedFile.id == CodeChunk.indexed_file_id)
                .where(
                    or_(
                        *[
                            IndexedFile.relative_path.ilike(f"%{name}")
                            for name in list(wanted_filenames)[:12]
                        ]
                    )
                )
                .limit(200)
            )
            for chunk, indexed_file in self._db.execute(stmt).all():
                base = _basename(indexed_file.relative_path)
                if base in wanted_filenames:
                    register(chunk, indexed_file.relative_path, _FILENAME_WEIGHT)
                else:
                    register(chunk, indexed_file.relative_path, _FILENAME_WEIGHT * 0.6)

        wanted_symbols = {s.lower() for s in query.exact_symbols}
        if wanted_symbols:
            conditions = [
                CodeChunk.symbol_name.ilike(f"%{symbol}%")
                for symbol in list(wanted_symbols)[:12]
            ]
            stmt = (
                select(CodeChunk, IndexedFile)
                .join(IndexedFile, IndexedFile.id == CodeChunk.indexed_file_id)
                .where(or_(*conditions))
                .limit(200)
            )
            for chunk, indexed_file in self._db.execute(stmt).all():
                name_lower = (chunk.symbol_name or "").lower()
                if name_lower in wanted_symbols:
                    register(chunk, indexed_file.relative_path, _SYMBOL_WEIGHT)
                elif any(s and s in name_lower for s in wanted_symbols):
                    register(chunk, indexed_file.relative_path, _SYMBOL_WEIGHT * 0.7)

        # Identifier scan is a capped LIKE search over chunk content.
        strong_identifiers = [
            i for i in query.identifiers if len(i) >= 5 and not i.startswith("error")
        ][:8]
        for identifier in strong_identifiers:
            pattern = f"%{identifier}%"
            stmt = (
                select(CodeChunk, IndexedFile)
                .join(IndexedFile, IndexedFile.id == CodeChunk.indexed_file_id)
                .where(CodeChunk.content.ilike(pattern))
                .limit(50)
            )
            for chunk, indexed_file in self._db.execute(stmt).all():
                occurrences = len(re.findall(identifier, chunk.content, flags=re.IGNORECASE))
                register(
                    chunk,
                    indexed_file.relative_path,
                    min(_IDENTIFIER_WEIGHT * occurrences, _IDENTIFIER_CAP),
                )

        results: list[RetrievalResult] = []
        for chunk_id, score in scores.items():
            chunk, relative_path = rows[chunk_id]
            results.append(
                RetrievalResult(
                    relative_path=relative_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbol_name=chunk.symbol_name,
                    symbol_type=chunk.symbol_type,
                    content=chunk.content,
                    score=round(score, 3),
                    retrieval_method=RetrievalMethod.EXACT,
                )
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]


# Backwards compatibility: original name was ExactRetriever
ExactRetriever = LexicalRetriever
