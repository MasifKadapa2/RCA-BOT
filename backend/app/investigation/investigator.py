"""Investigation orchestrator.

Coordinates the complete V1 workflow:

    Slack thread → SentryReferenceResolver → SentryClient →
    IncidentContextBuilder → CodeRetriever → LLMProvider →
    RCAValidator → InvestigationRepository → SlackThreadResponder

State transitions are persisted (PENDING → INVESTIGATING → COMPLETED/FAILED)
and every failure produces a safe server log plus a human-readable Slack
message. Stack traces, tokens and secrets never reach Slack or the logs.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import MissingSentryReferenceError, SentryEventNotFoundError
from app.core.logging import investigation_logger, reset_correlation_id, set_correlation_id
from app.investigation.context_builder import IncidentContextBuilder
from app.investigation.validator import RCAValidator
from app.llm.provider import LLMProvider
from app.models import IncidentStatus
from app.retrieval.query_builder import build_retrieval_query
from app.services.incident_repository import IncidentRepository
from app.slack.responder import SlackThreadResponder


class IncidentInvestigator:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable,
        sentry_client_factory: Callable[[], object],
        resolver,  # SentryReferenceResolver
        code_retriever,  # CodeRetriever
        llm_provider: LLMProvider,
        validator: RCAValidator | None = None,
        responder: SlackThreadResponder | None = None,
        context_builder: IncidentContextBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._sentry_client_factory = sentry_client_factory
        self._resolver = resolver
        self._code_retriever = code_retriever
        self._llm_provider = llm_provider
        self._validator = validator or (
            RCAValidator(settings.repository_path) if settings.repository_path else None
        )
        self._responder = responder
        self._context_builder = context_builder or IncidentContextBuilder()

    # --- Entry point -----------------------------------------------------------

    def run(self, incident_id: int) -> None:
        token = set_correlation_id(f"inv_{incident_id}")
        log = investigation_logger("app.investigation", incident_id)
        try:
            self._run(incident_id, log)
        except Exception as exc:  # never crash the Slack process
            log.exception("rca_bot.investigation_crashed")
            reason = humanize_error(exc)
            self._fail(incident_id, reason, exc, log)
        finally:
            reset_correlation_id(token)

    # --- Steps -------------------------------------------------------------------

    def _run(self, incident_id: int, log) -> None:
        total_start = time.perf_counter()
        slack_latency_ms = 0
        sentry_latency_ms = 0
        from app.models import Incident, SlackMessage

        slack_start = time.perf_counter()
        with self._session_factory() as db:
            repo = IncidentRepository(db)
            incident = repo.get(incident_id)
            if incident is None:
                log.error("rca_bot.incident_missing")
                return
            if incident.status == IncidentStatus.COMPLETED:
                log.info("rca_bot.already_completed")
                return
            channel_id, thread_ts = incident.slack_channel_id, incident.slack_thread_ts
            repo.set_status(incident, IncidentStatus.INVESTIGATING)

            # Thread texts: anything tied to this incident OR sharing the
            # channel+thread (root message may be stored with thread_ts=None).
            from sqlalchemy import or_

            texts = [
                m.text for m in db.scalars(
                    select(SlackMessage).where(
                        or_(
                            SlackMessage.incident_id == incident.id,
                            (SlackMessage.channel_id == channel_id)
                            & (SlackMessage.thread_ts == thread_ts),
                        )
                    )
                ).all()
                if m.text
            ]
        slack_latency_ms = int((time.perf_counter() - slack_start) * 1000)

        # 1. Resolve the Sentry reference from the Slack thread.
        try:
            reference = self._resolver.resolve_from_messages(texts)
        except MissingSentryReferenceError as exc:
            self._fail(incident_id, str(exc), exc, log,
                       missing_url=True, channel_id=channel_id, thread_ts=thread_ts)
            return

        # 2. Fetch authoritative data from the Sentry Web API.
        sentry_start = time.perf_counter()
        client = self._sentry_client_factory()
        issue_raw = client.get_issue(reference.issue_id)

        event_raw = None
        try:
            event_raw = client.get_latest_event(reference.issue_id)
        except SentryEventNotFoundError:
            log.warning("rca_bot.latest_event_missing")
            try:
                events = client.get_issue_events(reference.issue_id, limit=1)
                event_raw = events[0] if events else None
            except SentryEventNotFoundError:
                event_raw = None
        sentry_latency_ms = int((time.perf_counter() - sentry_start) * 1000)

        issue_data, event_data, context = self._context_builder.build(issue_raw, event_raw)

        with self._session_factory() as db:
            repo = IncidentRepository(db)
            incident = repo.get(incident_id)
            repo.upsert_sentry_issue(
                {
                    "issue_id": issue_data.issue_id,
                    "title": issue_data.title,
                    "environment": issue_data.environment,
                    "release": issue_data.release,
                    "last_seen": issue_data.last_seen,
                    "culprit": issue_data.culprit,
                    "level": issue_data.level,
                    "project": issue_data.project,
                }
            )
            if incident.sentry_issue_id != issue_data.issue_id:
                incident.sentry_issue_id = issue_data.issue_id
            repo.store_context(incident, context)
        log.info("rca_bot.sentry_context_built",
                 extra={"sentry_issue_id": issue_data.issue_id,
                        "frames": len(event_data.frames) if event_data else 0,
                        "sentry_latency_ms": sentry_latency_ms})

        # 3. Retrieve relevant repository code (with trace).
        retrieval_start = time.perf_counter()
        retrieval_latency_ms = None
        llm_latency_ms = None
        validation_latency_ms = None
        query = build_retrieval_query(issue_data, event_data, extra_texts=texts)
        frame_paths = [f.filename for f in (event_data.frames if event_data else []) if f.filename]
        # Use detailed retrieval to capture lexical/vector trace
        if hasattr(self._code_retriever, "retrieve_with_trace"):
            results, retrieval_trace = self._code_retriever.retrieve_with_trace(query, stack_frame_paths=frame_paths)
        else:
            results = self._code_retriever.retrieve(query, stack_frame_paths=frame_paths)
            retrieval_trace = {
                "query": {"semantic_query": query.semantic_query, "exact_filenames": query.exact_filenames, "exact_symbols": query.exact_symbols, "identifiers": query.identifiers},
                "lexical_results": [],
                "vector_results": [],
                "final_context": [{"path": r.relative_path, "start_line": r.start_line, "end_line": r.end_line, "symbol": r.symbol_name} for r in results],
            }
        retrieval_latency_ms = int((time.perf_counter() - retrieval_start) * 1000)
        retrieved_paths = {r.relative_path for r in results}
        log.info("rca_bot.code_retrieved", extra={"results": len(results), "retrieval_latency_ms": retrieval_latency_ms})

        # 4. Ask the local LLM for a grounded RCA.
        from app.llm.prompts import SYSTEM_PROMPT, build_rca_user_prompt
        from app.schemas.rca import IncidentAnalysis

        prompt = build_rca_user_prompt(issue_data, event_data, results, slack_messages=texts)
        llm_start = time.perf_counter()
        analysis = self._llm_provider.generate_structured(
            prompt, schema=IncidentAnalysis, system=SYSTEM_PROMPT,
        )
        llm_latency_ms = int((time.perf_counter() - llm_start) * 1000)
        log.info("rca_bot.llm_analysis_received", extra={"confidence": analysis.confidence, "llm_latency_ms": llm_latency_ms, "analysis_status": getattr(analysis, "analysis_status", "CONFIDENT")})

        # 5. Validate evidence and file references (strict, never hide hallucinations).
        validation_start = time.perf_counter()
        if self._validator is not None:
            validated, report = self._validator.validate(
                analysis, retrieved_paths=retrieved_paths, sentry_issue_id=reference.issue_id, slack_texts=texts
            )
            if report.has_invalid_references:
                removed_note = (
                    f"{len(report.invalid_files)} file reference(s) and/or "
                    f"{len(report.invalid_evidence)} evidence item(s) were removed during validation."
                )
                validated.unknowns.append(f"Removed invalid references: {removed_note}")
        else:
            validated = analysis
        validation_latency_ms = int((time.perf_counter() - validation_start) * 1000)

        # Handle explicit insufficient evidence state
        from app.schemas.rca import AnalysisStatus
        if validated.analysis_status == AnalysisStatus.INSUFFICIENT_EVIDENCE:
            validated.summary = "Unable to establish the root cause from the available evidence."
            validated.root_cause = "Insufficient evidence."
            if not any("failing application path could not be connected" in u for u in validated.unknowns):
                validated.unknowns.append("The failing application path could not be connected to a relevant source implementation.")

        total_latency_ms = int((time.perf_counter() - total_start) * 1000)

        # 6. Persist the investigation.
        with self._session_factory() as db:
            repo = IncidentRepository(db)
            incident = repo.get(incident_id)
            evidence_rows = [
                {
                    "source_type": ev.source_type,
                    "source_reference": ev.source_reference,
                    "explanation": ev.explanation,
                    "is_valid": True,
                }
                for ev in validated.evidence
            ]
            # Prepare model metadata and latencies
            extra_fields = {
                "analysis_status": validated.analysis_status.value if hasattr(validated.analysis_status, "value") else str(validated.analysis_status),
                "llm_model": self._settings.effective_llm_model,
                "embedding_model": getattr(self._settings, "embedding_model", ""),
                "slack_latency_ms": slack_latency_ms,
                "sentry_latency_ms": sentry_latency_ms,
                "retrieval_latency_ms": retrieval_latency_ms,
                "llm_latency_ms": llm_latency_ms,
                "validation_latency_ms": validation_latency_ms,
                "total_latency_ms": total_latency_ms,
                "retrieval_query": retrieval_trace.get("query"),
                "lexical_results": retrieval_trace.get("lexical_results"),
                "vector_results": retrieval_trace.get("vector_results"),
                "final_context": retrieval_trace.get("final_context"),
            }
            # Pass through model_dump with extra
            model_data = validated.model_dump()
            model_data.update({k: v for k, v in extra_fields.items() if k in ["analysis_status"]})
            investigation_row = repo.save_analysis(incident, model_data, evidence_rows, extra_fields=extra_fields)

            context["relevant_files"] = [rf.model_dump() for rf in validated.relevant_files]
            # Persist retrieved snippets so the dashboard can display the exact
            # code that was used as evidence (no arbitrary repo browsing).
            validated_paths = {rf.path for rf in validated.relevant_files}
            context["code_snippets"] = [
                {
                    "path": r.relative_path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "symbol_name": r.symbol_name,
                    "content": r.content,
                }
                for r in results
                if r.relative_path in validated_paths
            ]
            if not context["code_snippets"]:
                context["code_snippets"] = [
                    {
                        "path": r.relative_path,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "symbol_name": r.symbol_name,
                        "content": r.content,
                    }
                    for r in results[:3]
                ]
            repo.store_context(incident, context)
            repo.set_status(incident, IncidentStatus.COMPLETED)

        # 7. Reply in the same Slack thread.
        if self._responder is not None:
            self._responder.post_rca(
                channel_id, thread_ts, validated,
                issue=issue_data, event=event_data,
                investigation_id=investigation_row.id,
            )
        log.info("rca_bot.investigation_completed",
                 extra={"investigation_id": f"inv_{incident_id}"})

    def _fail(
        self,
        incident_id: int,
        safe_reason: str,
        original: Exception,
        log,
        missing_url: bool = False,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        with self._session_factory() as db:
            repo = IncidentRepository(db)
            incident = repo.get(incident_id)
            if incident is not None:
                repo.mark_failed(incident, safe_reason)
                channel_id = channel_id or incident.slack_channel_id
                thread_ts = thread_ts or incident.slack_thread_ts
        log.error("rca_bot.investigation_failed",
                  extra={"reason": safe_reason, "error_type": type(original).__name__,
                         "missing_url": missing_url})
        if self._responder and channel_id and thread_ts:
            if missing_url:
                self._responder.post_missing_sentry_url(channel_id, thread_ts)
            else:
                self._responder.post_failure(channel_id, thread_ts, safe_reason)


def humanize_error(exc: Exception) -> str:
    """Map internal exceptions to safe, human-readable Slack reasons."""
    name = type(exc).__name__
    mapping = {
        "SlackAuthenticationError": "Slack authentication failed. Check SLACK_BOT_TOKEN.",
        "SlackAPIError": "A Slack API call failed.",
        "SlackThreadNotFoundError": "The Slack thread could not be retrieved.",
        "SentryAuthenticationError": "Sentry authentication failed. Check SENTRY_AUTH_TOKEN and SENTRY_ORG.",
        "SentryIssueNotFoundError": "The referenced Sentry issue could not be found.",
        "SentryEventNotFoundError": "No events were found for this Sentry issue.",
        "SentryAPIError": "The Sentry API returned an error.",
        "InvalidSentryURLError": "The Sentry link in this thread could not be parsed.",
        "RepositoryNotFoundError": "The configured REPOSITORY_PATH does not exist. Index the repository first.",
        "EmbeddingError": "Local embedding model failed. Check EMBEDDING_MODEL configuration.",
        "VectorStoreError": "The vector store (ChromaDB) is unavailable.",
        "LLMUnavailableError": "Ollama is unreachable. Ensure Ollama is running (`ollama serve`).",
        "LLMResponseError": "The LLM returned an error. Verify OLLAMA_MODEL is pulled.",
        "LLMMalformedOutputError": "The LLM returned malformed output. Try again or use a stronger model.",
        "DatabaseError": "A database error occurred.",
    }
    return mapping.get(name, str(exc) or "Unexpected internal error.")
