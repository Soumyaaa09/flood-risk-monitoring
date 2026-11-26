import logging
import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.config import get_settings
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClientError
from app.schemas.ingestion import (
    IngestionRunResponse,
    IngestionTriggerResponse,
)
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingestion", tags=["Weather Ingestion"])


def get_ingestion_service() -> IngestionService:
    return IngestionService()


def verify_ingestion_token(
    x_ingestion_token: Optional[str] = Header(
        None,
        alias="X-Ingestion-Token",
        description="Backend administrative ingestion authorization token",
    ),
) -> bool:
    """Authorize ingestion administrative access strictly via X-Ingestion-Token header.
    
    Query parameter authentication is explicitly rejected to prevent token leakage
    in web server access logs, reverse proxies, and browser histories.
    """
    settings = get_settings()
    configured_token = settings.INGESTION_TRIGGER_TOKEN.strip()
    provided_token = (x_ingestion_token or "").strip()

    if not provided_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "message": "Missing required 'X-Ingestion-Token' header for ingestion access.",
            },
        )

    if not configured_token or not secrets.compare_digest(provided_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "Invalid ingestion authorization token."},
        )

    return True


@router.post(
    "/trigger",
    response_model=IngestionTriggerResponse,
    summary="Trigger Weather Data Ingestion",
    description=(
        "Fetch precipitation data from Open-Meteo for all pilot locations, classify as analysis "
        "and active forecasts, and persist atomically in Supabase with audit logging."
    ),
)
async def trigger_ingestion(
    location_id: Optional[str] = Query(
        None, description="Optional UUID to trigger ingestion for a single location only"
    ),
    authorized: bool = Depends(verify_ingestion_token),
    service: IngestionService = Depends(get_ingestion_service),
) -> IngestionTriggerResponse:
    try:
        return await service.trigger_ingestion(location_id=location_id)
    except ValueError as exc:
        safe_msg = sanitize_error_message(str(exc)) or "Invalid ingestion parameter or missing data source."
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "resource_not_found", "message": safe_msg},
        )
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during ingestion."
        logger.error("Supabase client error during ingestion: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Internal database error during ingestion."},
        )
    except Exception as exc:
        logger.error("Unexpected error during ingestion: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "ingestion_failed", "message": "An error occurred during weather data ingestion."},
        )


@router.get(
    "/runs",
    response_model=List[IngestionRunResponse],
    summary="List Ingestion Runs",
    description="Fetch recent ingestion runs with operational counters and status. Requires administrative token.",
)
async def list_ingestion_runs(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum number of runs to return"),
    authorized: bool = Depends(verify_ingestion_token),
    service: IngestionService = Depends(get_ingestion_service),
) -> List[IngestionRunResponse]:
    try:
        return await service.get_ingestion_runs(limit=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to query ingestion runs: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "query_failed", "message": "Failed to retrieve ingestion runs."},
        )


@router.get(
    "/runs/{run_id}",
    response_model=IngestionRunResponse,
    summary="Get Ingestion Run Detail",
    description="Fetch audit detail and counters for a specific ingestion run by UUID. Requires administrative token.",
)
async def get_ingestion_run(
    run_id: str,
    authorized: bool = Depends(verify_ingestion_token),
    service: IngestionService = Depends(get_ingestion_service),
) -> IngestionRunResponse:
    try:
        run = await service.get_ingestion_run_by_id(run_id)
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "message": f"Ingestion run '{run_id}' not found."},
            )
        return run
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to query ingestion run '%s': %s", run_id, exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "query_failed", "message": "Failed to retrieve ingestion run detail."},
        )
