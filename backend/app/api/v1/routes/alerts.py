"""FastAPI API routes for Risk Alerts & In-App Notification System (Phase 2.7).

Provides:
- Public read-only access to alerts and operational alert summary.
- Authenticated operational acknowledgement requiring administrative token (X-Ingestion-Token).
- Parameter validation, sanitized responses, and prototype research disclaimers.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.routes.ingestion import verify_ingestion_token
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClientError
from app.schemas.alert import (
    AlertAcknowledgeRequest,
    AlertResolveRequest,
    AlertResponse,
    AlertSeverity,
    AlertStatus,
    AlertSummaryResponse,
    AlertType,
    AlertsListResponse,
)
from app.services.alert_service import AlertService, get_alert_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Risk Alerts & Notifications"])


def _mask_alert_for_response(alert: AlertResponse) -> AlertResponse:
    """Redact internal fingerprint and mask operator identity consistently across alert responses."""
    alert.fingerprint = None
    if alert.acknowledged_by:
        alert.acknowledged_by = "operator"
    return alert


@router.get(
    "",
    response_model=AlertsListResponse,
    summary="List Risk Alerts",
    description=(
        "Retrieve in-app rainfall risk alerts and data-quality notifications for pilot locations. "
        "Supports filtering by status, severity, alert type, and pilot location."
    ),
)
async def list_alerts(
    location_id: Optional[str] = Query(None, description="Filter by pilot location UUID"),
    status_filter: Optional[AlertStatus] = Query(None, alias="status", description="Filter by lifecycle status"),
    severity: Optional[AlertSeverity] = Query(None, description="Filter by operational severity"),
    alert_type: Optional[AlertType] = Query(None, description="Filter by categorical alert type"),
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
    service: AlertService = Depends(get_alert_service),
) -> AlertsListResponse:
    try:
        res = await service.get_alerts(
            location_id=location_id,
            status=status_filter.value if status_filter else None,
            severity=severity.value if severity else None,
            alert_type=alert_type.value if alert_type else None,
            limit=limit,
            offset=offset,
        )
        # Protect operator identity and internal fingerprint on public read endpoint
        for a in res.alerts:
            _mask_alert_for_response(a)
        return res
    except SupabaseClientError as exc:
        logger.error("Database error retrieving alerts: %s", sanitize_error_message(exc.message))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Database persistence error retrieving alert records."},
        )
    except Exception as exc:
        logger.error("Error retrieving alerts: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "alert_query_error", "message": "Failed to retrieve alert records."},
        )


@router.get(
    "/summary",
    response_model=AlertSummaryResponse,
    summary="Get Alert Summary & KPI Indicators",
    description=(
        "Retrieve high-level operational counts of active alerts, severity breakdowns, "
        "and recent notifications for navbar badges and dashboard widgets."
    ),
)
async def get_alerts_summary(
    service: AlertService = Depends(get_alert_service),
) -> AlertSummaryResponse:
    try:
        summary = await service.get_summary()
        for a in summary.recent_alerts:
            _mask_alert_for_response(a)
        return summary
    except SupabaseClientError as exc:
        logger.error("Database error retrieving alert summary: %s", sanitize_error_message(exc.message))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Database persistence error computing alert summary."},
        )
    except Exception as exc:
        logger.error("Error retrieving alert summary: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "alert_summary_error", "message": "Failed to compute alert summary."},
        )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get Alert by ID",
    description="Retrieve full details and meteorological evidence for an individual alert.",
)
async def get_alert(
    alert_id: str,
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    try:
        alert = await service.get_alert_by_id(alert_id)
    except SupabaseClientError as exc:
        logger.error("Database error retrieving alert '%s': %s", alert_id, sanitize_error_message(exc.message))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Database persistence error retrieving alert record."},
        )
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "alert_not_found", "message": f"Alert '{alert_id}' not found."},
        )
    return _mask_alert_for_response(alert)


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertResponse,
    summary="Acknowledge Alert (Authorized)",
    description=(
        "Acknowledge an active alert without changing the underlying meteorological risk calculation. "
        "Requires administrative authentication via X-Ingestion-Token header."
    ),
)
async def acknowledge_alert(
    alert_id: str,
    payload: AlertAcknowledgeRequest = AlertAcknowledgeRequest(),
    authorized: bool = Depends(verify_ingestion_token),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    try:
        existing = await service.get_alert_by_id(alert_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "alert_not_found", "message": f"Alert '{alert_id}' not found."},
            )

        ack_res = await service.acknowledge_alert(
            alert_id=alert_id,
            acknowledged_by=payload.acknowledged_by or "operator",
        )
    except HTTPException:
        raise
    except SupabaseClientError as exc:
        logger.error("Database persistence error acknowledging alert '%s': %s", alert_id, sanitize_error_message(exc.message))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Failed to persist alert acknowledgement to database."},
        )

    if not ack_res:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "acknowledgement_failed", "message": "Failed to update alert acknowledgement status."},
        )
    return _mask_alert_for_response(ack_res)


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertResponse,
    summary="Resolve Alert (Authorized)",
    description=(
        "Resolve an open risk alert with an operational resolution reason. "
        "Requires administrative authentication via X-Ingestion-Token header."
    ),
)
async def resolve_alert(
    alert_id: str,
    payload: AlertResolveRequest,
    authorized: bool = Depends(verify_ingestion_token),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    # Validate non-empty resolution reason
    if not payload.resolved_reason or not payload.resolved_reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_reason", "message": "A non-empty resolution reason must be provided."},
        )

    try:
        existing = await service.get_alert_by_id(alert_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "alert_not_found", "message": f"Alert '{alert_id}' not found."},
            )

        # Allow resolution only for valid open alert states
        if existing.status == AlertStatus.RESOLVED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_lifecycle_transition",
                    "message": f"Alert '{alert_id}' is already in RESOLVED status and cannot be resolved again.",
                },
            )

        resolve_res = await service.resolve_alert(
            alert_id=alert_id,
            resolved_reason=payload.resolved_reason.strip(),
            resolved_by=payload.resolved_by or "operator",
        )
    except HTTPException:
        raise
    except SupabaseClientError as exc:
        logger.error("Database persistence error resolving alert '%s': %s", alert_id, sanitize_error_message(exc.message))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Failed to persist alert resolution to database."},
        )

    if not resolve_res:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "resolution_failed", "message": "Failed to update alert resolution status."},
        )

    return _mask_alert_for_response(resolve_res)
