"""FastAPI application entry point (dashboard API)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import incidents, repositories
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.base import init_db

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db()
        logger.info("rca_bot.api_started")
        yield

    application = FastAPI(
        title="RCA Bot",
        version="1.0.0",
        description="Local-first AI assistant that investigates Sentry issues from Slack.",
        lifespan=lifespan,
    )
    application.include_router(incidents.router)
    application.include_router(repositories.router)

    @application.get("/health", tags=["health"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": "1.0.0",
            "components": {
                "slack_configured": settings.is_slack_configured,
                "sentry_configured": settings.is_sentry_configured,
                "ollama_configured": settings.is_ollama_configured,
                "repository_configured": bool(settings.repository_path),
            },
        }

    return application


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":  # pragma: no cover
    main()
