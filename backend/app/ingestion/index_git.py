"""CLI: incremental Git history indexing.

Usage:
    python -m app.ingestion.index_git [--path /repo]
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index Git history for Change Intelligence.")
    parser.add_argument("--path", default=None, help="Repository path (defaults to REPOSITORY_PATH)")
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.core.logging import setup_logging
    from app.db.base import init_db, get_session_factory
    from app.git import GitIndexService, NotAGitRepositoryError
    from app.core.errors import RepositoryNotFoundError

    settings = get_settings()
    setup_logging(settings.log_level)
    init_db()

    repo_path = args.path or settings.repository_path
    if not repo_path:
        print("Error: REPOSITORY_PATH not configured and --path not provided.", file=sys.stderr)
        return 2

    try:
        service = GitIndexService(settings)
        with get_session_factory()() as db:
            stats, _repo = service.index(db, repository_path=repo_path)
    except NotAGitRepositoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3
    except RepositoryNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("Git indexing complete")
    print(json.dumps(stats.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
