"""CLI entry point for repository indexing.

Usage:
    python -m app.ingestion.index_repository [--path /path/to/repo]
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index a repository for RCA Bot retrieval.")
    parser.add_argument("--path", default=None, help="Repository path (defaults to REPOSITORY_PATH)")
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.core.logging import setup_logging
    from app.db.base import init_db, reset_engine
    from app.ingestion.embeddings import get_embedding_provider
    from app.ingestion.indexer import RepositoryIndexer
    from app.ingestion.vector_store import ChromaVectorStore

    settings = get_settings()
    setup_logging(settings.log_level)

    repo_path = args.path or settings.repository_path
    if not repo_path:
        print("Error: REPOSITORY_PATH is not configured and --path was not provided.", file=sys.stderr)
        return 2
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_path):
        print(f"Error: repository path does not exist: {repo_path}", file=sys.stderr)
        return 2

    reset_engine()
    init_db()

    from app.db.base import get_session_factory

    indexer = RepositoryIndexer(
        repository_path=repo_path,
        chroma_path=settings.chroma_path,
        embedding_provider=get_embedding_provider(settings.embedding_model),
        vector_store_factory=lambda: ChromaVectorStore(persist_directory=settings.chroma_path),
        max_file_size_bytes=settings.max_file_size_bytes,
    )
    with get_session_factory()() as db:
        stats = indexer.run(db)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
