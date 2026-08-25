"""Merged retrieval with deterministic ranking.

Combines exact and semantic results, removes duplicates, boosts exact
filename/symbol/stack-frame matches and enforces the context budget.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.vector_store import VectorStore
from app.schemas.retrieval import RetrievalMethod, RetrievalQuery, RetrievalResult
from app.retrieval.exact import LexicalRetriever
from app.retrieval.vector import VectorRetriever

# Alias for backwards compatibility
ExactRetriever = LexicalRetriever

logger = logging.getLogger(__name__)

WEIGHT_FILENAME_EXACT = 4.0
WEIGHT_SYMBOL_EXACT = 3.0
WEIGHT_STACK_FRAME_PATH = 3.5
WEIGHT_SEMANTIC = 2.0


class CodeRetriever:
    def __init__(
        self,
        db_session_factory,
        embedding_provider: EmbeddingProvider,
        vector_store_factory,
        max_results: int = 10,
        max_context_chars: int = 24_000,
        repository_id: int | None = None,
    ) -> None:
        self._session_factory = db_session_factory
        self._embedder = embedding_provider
        self._vector_store_factory = vector_store_factory
        self._max_results = max_results
        self._max_context_chars = max_context_chars
        self._repository_id = repository_id

    def retrieve(
        self,
        query: RetrievalQuery,
        stack_frame_paths: list[str] | None = None,
    ) -> list[RetrievalResult]:
        results, _trace = self.retrieve_with_trace(query, stack_frame_paths)
        return results

    def retrieve_with_trace(
        self,
        query: RetrievalQuery,
        stack_frame_paths: list[str] | None = None,
    ) -> tuple[list[RetrievalResult], dict]:
        """Retrieve and also return a trace for dashboard observability."""
        with self._session_factory() as db:
            lexical = LexicalRetriever(db).retrieve(query, limit=max(self._max_results * 2, 20))
            vector: list[RetrievalResult] = []
            try:
                store = self._vector_store_factory()
                retriever = VectorRetriever(
                    db,
                    embedding_provider=self._embedder,
                    vector_store=store,
                    repository_id=self._repository_id,
                )
                vector = retriever.retrieve(query, limit=self._max_results)
            except Exception as exc:
                logger.warning("vector_retrieval_failed", extra={"error": str(exc)})

        merged = self.merge(lexical, vector, stack_frame_paths or [])
        final = merged[: self._max_results]

        # Build trace dict for persistence
        def _to_trace(results: list[RetrievalResult], is_lexical: bool = False):
            out = []
            for rank, r in enumerate(results, start=1):
                reason = "lexical"
                if is_lexical:
                    # Derive match reason from query
                    base = Path(r.relative_path).name.lower()
                    if any(base == f.lower() for f in query.exact_filenames):
                        reason = "filename match"
                    elif r.symbol_name and any(r.symbol_name.lower() == s.lower() for s in query.exact_symbols):
                        reason = "symbol match"
                    elif any(f.lower() in r.relative_path.lower() for f in (stack_frame_paths or [])):
                        reason = "stack-frame match"
                    else:
                        reason = "content match"
                else:
                    reason = "semantic similarity"
                out.append({
                    "rank": rank,
                    "path": r.relative_path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "symbol": r.symbol_name,
                    "symbol_type": r.symbol_type,
                    "score": r.score,
                    "similarity": r.score if not is_lexical else None,
                    "match_reason": reason,
                    "retrieval_method": r.retrieval_method,
                })
            return out

        trace = {
            "query": {
                "semantic_query": query.semantic_query,
                "exact_filenames": query.exact_filenames,
                "exact_symbols": query.exact_symbols,
                "identifiers": query.identifiers,
            },
            "lexical_results": _to_trace(lexical, is_lexical=True),
            "vector_results": _to_trace(vector, is_lexical=False),
            "final_context": [
                {
                    "rank": i+1,
                    "path": r.relative_path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "symbol": r.symbol_name,
                    "content": r.content[:2000],  # cap for storage
                }
                for i, r in enumerate(final)
            ],
        }
        return final, trace

    # --- Ranking -----------------------------------------------------------------

    def merge(
        self,
        exact: list[RetrievalResult],
        vector: list[RetrievalResult],
        stack_frame_paths: list[str],
    ) -> list[RetrievalResult]:
        frame_basenames = {
            p.replace("\\", "/").rsplit("/", 1)[-1].lower() for p in stack_frame_paths if p
        }
        combined: dict[tuple[str, int], RetrievalResult] = {}

        for result in exact:
            key = (result.relative_path, result.start_line)
            score = result.score
            base = Path(result.relative_path).name.lower()
            if base in frame_basenames:
                score += WEIGHT_STACK_FRAME_PATH
            entry = dict(result)
            entry["score"] = round(score, 3)
            combined[key] = RetrievalResult(**entry)

        for result in vector:
            key = (result.relative_path, result.start_line)
            if key in combined:
                existing = combined[key]
                existing.score = round(existing.score + WEIGHT_SEMANTIC * float(result.score), 3)
                # Attribute merged hits to both methods via EXACT (stronger provenance).
                existing.retrieval_method = RetrievalMethod.EXACT
            else:
                boosted = dict(result)
                base = Path(result.relative_path).name.lower()
                extra = WEIGHT_SEMANTIC * float(result.score)
                if base in frame_basenames:
                    extra += WEIGHT_STACK_FRAME_PATH * 0.8
                boosted["score"] = round(float(result.score) + extra, 3)
                combined[key] = RetrievalResult(**boosted)

        ranked = sorted(combined.values(), key=lambda r: r.score, reverse=True)

        # Enforce the LLM context budget.
        budget = self._max_context_chars
        kept: list[RetrievalResult] = []
        used = 0
        for item in ranked:
            content_len = len(item.content)
            if used + content_len > budget:
                remaining = budget - used
                if remaining > 400:
                    truncated = item.model_copy(update={"content": item.content[:remaining]})
                    kept.append(truncated)
                    used += remaining
                break
            kept.append(item)
            used += content_len
        logger.info(
            "rca_bot.retrieval_completed",
            extra={
                "exact": len(exact),
                "vector": len(vector),
                "returned": len(kept),
                "context_chars": used,
            },
        )
        return kept
