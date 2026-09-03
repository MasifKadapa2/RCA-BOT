"""Safe Git access for Change Intelligence (V2).

Security invariants:
- fixed ``git`` binary, structured list arguments, shell=False
- validated repository root and repository-relative paths
- timeouts + bounded output on every invocation
- no LLM/user-provided command strings ever reach this layer
"""

from app.git.client import (
    GitClient,
    GitCommandError,
    NotAGitRepositoryError,
)
from app.git.service import GitIndexService, GitIndexStats

__all__ = [
    "GitClient",
    "GitCommandError",
    "NotAGitRepositoryError",
    "GitIndexService",
    "GitIndexStats",
]
