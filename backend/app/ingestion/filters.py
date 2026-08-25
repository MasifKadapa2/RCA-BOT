"""Repository walking and filtering rules.

The indexer must never leave the configured repository directory and never
touches secrets (.env, credentials) or binary/huge files.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".rb": "ruby",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".java": "java",
}

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "vendor",
        "tmp",
        "temp",
        "log",
        "logs",
        "coverage",
        "dist",
        "build",
        ".cache",
        ".next",
        ".nuxt",
        ".turbo",
        "venv",
        ".venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "target",
        "out",
        "bower_components",
        "chroma",
        "data",
    }
)

DEFAULT_IGNORE_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*credential*",
    "*secret*",
    "*.lock",
    "*.min.js",
)

BINARY_SNIFF_BYTES = 8192


class FileFilter:
    def __init__(
        self,
        extra_ignore_dirs: set[str] | None = None,
        extra_ignore_patterns: tuple[str, ...] = (),
        max_file_size_bytes: int = 512_000,
    ) -> None:
        self.ignore_dirs = set(DEFAULT_IGNORE_DIRS) | (extra_ignore_dirs or set())
        self.ignore_patterns = DEFAULT_IGNORE_FILE_PATTERNS + extra_ignore_patterns
        self.max_file_size_bytes = max_file_size_bytes

    # --- Directories ----------------------------------------------------------

    def is_ignored_directory(self, dir_name: str) -> bool:
        return dir_name in self.ignore_dirs or dir_name.startswith(".")

    # --- Files -----------------------------------------------------------------

    def is_supported(self, file_name: str) -> bool:
        return Path(file_name).suffix.lower() in SUPPORTED_EXTENSIONS

    def is_ignored_file(self, file_name: str) -> bool:
        lowered = file_name.lower()
        return any(
            fnmatch.fnmatch(lowered, pattern.lower()) for pattern in self.ignore_patterns
        )

    def should_skip_stat(self, size: int) -> bool:
        return size > self.max_file_size_bytes

    def is_binary(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                chunk = handle.read(BINARY_SNIFF_BYTES)
        except OSError:
            return True
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        nontext = sum(byte not in text_chars for byte in chunk[:1024])
        return nontext / min(len(chunk), 1024) > 0.30


def iter_repository_files(root: Path, file_filter: FileFilter) -> list[Path]:
    """Yield supported, non-ignored files under ``root`` (never outside it)."""
    results: list[Path] = []
    root = root.resolve()
    base_len = len(root.parts)
    for current, dirnames, filenames in os.walk(root):
        here = Path(current)
        # Prune ignored directories in-place so os.walk skips descending.
        dirnames[:] = sorted(d for d in dirnames if not file_filter.is_ignored_directory(d))
        for filename in sorted(filenames):
            if not file_filter.is_supported(filename):
                continue
            if file_filter.is_ignored_file(filename):
                continue
            path = here / filename
            parts = path.resolve().parts
            if parts[:base_len] != root.parts:
                continue  # defensive: symlink escape guard
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if file_filter.should_skip_stat(size):
                continue
            if file_filter.is_binary(path):
                continue
            results.append(path)
    return results


def safe_relative_path(root: Path, relative: str) -> Path | None:
    """Resolve ``relative`` against ``root``; returns the absolute path or
    ``None`` when it would escape the repository root."""
    root = root.resolve()
    try:
        candidate = (root / relative).resolve()
    except OSError:
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def file_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_source(path: Path) -> str | None:
    """Read a text file, returning None for unreadable or binary files."""
    try:
        with path.open("rb") as handle:
            head = handle.read(BINARY_SNIFF_BYTES)
        if b"\x00" in head:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
