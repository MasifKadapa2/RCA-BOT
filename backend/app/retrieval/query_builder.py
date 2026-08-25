"""Builds retrieval queries from incident context.

Prioritizes exact filenames and symbols from the stack trace, then adds
exception-derived identifiers for lexical search and a semantic query for the
vector store.
"""

from __future__ import annotations

import re

from app.schemas.retrieval import RetrievalQuery
from app.schemas.sentry import SentryEventData, SentryIssueData

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _split_identifier(identifier: str) -> list[str]:
    """employee_serializer.rb / EmployeeSerializer -> useful sub-terms."""
    terms: list[str] = []
    for part in _WORD.findall(identifier):
        terms.append(part)
        terms.extend(_CAMEL.split(part))
    lowered = {t.lower() for t in terms if len(t) >= 3}
    return sorted(lowered)


def extract_filenames(texts) -> list[str]:
    names: list[str] = []
    for text in texts:
        if not text:
            continue
        for token in re.findall(r"[\w./\\-]+\.\w{1,10}", text or ""):
            base = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if re.fullmatch(r"[A-Za-z_][\w.-]*", base):
                names.append(base)
    return names


class QueryBuilder:
    def build(
        self,
        issue: SentryIssueData | None,
        event: SentryEventData | None,
        extra_texts: list[str] | None = None,
    ) -> RetrievalQuery:
        filenames: list[str] = []
        symbols: list[str] = []
        identifiers: set[str] = set()

        if event:
            for frame in event.frames:
                if frame.filename:
                    base = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
                    if "." in base and "site-packages" not in (frame.filename or ""):
                        filenames.append(base)
                if frame.function:
                    symbols.append(frame.function)
                if frame.module:
                    identifiers.update(_split_identifier(frame.module))

        exception_type = ""
        exception_value = ""
        if event:
            for exc in event.exceptions or []:
                if exc.type or exc.value:
                    exception_type = exc.type or ""
                    exception_value = exc.value or ""
                    break
            for term in _split_identifier(exception_type):
                identifiers.add(term)

        # Exception messages often mention methods/attributes; keep distinctive words.
        message_words = _split_identifier(f"{exception_value} {(issue.title or '')}")
        identifiers.update(message_words)
        if extra_texts:
            for name in extract_filenames(extra_texts):
                filenames.append(name)

        # Deduplicate while preserving order/priority.
        def dedupe(items: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for item in items:
                key = item.lower()
                if item and key not in seen:
                    seen.add(key)
                    out.append(item)
            return out

        semantic_parts = [
            exception_type,
            exception_value,
            issue.title if issue else "",
            issue.culprit if issue else "",
            event.transaction if event else "",
            " ".join(symbols[:6]),
            " ".join(filenames[:4]),
        ]
        semantic_query = " ".join(part for part in semantic_parts if part).strip()

        return RetrievalQuery(
            semantic_query=semantic_query,
            exact_filenames=dedupe(filenames),
            exact_symbols=dedupe(symbols),
            identifiers=dedupe(sorted(identifiers))[:40],
        )


def build_retrieval_query(issue, event, extra_texts=None) -> RetrievalQuery:
    return QueryBuilder().build(issue, event, extra_texts)
