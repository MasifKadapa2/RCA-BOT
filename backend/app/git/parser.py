"""Parsers for Git log and unified diff output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER = re.compile(r'^diff --git a/(.+?) b/(.+)$')


@dataclass
class HunkData:
    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    patch: str
    truncated: bool = False


@dataclass
class DiffResult:
    hunks: list[HunkData] = field(default_factory=list)
    truncated: bool = False  # whole-diff truncation hit the char budget


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_unified_diff(diff_text: str, max_patch_chars: int) -> DiffResult:
    """Parse a unified diff into per-file bounded hunks."""
    result = DiffResult()
    current_file: str | None = None
    current_hunk: HunkData | None = None
    patch_lines: list[str] = []

    def flush() -> None:
        nonlocal current_hunk, patch_lines
        if current_hunk is None:
            patch_lines = []
            return
        patch = "\n".join(patch_lines)
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars]
            current_hunk.truncated = True
            result.truncated = True
        current_hunk.patch = patch
        result.hunks.append(current_hunk)
        current_hunk = None
        patch_lines = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush()
            match = _FILE_HEADER.match(line)
            current_file = (match.group(2) if match else None)
            continue
        header = _HUNK_HEADER.match(line)
        if header:
            flush()
            if current_file is None:
                continue
            old_start = int(header.group(1))
            old_lines = int(header.group(2) or "1")
            new_start = int(header.group(3))
            new_lines = int(header.group(4) or "1")
            current_hunk = HunkData(
                file_path=current_file,
                old_start=old_start, old_lines=old_lines,
                new_start=new_start, new_lines=new_lines,
                patch="",
            )
            patch_lines = [line]
            continue
        if current_hunk is not None:
            if line.startswith(("+", "-", " ", "\\")):
                patch_lines.append(line)
    flush()
    return result
