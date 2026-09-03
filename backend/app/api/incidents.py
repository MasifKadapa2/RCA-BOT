"""Incident API endpoints (dashboard read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models import Evidence as EvidenceRow
from app.models import Incident, Investigation
from app.schemas.incidents import (
    EvidenceOut,
    IncidentDetail,
    IncidentListItem,
    InvestigationSummary,
    RegressionOut,
    RetrievalTraceOut,
)

router = APIRouter(prefix="/api", tags=["incidents"])


def _get_incident_or_404(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.get("/incidents", response_model=list[IncidentListItem])
def list_incidents(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    stmt = (
        select(Incident, Investigation)
        .outerjoin(Investigation, Investigation.incident_id == Incident.id)
        .order_by(desc(Incident.created_at))
        .limit(min(limit, 200))
        .offset(offset)
    )
    rows = db.execute(stmt).all()
    items = []
    for incident, investigation in rows:
        sentry_title = incident.sentry_issue.title if incident.sentry_issue else None
        items.append(
            IncidentListItem(
                id=incident.id,
                status=incident.status,
                sentry_issue_id=incident.sentry_issue_id,
                title=sentry_title or incident.error_message or "Unknown issue",
                confidence=investigation.confidence if investigation else None,
                slack_channel_id=incident.slack_channel_id,
                slack_thread_ts=incident.slack_thread_ts,
                created_at=incident.created_at,
            )
        )
    return items


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    incident = _get_incident_or_404(db, incident_id)
    investigation = (
        db.execute(
            select(Investigation)
            .where(Investigation.incident_id == incident.id)
            .order_by(desc(Investigation.id))
            .limit(1)
        )
        .scalars()
        .first()
    )

    investigation_out = None
    evidence_out: list[EvidenceOut] = []
    if investigation:
        investigation_out = InvestigationSummary(
            id=investigation.id,
            summary=investigation.summary,
            root_cause=investigation.root_cause,
            suggested_fix=investigation.suggested_fix,
            confidence=investigation.confidence,
            analysis_status=investigation.analysis_status,
            unknowns=investigation.unknowns or [],
            affected_components=investigation.affected_components or [],
            created_at=investigation.created_at,
            completed_at=investigation.completed_at,
            llm_model=investigation.llm_model,
            embedding_model=investigation.embedding_model,
            slack_latency_ms=investigation.slack_latency_ms,
            sentry_latency_ms=investigation.sentry_latency_ms,
            retrieval_latency_ms=investigation.retrieval_latency_ms,
            llm_latency_ms=investigation.llm_latency_ms,
            validation_latency_ms=investigation.validation_latency_ms,
            total_latency_ms=investigation.total_latency_ms,
        )
        evidence_rows = db.scalars(
            select(EvidenceRow).where(EvidenceRow.investigation_id == investigation.id)
        ).all()
        evidence_out = [
            EvidenceOut(
                source_type=e.source_type,
                source_reference=e.source_reference,
                explanation=e.explanation,
                is_valid=e.is_valid,
                metadata=e.metadata_json or {},
            )
            for e in evidence_rows
        ]

    sentry = incident.sentry_issue
    context = incident.context or {}
    relevant_files = [
        {
            "path": rf.get("path"),
            "start_line": rf.get("start_line"),
            "end_line": rf.get("end_line"),
            "reason": rf.get("reason", ""),
        }
        for rf in (context.get("relevant_files") or [])
    ]
    code_snippets = [
        {
            "path": snippet.get("path"),
            "start_line": snippet.get("start_line"),
            "end_line": snippet.get("end_line"),
            "symbol_name": snippet.get("symbol_name"),
            "content": snippet.get("content"),
        }
        for snippet in (context.get("code_snippets") or [])
    ]

    return IncidentDetail(
        id=incident.id,
        status=incident.status,
        error_message=incident.error_message,
        sentry_issue_id=incident.sentry_issue_id,
        sentry_title=sentry.title if sentry else None,
        sentry_environment=sentry.environment if sentry else None,
        sentry_release=sentry.release if sentry else None,
        slack_channel_id=incident.slack_channel_id,
        slack_thread_ts=incident.slack_thread_ts,
        triggering_message_ts=incident.triggering_message_ts,
        exception=context.get("exception"),
        stack_trace=context.get("frames") or [],
        release=context.get("release"),
        environment=context.get("environment"),
        platform=context.get("platform"),
        transaction=context.get("transaction"),
        tags=context.get("tags") or {},
        timestamp=context.get("timestamp"),
        investigation=investigation_out,
        evidence=evidence_out,
        relevant_files=relevant_files,
        code_snippets=code_snippets,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


@router.get("/incidents/{incident_id}/evidence", response_model=list[EvidenceOut])
def get_incident_evidence(incident_id: int, db: Session = Depends(get_db)):
    _get_incident_or_404(db, incident_id)
    stmt = (
        select(EvidenceRow)
        .join(Investigation, Investigation.id == EvidenceRow.investigation_id)
        .where(Investigation.incident_id == incident_id)
        .order_by(EvidenceRow.id)
    )
    rows = db.scalars(stmt).all()
    return [
        EvidenceOut(
            source_type=e.source_type,
            source_reference=e.source_reference,
            explanation=e.explanation,
            is_valid=e.is_valid,
            metadata=e.metadata_json or {},
        )
        for e in rows
    ]


@router.get("/incidents/{incident_id}/retrieval", response_model=RetrievalTraceOut)
def get_retrieval_trace(incident_id: int, db: Session = Depends(get_db)):
    _get_incident_or_404(db, incident_id)
    investigation = (
        db.execute(
            select(Investigation)
            .where(Investigation.incident_id == incident_id)
            .order_by(desc(Investigation.id))
            .limit(1)
        )
        .scalars()
        .first()
    )
    if investigation is None:
        raise HTTPException(status_code=404, detail="No investigation found for incident")
    return RetrievalTraceOut(
        query=investigation.retrieval_query,
        lexical_results=investigation.lexical_results,
        vector_results=investigation.vector_results,
        final_context=investigation.final_context,
    )


@router.get("/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Incident.id))) or 0
    completed = (
        db.scalar(select(func.count(Incident.id)).where(Incident.status == "COMPLETED")) or 0
    )
    failed = db.scalar(select(func.count(Incident.id)).where(Incident.status == "FAILED")) or 0
    avg_confidence = db.scalar(select(func.avg(Investigation.confidence))) or 0
    return {
        "total_investigations": total,
        "completed_investigations": completed,
        "failed_investigations": failed,
        "average_confidence": round(float(avg_confidence), 1),
    }


# --- V2 — Change Intelligence ---------------------------------------------------

def _latest_regression(db: Session, incident_id: int) -> Investigation | None:
    return db.execute(
        select(Investigation)
        .where(
            Investigation.incident_id == incident_id,
            Investigation.regression_data.is_not(None),
        )
        .order_by(desc(Investigation.id))
        .limit(1)
    ).scalars().first()


@router.get("/incidents/{incident_id}/regression", response_model=RegressionOut)
def get_regression(incident_id: int, db: Session = Depends(get_db)):
    _get_incident_or_404(db, incident_id)
    investigation = _latest_regression(db, incident_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="No change intelligence for this incident")
    data = investigation.regression_data or {}
    return RegressionOut(
        status=investigation.regression_status,
        likely_commit=data.get("likely_commit"),
        confidence=data.get("confidence"),
        causal_explanation=data.get("causal_explanation"),
        evidence_for=data.get("evidence_for") or [],
        evidence_against=data.get("evidence_against") or [],
        unknowns=data.get("unknowns") or [],
        recommended_action=data.get("recommended_action"),
        regression_model=investigation.regression_model,
        candidates=data.get("candidate_commits") or [],
    )


@router.get("/incidents/{incident_id}/changes", response_model=RegressionOut)
def get_changes(incident_id: int, db: Session = Depends(get_db)):
    """Alias of /regression focused on candidate commits."""
    return get_regression(incident_id, db)


@router.get("/incidents/{incident_id}/timeline")
def get_timeline(incident_id: int, db: Session = Depends(get_db)):
    from app.change_intelligence import build_timeline

    incident = _get_incident_or_404(db, incident_id)
    investigation = _latest_regression(db, incident_id) or (
        db.execute(
            select(Investigation).where(Investigation.incident_id == incident.id)
            .order_by(desc(Investigation.id)).limit(1)
        ).scalars().first()
    )
    analysis = None
    if investigation and investigation.regression_data:
        from app.schemas.regression import RegressionAnalysis

        try:
            analysis = RegressionAnalysis.model_validate(investigation.regression_data)
        except Exception:
            analysis = None
    context = incident.context or {}
    timeline = build_timeline(
        analysis,
        issue_first_seen=context.get("timestamp"),
        investigation_created_at=incident.created_at,
        investigation_completed_at=(
            investigation.completed_at if investigation else None
        ),
    )
    return {"entries": timeline}
