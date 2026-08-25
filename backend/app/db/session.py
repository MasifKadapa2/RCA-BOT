"""Session factory used by background services (investigator, indexer).

The FastAPI layer uses ``app.db.base.get_db``; long-lived workers use
:class:`SessionFactory` so each unit of work gets its own scoped session.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


class SessionFactory:
    """Creates short-lived sessions bound to the configured engine."""

    _lock = Lock()
    _shared: "SessionFactory | None" = None

    def __init__(self, settings: Settings | None = None) -> None:
        from app.db.base import get_session_factory as _base_factory

        self._settings = settings
        self._factory = None
        self._base_factory_fn = _base_factory

    def _ensure_factory(self) -> sessionmaker:
        if self._factory is None:
            if self._settings is not None:
                # Build a dedicated engine for these settings (tests inject tmp DBs).
                from app.db.base import _configure, reset_engine

                engine, factory = _configure(self._settings.database_url)
                self._engine = engine
                self._factory = factory
            else:
                self._factory = self._base_factory_fn()
        return self._factory

    @contextmanager
    def scope(self):
        """Provide a session; commit on success, rollback on error."""
        session: Session = self._ensure_factory()()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Convenience: allow using the factory directly as a callable.
    def __call__(self) -> Session:
        return self._ensure_factory()()


def get_shared_session_factory() -> SessionFactory:
    """Process-wide default factory bound to app settings."""
    with SessionFactory._lock:
        if SessionFactory._shared is None:
            SessionFactory._shared = SessionFactory(get_settings())
    return SessionFactory._shared
