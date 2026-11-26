"""FastAPI Route for FloodWatch Flood-Risk Assessment Engine.

Provides transparent, read-only endpoints for:
1. Rainfall-based surface runoff risk categorization
2. Location-specific flood-risk assessments
3. Configurable research threshold inspection
4. Hydrological component operational readiness
"""

import logging
from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClientError
from app.risk_engine.config import RainfallRiskConfig, get_risk_config
from app.schemas.risk import (
    AntecedentRainfallIndicator,
    ComponentReadiness,
    HistoricalRainfallResponse,
    HistoricalRainfallSummaryResponse,
    RainfallRiskAssessmentResponse,
    RainfallRiskSummaryResponse,
)
from app.services.risk_service import RiskService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk", tags=["Flood Risk Assessment"])


def get_risk_service() -> RiskService:
    return RiskService()


@router.get(
    "/rainfall",
    response_model=Union[RainfallRiskSummaryResponse, RainfallRiskAssessmentResponse],
    summary="Get Rainfall-Based Flood Risk Assessment",
    description=(
        "Retrieve rainfall-based surface runoff risk categorization across all pilot locations "
        "(Paradeep, Cuttack, Kendrapara) or for an individual location via the optional location_id query parameter.\n\n"
        "**DISCLAIMER:** Research prototype based on atmospheric precipitation. "
        "Does not constitute an official flood warning."
    ),
)
async def get_rainfall_risk(
    location_id: Optional[str] = Query(
        None,
        description="Optional pilot location UUID to filter a single station assessment",
    ),
    service: RiskService = Depends(get_risk_service),
):
    try:
        if location_id:
            assessment = await service.get_location_rainfall_risk(location_id=location_id.strip())
            if not assessment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "location_not_found",
                        "message": f"Pilot monitoring location with ID '{location_id}' not found.",
                    },
                )
            return assessment

        return await service.get_all_locations_rainfall_risk()

    except HTTPException:
        raise
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during risk lookup."
        logger.error("Supabase client error in rainfall risk route: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve risk telemetry from database."},
        )
    except Exception as exc:
        logger.error("Unexpected error in rainfall risk route: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "risk_assessment_error", "message": "An unexpected error occurred during risk assessment."},
        )


@router.get(
    "/rainfall/history",
    response_model=Union[HistoricalRainfallSummaryResponse, HistoricalRainfallResponse],
    summary="Get Historical Rainfall Analysis",
    description=(
        "Retrieve historical rainfall aggregations (24h, 72h, 7d totals, daily breakdown, "
        "consecutive rainy days, trend, and data coverage) across pilot locations or for an individual location.\n\n"
        "**Principle:** Forecast records are strictly excluded from all historical analytics."
    ),
)
async def get_rainfall_history(
    location_id: Optional[str] = Query(
        None,
        description="Optional pilot location UUID to filter historical rainfall for a single station",
    ),
    days: int = Query(
        7,
        ge=1,
        le=30,
        description="Historical observation window in days (1 to 30, defaults to 7)",
    ),
    service: RiskService = Depends(get_risk_service),
):
    try:
        if location_id:
            loc_history = await service.get_location_rainfall_history(
                location_id=location_id.strip(), days=days
            )
            if not loc_history:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "location_not_found",
                        "message": f"Pilot monitoring location with ID '{location_id}' not found.",
                    },
                )
            return loc_history

        return await service.get_all_locations_rainfall_history(days=days)

    except HTTPException:
        raise
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during historical rainfall lookup."
        logger.error("Supabase client error in rainfall history route: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve historical observations from database."},
        )
    except Exception as exc:
        logger.error("Unexpected error in rainfall history route: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "history_error", "message": "An unexpected error occurred during historical analysis."},
        )


@router.get(
    "/rainfall/antecedent",
    summary="Get Antecedent Rainfall Indicators",
    description=(
        "Retrieve experimental Antecedent Rainfall Indicators (multi-window weighted precipitation accumulation) "
        "and underlying 24h, 72h, and 7d observed totals."
    ),
)
async def get_antecedent_rainfall_indicators(
    location_id: Optional[str] = Query(
        None,
        description="Optional pilot location UUID to filter antecedent indicator for a single station",
    ),
    service: RiskService = Depends(get_risk_service),
):
    try:
        if location_id:
            loc_history = await service.get_location_rainfall_history(
                location_id=location_id.strip(), days=7
            )
            if not loc_history:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "location_not_found",
                        "message": f"Pilot monitoring location with ID '{location_id}' not found.",
                    },
                )
            return loc_history.antecedent

        summary = await service.get_all_locations_rainfall_history(days=7)
        return {
            "total_locations": summary.total_locations,
            "assessment_timestamp": summary.assessment_timestamp,
            "antecedent_indicators": [
                {
                    "location_id": l.location_id,
                    "location_name": l.location_name,
                    "district_name": l.district_name,
                    "antecedent": l.antecedent,
                }
                for l in summary.locations
            ],
            "disclaimer": summary.disclaimer,
        }

    except HTTPException:
        raise
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during antecedent lookup."
        logger.error("Supabase client error in antecedent route: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve antecedent data from database."},
        )
    except Exception as exc:
        logger.error("Unexpected error in antecedent route: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "antecedent_error", "message": "An unexpected error occurred retrieving antecedent indicators."},
        )


@router.get(
    "/rainfall/{location_id}",
    response_model=RainfallRiskAssessmentResponse,
    summary="Get Single Location Rainfall Risk Assessment",
    description="Retrieve detailed rainfall flood risk categorization for a specific pilot monitoring location UUID.",
)
async def get_single_location_rainfall_risk(
    location_id: str,
    service: RiskService = Depends(get_risk_service),
) -> RainfallRiskAssessmentResponse:
    try:
        assessment = await service.get_location_rainfall_risk(location_id=location_id.strip())
        if not assessment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "location_not_found",
                    "message": f"Pilot monitoring location with ID '{location_id}' not found.",
                },
            )
        return assessment
    except HTTPException:
        raise
    except SupabaseClientError as exc:
        safe_msg = sanitize_error_message(exc.message) or "Database service error during location risk lookup."
        logger.error("Supabase client error in single location risk route: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "database_error", "message": "Failed to retrieve location risk telemetry from database."},
        )
    except Exception as exc:
        logger.error("Unexpected error in single location risk route: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "risk_assessment_error", "message": "An unexpected error occurred during location risk assessment."},
        )


@router.get(
    "/thresholds",
    response_model=RainfallRiskConfig,
    summary="Get Configured Rainfall Risk Thresholds",
    description="Inspect the provisional research threshold bands, data completeness limits, and version metadata.",
)
async def get_rainfall_thresholds() -> RainfallRiskConfig:
    return get_risk_config()


@router.get(
    "/components",
    response_model=List[ComponentReadiness],
    summary="Get Hydrological Risk Components Readiness",
    description=(
        "Audit the operational readiness of the independent FloodWatch risk modules: "
        "Rainfall Risk (Operational), River-Stage Gauge Risk (Non-operational), and "
        "Combined Multi-Hazard Risk (Unavailable)."
    ),
)
async def get_risk_components_readiness(
    service: RiskService = Depends(get_risk_service),
) -> List[ComponentReadiness]:
    return service.get_component_readiness()
