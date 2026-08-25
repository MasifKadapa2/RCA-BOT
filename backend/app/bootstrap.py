"""Production dependency wiring.

Builds all integrations from settings in one place so both the API server
and the Socket Mode runner share identical configuration. Tests construct
their own fakes instead of using this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from slack_sdk.web import WebClient

from app.core.config import Settings, get_settings
from app.db.base import get_session_factory
from app.investigation.investigator import IncidentInvestigator
from app.llm.ollama_provider import OllamaProvider
from app.retrieval.retriever import CodeRetriever
from app.sentry.client import SentryClient
from app.sentry.resolver import SentryReferenceResolver
from app.services.incident_repository import IncidentRepository
from app.slack.formatter import SlackResponseFormatter
from app.slack.gateway import SlackGateway, WebClientGateway
from app.slack.responder import SlackThreadResponder


@dataclass
class Container:
    settings: Settings
    session_factory: Any = field(default=None)
    gateway: SlackGateway | None = None
    resolver: SentryReferenceResolver | None = None
    formatter: SlackResponseFormatter | None = None
    responder: SlackThreadResponder | None = None
    investigator: IncidentInvestigator | None = None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    session_factory = get_session_factory()

    gateway: WebClientGateway | None = None
    if settings.is_slack_configured:
        client = WebClient(token=settings.slack_bot_token)
        gateway = WebClientGateway(client, max_thread_messages=settings.slack_max_thread_messages)

    formatter = SlackResponseFormatter(settings)
    resolver = SentryReferenceResolver()

    def sentry_client_factory() -> SentryClient:
        return SentryClient(
            auth_token=settings.sentry_auth_token,
            organization=settings.sentry_org,
            base_url=settings.sentry_base_url,
        )

    def vector_store_factory():
        from app.ingestion.vector_store import ChromaVectorStore

        return ChromaVectorStore(persist_directory=settings.chroma_path)

    from app.ingestion.embeddings import get_embedding_provider

    code_retriever = CodeRetriever(
        db_session_factory=session_factory,
        embedding_provider=get_embedding_provider(settings.embedding_model),
        vector_store_factory=vector_store_factory,
        max_results=settings.max_retrieval_results,
        max_context_chars=settings.max_llm_context_chars,
    )

    # Priority: generic cloud (Gemini) → Groq → Ollama (local fallback)
    if settings.is_cloud_llm_configured:
        from app.llm.cloud_provider import OpenAICompatProvider

        llm_provider = OpenAICompatProvider(settings)
        llm_model_name = settings.llm_model
    elif settings.is_groq_configured:
        from app.llm.groq_provider import GroqProvider

        llm_provider = GroqProvider(settings)
        llm_model_name = settings.groq_model
    elif settings.is_ollama_configured:
        llm_provider = OllamaProvider(settings)
        llm_model_name = settings.ollama_model
    else:
        llm_provider = None
        llm_model_name = ""

    responder = None
    investigator = None
    if gateway is not None:
        responder = SlackThreadResponder(gateway, formatter)

    if llm_provider is not None and settings.repository_path:
        from app.investigation.validator import RCAValidator

        investigator = IncidentInvestigator(
            settings=settings,
            session_factory=session_factory,
            sentry_client_factory=sentry_client_factory,
            resolver=resolver,
            code_retriever=code_retriever,
            llm_provider=llm_provider,
            validator=RCAValidator(settings.repository_path),
            responder=responder,
        )

    return Container(
        settings=settings,
        session_factory=session_factory,
        gateway=gateway,
        resolver=resolver,
        formatter=formatter,
        responder=responder,
        investigator=investigator,
    )


def run_investigation(container: Container, incident_id: int) -> None:
    """Synchronous investigation runner used by background threads."""
    assert container.investigator is not None
    container.investigator.run(incident_id)


__all__ = ["Container", "build_container", "run_investigation"]
