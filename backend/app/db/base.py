"""SQLAlchemy engine, session factory and declarative base.

All services receive a ``Session`` explicitly which keeps them testable
without monkeypatching globals. The FastAPI layer uses ``get_db``.
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = Lock()


def _configure(url: str) -> tuple[Engine, sessionmaker[Session]]:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, factory


def get_engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            _engine, _session_factory = _configure(get_settings().database_url)
        return _engine


def reset_engine() -> None:
    """Dispose cached engine (used by tests and CLI tools)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    with _lock:
        if _session_factory is None:
            _, _session_factory = _configure(get_settings().database_url)
        return _session_factory


def init_db() -> None:
    """Create all tables (idempotent)."""
    # Import models so metadata is fully populated.
    from pathlib import Path

    from app import models  # noqa: F401
    from app.core.config import get_settings

    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        db_file = url.replace("sqlite:///", "", 1)
        if db_file and db_file != ":memory:":
            Path(db_file).expanduser().parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine()
    Base.metadata.create_all(engine)
    # Lightweight migration: add new Investigation columns if DB was created with old schema
    _migrate_investigations(engine)


def _migrate_investigations(engine) -> None:
    """Add new Investigation columns to existing SQLite DBs without dropping data."""
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(investigations)"))
            existing = {row[1] for row in result.fetchall()}
            # Map of column -> SQL type
            to_add: dict[str, str] = {}
            if "analysis_status" not in existing:
                to_add["analysis_status"] = "VARCHAR(32)"
            if "llm_model" not in existing:
                to_add["llm_model"] = "VARCHAR(255)"
            if "embedding_model" not in existing:
                to_add["embedding_model"] = "VARCHAR(255)"
            for col in [
                "slack_latency_ms",
                "sentry_latency_ms",
                "retrieval_latency_ms",
                "llm_latency_ms",
                "validation_latency_ms",
                "total_latency_ms",
            ]:
                if col not in existing:
                    to_add[col] = "INTEGER"
            for col in ["retrieval_query", "lexical_results", "vector_results", "final_context"]:
                if col not in existing:
                    to_add[col] = "JSON"
            for col, sql_type in to_add.items():
                conn.execute(text(f"ALTER TABLE investigations ADD COLUMN {col} {sql_type}"))
            if to_add:
                conn.commit()
    except Exception:
        # Best-effort: if migration fails, fresh DBs still work via create_all
        pass


def get_db() -> Iterator[Session]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
