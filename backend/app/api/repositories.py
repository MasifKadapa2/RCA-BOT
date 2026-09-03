"""Repository API endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.models import IndexedFile, Repository
from app.schemas.incidents import IndexRequest, RepositoryOut

router = APIRouter(prefix="/api", tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository)).all()
    out = []
    for repo in repos:
        files_count = db.scalar(
            select(func.count(IndexedFile.id)).where(IndexedFile.repository_id == repo.id)
        )
        out.append(
            RepositoryOut(
                id=repo.id,
                name=repo.name,
                path=repo.path,
                indexed_files=files_count or 0,
                created_at=repo.created_at,
                updated_at=repo.updated_at,
            )
        )
    return out


@router.post("/repositories/index")
def index_repository(payload: IndexRequest | None = None, db: Session = Depends(get_db)) -> dict:
    """Trigger (incremental) indexing of the configured repository.

    Only server-side configured local directories are accepted; this endpoint
    never exposes arbitrary filesystem access to clients.
    """
    from app.ingestion.embeddings import get_embedding_provider
    from app.ingestion.indexer import RepositoryIndexer
    from app.ingestion.vector_store import ChromaVectorStore

    settings = get_settings()
    repo_path = (payload.repository_path if payload else "") or settings.repository_path
    if not repo_path:
        raise HTTPException(status_code=400, detail="REPOSITORY_PATH is not configured")
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=400, detail=f"Repository path not found: {repo_path}")

    try:
        indexer = RepositoryIndexer(
            repository_path=repo_path,
            chroma_path=settings.chroma_path,
            embedding_provider=get_embedding_provider(settings.embedding_model),
            vector_store_factory=lambda path=settings.chroma_path: ChromaVectorStore(
                persist_directory=path
            ),
            max_file_size_bytes=settings.max_file_size_bytes,
        )
        stats = indexer.run(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

    return {"status": "ok", "stats": stats}


@router.post("/repositories/index_git")
def index_git_history(payload: IndexRequest | None = None, db: Session = Depends(get_db)) -> dict:
    """Incremental Git history indexing for Change Intelligence."""
    from app.core.errors import RepositoryNotFoundError
    from app.git import GitIndexService, NotAGitRepositoryError

    settings = get_settings()
    repo_path = (payload.repository_path if payload else "") or settings.repository_path
    if not repo_path:
        raise HTTPException(status_code=400, detail="REPOSITORY_PATH is not configured")
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=400, detail=f"Repository path not found: {repo_path}")

    try:
        service = GitIndexService(settings)
        stats, _repo = service.index(db, repository_path=repo_path)
    except NotAGitRepositoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "stats": stats.__dict__}
