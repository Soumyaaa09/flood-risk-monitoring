import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.routes.ingestion import verify_ingestion_token
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClientError
from app.schemas.monitoring import (
    MonitoringIngestionHealthResponse,
    MonitoringLocationsResponse,
    MonitoringSummaryResponse,
    SchedulerStatusResponse,
)
from app.services.ingestion_scheduler import IngestionScheduler, get_ingestion_scheduler
from app.services.monitoring_service import MonitoringService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["Monitoring & Data Quality"])


def get_monitoring_service() -> MonitoringService:
    return MonitoringService()


@router.get(
    "/summary",
    response_model=MonitoringSummaryResponse,
    summary="Get Operational Monitoring Summary",
    description=(
        "Retrieve high-level data freshness, ingestion telemetry, and data-quality status "
        "across all pilot monitoring locations (Paradeep, Cuttack, Kendrapara)."
    ),
)
async def get_monitoring_summary(
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringSummaryResponse:
    try:
        return await service.get_monitoring_summary()
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during monitoring summary lookup."
        logger.error("Supabase client error in monitoring summary: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve monitoring summary from database."},
        )
    except Exception as exc:
        logger.error("Unexpected error in monitoring summary: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "monitoring_error", "message": "An unexpected error occurred retrieving monitoring telemetry."},
        )


@router.get(
    "/scheduler",
    response_model=SchedulerStatusResponse,
    summary="Get Ingestion Scheduler Status",
    description=(
        "Retrieve operational status, configured collection interval, last cycle outcome, "
        "and next scheduled cycle time for the automated weather data ingestion scheduler."
    ),
)
async def get_scheduler_status(
    scheduler: IngestionScheduler = Depends(get_ingestion_scheduler),
) -> SchedulerStatusResponse:
    try:
        return scheduler.get_status()
    except Exception as exc:
        logger.error("Error retrieving scheduler status: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "scheduler_error", "message": "Failed to retrieve scheduler status."},
        )


@router.get(
    "/locations",
    response_model=MonitoringLocationsResponse,
    summary="Get Location Data Quality & Freshness Health",
    description=(
        "Retrieve detailed observation quality validation and freshness status for each registered pilot location."
    ),
)
async def get_monitoring_locations(
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringLocationsResponse:
    try:
        return await service.get_monitoring_locations()
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during location quality lookup."
        logger.error("Supabase client error in location quality lookup: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve location quality telemetry."},
        )
    except Exception as exc:
        logger.error("Unexpected error in location quality lookup: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "monitoring_error", "message": "An unexpected error occurred retrieving location quality telemetry."},
        )


@router.get(
    "/ingestion-health",
    response_model=MonitoringIngestionHealthResponse,
    summary="Get Ingestion Pipeline Health & Stale Runs (Authorized)",
    description=(
        "Retrieve operational audit counters, recent ingestion runs, and uncompleted or stale runs. "
        "Protected endpoint requiring administrative X-Ingestion-Token header authorization."
    ),
)
async def get_ingestion_health(
    authorized: bool = Depends(verify_ingestion_token),
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringIngestionHealthResponse:
    try:
        return await service.get_ingestion_health()
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during ingestion health lookup."
        logger.error("Supabase client error in ingestion health: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve ingestion pipeline health telemetry."},
        )
    except Exception as exc:
        logger.error("Unexpected error in ingestion health: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "monitoring_error", "message": "An unexpected error occurred retrieving ingestion health telemetry."},
        )
