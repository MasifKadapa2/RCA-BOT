"""Structured logging with correlation IDs.

Every investigation logs with ``investigation_id=inv_<id>`` so its full trail
can be filtered out of the logs. Tokens and secrets must never be passed to
loggers; this module provides no way to configure sensitive fields.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from typing import Any

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}


def set_correlation_id(correlation_id: str) -> contextvars.Token:
    return _correlation_id.set(correlation_id)


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


@contextlib.contextmanager
def correlation(correlation_id: str):
    """Context manager binding all log records to a correlation id."""
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not getattr(record, "correlation_id", ""):
            record.correlation_id = _correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal JSON lines formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", ""),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # Third party noise reduction.
    for noisy in ("httpx", "httpcore", "urllib3", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def investigation_logger(name: str, investigation_id: int) -> logging.LoggerAdapter:
    """Return a logger adapter tagged with the investigation correlation id."""
    return logging.LoggerAdapter(
        get_logger(name),
        {"correlation_id": f"inv_{investigation_id}"},
    )


# --- Compatibility helpers used across the app -----------------------------

import contextlib as _contextlib


@_contextlib.contextmanager
def correlation(correlation_id: str):
    """Context manager binding a correlation id to every log record."""
    token = set_correlation_id(correlation_id)
    try:
        yield
    finally:
        reset_correlation_id(token)


def configure_logging(level: str = "INFO") -> None:
    """Alias of :func:`setup_logging`."""
    setup_logging(level)
