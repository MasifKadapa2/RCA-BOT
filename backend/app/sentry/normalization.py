"""Normalizes raw Sentry payloads into internal models.

Two layers of helpers:
- extraction utilities used directly by SentryClient (extract_* functions)
- normalization into pydantic domain models (normalize_issue/normalize_event)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterator, Optional

from app.schemas.sentry import (
    ExceptionInfo,
    SentryEventData,
    SentryIssueData,
    StackFrame,
)


# ---------------------------------------------------------------------------
# Extraction helpers (used by SentryClient.extract_*)
# ---------------------------------------------------------------------------


def extract_raw_frames(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the raw frame list from the most relevant stacktrace in an event.

    Frames are returned crash-site-first (Sentry lists them oldest-first).
    """
    entries = event.get("entries") or []

    for entry in entries:
        if entry.get("type") == "exception":
            values = (entry.get("data") or {}).get("values") or []
            for value in values:  # first value = innermost exception
                frames = _frames_from_stacktrace(value.get("stacktrace"))
                if frames:
                    return _crash_first(frames)

    for entry in entries:
        if entry.get("type") == "stacktrace":
            frames = _frames_from_stacktrace(entry.get("data"))
            if frames:
                return _crash_first(frames)

    for entry in entries:
        if entry.get("type") == "threads":
            values = (entry.get("data") or {}).get("values") or []
            for value in values:
                if isinstance(value, dict) and value.get("crashed"):
                    frames = _frames_from_stacktrace(value.get("stacktrace"))
                    if frames:
                        return _crash_first(frames)

    return _crash_first(_frames_from_stacktrace(event.get("stacktrace")))


def _frames_from_stacktrace(stacktrace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not stacktrace:
        return []
    return [f for f in (stacktrace.get("frames") or []) if isinstance(f, dict)]


def _crash_first(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(reversed(frames))


def extract_primary_exception(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The most relevant exception value dict, or None."""
    values: list[dict[str, Any]] = []
    entries = event.get("entries") or []
    for entry in entries:
        if entry.get("type") == "exception":
            values = (entry.get("data") or {}).get("values") or []
            break
    if not values:
        values = (event.get("exception") or {}).get("values") or []
    return values[-1] if values else None


def extract_event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    tags: dict[str, str] = {}
    for tag in event.get("tags") or []:
        if isinstance(tag, dict) and tag.get("key"):
            tags[str(tag["key"])] = str(tag.get("value", ""))

    release = event.get("release")
    if isinstance(release, dict):
        release = release.get("version")

    return {
        "event_id": event.get("event_id") or event.get("eventID") or event.get("id"),
        "timestamp": event.get("dateCreated") or event.get("timestamp"),
        "platform": event.get("platform"),
        "environment": event.get("environment") or tags.get("environment"),
        "release": release or tags.get("release"),
        "transaction": event.get("transaction"),
        "level": event.get("level"),
        "tags": tags,
    }


# ---------------------------------------------------------------------------
# Normalization into pydantic models
# ---------------------------------------------------------------------------


def normalize_frame(raw: dict[str, Any]) -> StackFrame:
    context: list[list[Any]] = []
    # The Web API returns camelCase (lineNo/colNo); some payloads use snake_case.
    line_no = raw.get("lineno")
    if line_no is None:
        line_no = raw.get("lineNo")
    pre = raw.get("pre_context") or raw.get("preContext") or []
    post = raw.get("post_context") or raw.get("postContext") or []
    if line_no is not None:
        try:
            start = int(line_no) - len(pre)
            for i, text in enumerate(pre):
                context.append([start + i, text])
            context_line = raw.get("context_line", raw.get("contextLine"))
            if context_line:
                context.append([int(line_no), context_line])
            for i, text in enumerate(post):
                context.append([int(line_no) + 1 + i, text])
        except (TypeError, ValueError):
            context = []

    in_app = raw.get("in_app", raw.get("inApp", False))
    column = raw.get("colno", raw.get("colNo"))
    try:
        line_number = int(line_no) if line_no is not None else None
    except (TypeError, ValueError):
        line_number = None
    try:
        column_number = int(column) if column is not None else None
    except (TypeError, ValueError):
        column_number = None

    return StackFrame(
        filename=raw.get("filename"),
        abs_path=raw.get("abs_path", raw.get("absPath")),
        function=raw.get("function"),
        module=raw.get("module"),
        line_number=line_number,
        column_number=column_number,
        context=context or None,
        context_line=raw.get("context_line", raw.get("contextLine")),
        pre_context=[str(t) for t in pre],
        post_context=[str(t) for t in post],
        in_app=bool(in_app),
        raw_frame=raw,
    )


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _string_or(value):
    return str(value) if value and not isinstance(value, (dict, list)) else None


def _release_string(value):
    """Release may arrive as a string or {"version": "..."}."""
    if isinstance(value, dict):
        version = value.get("version")
        return str(version) if version else None
    if isinstance(value, str):
        return value
    return None


def normalize_issue(issue_id: str, issue) -> SentryIssueData:
    if isinstance(issue, str):  # tolerate being handed just the id
        issue = {"id": issue_id}
    issue = dict(issue or {})

    def env_from_tags() -> Optional[str]:
        for tag in issue.get("tags") or []:
            if isinstance(tag, dict) and tag.get("key") == "environment":
                value = tag.get("value")
                if isinstance(value, list) and value:
                    return str(value[0])
                if isinstance(value, str):
                    return value
        return None

    project = issue.get("project")
    metadata = issue.get("metadata") or {}
    count = issue.get("count")
    derived_title = f"{metadata.get('type', '')}: {metadata.get('value', '')}".strip(": ").strip()
    title = issue.get("title") or derived_title or f"Sentry issue {issue_id}"

    return SentryIssueData(
        issue_id=str(issue_id),
        title=title,
        short_id=issue.get("shortId"),
        culprit=issue.get("culprit"),
        project=project.get("slug") if isinstance(project, dict) else None,
        environment=_string_or(issue.get("environment")) or env_from_tags(),
        release=_release_string(issue.get("release") or metadata.get("release")),
        platform=issue.get("platform"),
        level=issue.get("level"),
        status=issue.get("status"),
        first_seen=parse_datetime(issue.get("firstSeen")),
        last_seen=parse_datetime(issue.get("lastSeen")),
        times_seen=int(count) if count and str(count).isdigit() else None,
        permalink=issue.get("permalink"),
        metadata={
            "type": metadata.get("type"),
            "value": metadata.get("value"),
        },
        raw={},
    )


def normalize_event(
    event: dict, fallback_environment: Optional[str] = None
) -> SentryEventData:
    exceptions: list[ExceptionInfo] = []
    primary_frames: list[StackFrame] = []

    entries = event.get("entries") or []
    exc_values: list[dict] = []
    for entry in entries:
        if entry.get("type") == "exception":
            exc_values = (entry.get("data") or {}).get("values") or []
            break
    if not exc_values:
        exc_values = (event.get("exception") or {}).get("values") or []

    for value in exc_values:
        frames = [normalize_frame(f) for f in ((value.get("stacktrace") or {}).get("frames") or [])]
        exceptions.append(
            ExceptionInfo(
                type=value.get("type"),
                value=value.get("value"),
                module=value.get("module"),
                mechanism_type=(value.get("mechanism") or {}).get("type"),
                stacktrace_frames=frames,
            )
        )
        if frames and not primary_frames:
            # First exception with a stack = the crashing one; crash-site first.
            primary_frames = list(reversed(frames))

    meta = extract_event_metadata(event)

    tags: dict[str, str] = {}
    for tag in event.get("tags") or []:
        if isinstance(tag, dict) and tag.get("key"):
            tags[str(tag["key"])] = str(tag.get("value", ""))

    return SentryEventData(
        event_id=str(meta.get("event_id") or "") or None,
        title=event.get("title") or (event.get("metadata") or {}).get("title"),
        timestamp=parse_datetime(meta.get("timestamp")),
        platform=meta.get("platform"),
        environment=meta.get("environment") or tags.get("environment") or fallback_environment,
        release=meta.get("release") or tags.get("release"),
        transaction=meta.get("transaction"),
        level=meta.get("level"),
        message=event.get("message"),
        tags=tags or meta["tags"],
        exceptions=exceptions,
        frames=primary_frames,
        raw={},
    )


def extract_exceptions(event: dict[str, Any]) -> list[ExceptionInfo]:
    """All exception values from an event, innermost last."""
    values: list[dict[str, Any]] = []
    entries = event.get("entries") or []
    for entry in entries:
        if entry.get("type") == "exception":
            values = (entry.get("data") or {}).get("values") or []
            break
    if not values:
        values = (event.get("exception") or {}).get("values") or []

    exceptions: list[ExceptionInfo] = []
    for value in values:
        frames = [normalize_frame(f) for f in ((value.get("stacktrace") or {}).get("frames") or [])]
        exceptions.append(
            ExceptionInfo(
                type=value.get("type"),
                value=value.get("value"),
                module=value.get("module"),
                mechanism_type=(value.get("mechanism") or {}).get("type"),
                stacktrace_frames=frames,
            )
        )
    return exceptions


def normalize_frames(raw_frames: list[dict[str, Any]]) -> list[StackFrame]:
    """Normalize an already-ordered raw frame list."""
    return [normalize_frame(f) for f in raw_frames or []]
