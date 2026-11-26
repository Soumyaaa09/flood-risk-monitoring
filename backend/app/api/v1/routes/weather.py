import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.sanitizer import sanitize_error_message
from app.ingestion.open_meteo import ProviderException, ProviderTimeoutException
from app.schemas.ingestion import (
    LocationLatestWeather,
    LocationSummary,
    ObservationRecord,
    SourceHealthResponse,
)
from app.schemas.weather import WeatherPreviewResponse
from app.services.ingestion_service import IngestionService
from app.services.weather_service import WeatherService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Weather & Rainfall"])


def get_weather_service() -> WeatherService:
    return WeatherService()


def get_ingestion_service() -> IngestionService:
    return IngestionService()


@router.get(
    "/weather/locations",
    response_model=List[LocationSummary],
    summary="List Registered Pilot Weather Locations",
    description="Fetch all registered virtual weather monitoring locations for pilot districts.",
)
async def get_weather_locations(
    service: IngestionService = Depends(get_ingestion_service),
) -> List[LocationSummary]:
    try:
        return await service.get_locations()
    except Exception as exc:
        safe_err = sanitize_error_message(str(exc)) or exc.__class__.__name__
        logger.error("Error listing weather locations: %s", safe_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "query_failed", "message": "Failed to retrieve pilot monitoring locations."},
        )


@router.get(
    "/weather/latest/{location_id}",
    response_model=LocationLatestWeather,
    summary="Get Latest Weather & Active Forecasts for Location",
    description=(
        "Fetch latest current analysis (nowcast) and active 3-day forecast summary for a pilot location, "
        "including explicit data freshness state."
    ),
)
async def get_latest_weather_for_location(
    location_id: str,
    service: IngestionService = Depends(get_ingestion_service),
) -> LocationLatestWeather:
    try:
        return await service.get_latest_weather_for_location(location_id=location_id)
    except ValueError as exc:
        safe_msg = sanitize_error_message(str(exc)) or "Requested pilot location was not found."
        logger.warning("Location not found in latest weather query: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "location_not_found", "message": safe_msg},
        )
    except Exception as exc:
        safe_err = sanitize_error_message(str(exc)) or exc.__class__.__name__
        logger.error("Error retrieving latest weather for '%s': %s", location_id, safe_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "query_failed", "message": "Failed to retrieve latest weather telemetry."},
        )


@router.get(
    "/weather/observations/{location_id}",
    response_model=List[ObservationRecord],
    summary="Get Observations & Forecast Horizon for Location",
    description="Fetch chronological observations and hourly forecast records for a location.",
)
async def get_observations_for_location(
    location_id: str,
    limit: int = Query(default=72, ge=1, le=168, description="Number of hourly records to return"),
    service: IngestionService = Depends(get_ingestion_service),
) -> List[ObservationRecord]:
    try:
        return await service.get_observations_for_location(location_id=location_id, limit=limit)
    except Exception as exc:
        safe_err = sanitize_error_message(str(exc)) or exc.__class__.__name__
        logger.error("Error retrieving weather observations for '%s': %s", location_id, safe_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "query_failed", "message": "Failed to retrieve weather observation records."},
        )


@router.get(
    "/sources/health",
    response_model=SourceHealthResponse,
    summary="Data Sources & Connectivity Health",
    description="Check upstream weather provider connectivity and database service-role status.",
)
async def get_sources_health(
    service: IngestionService = Depends(get_ingestion_service),
) -> SourceHealthResponse:
    try:
        return await service.get_sources_health()
    except Exception as exc:
        safe_err = sanitize_error_message(str(exc)) or exc.__class__.__name__
        logger.error("Error evaluating source health: %s", safe_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "health_check_failed", "message": "Failed to evaluate data source health."},
        )


@router.get(
    "/weather/preview",
    response_model=WeatherPreviewResponse,
    summary="Preview Weather and Rainfall Data",
    description=(
        "Fetch real-time weather and hourly rainfall forecast from Open-Meteo for given coordinates. "
        "Coordinates are used for connectivity verification and prototype decision-support; "
        "they do NOT represent official monitoring stations."
    ),
)
async def get_weather_preview(
    latitude: float = Query(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees (-90.0 to 90.0)",
        examples=[20.316],
    ),
    longitude: float = Query(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees (-180.0 to 180.0)",
        examples=[86.611],
    ),
    forecast_days: int = Query(
        default=3,
        ge=1,
        le=7,
        description="Number of forecast days (1 to 7)",
    ),
    service: WeatherService = Depends(get_weather_service),
) -> WeatherPreviewResponse:
    try:
        return await service.get_preview(
            latitude=latitude,
            longitude=longitude,
            forecast_days=forecast_days,
        )
    except ProviderTimeoutException as exc:
        safe_msg = sanitize_error_message(exc.message) or "Upstream weather provider request timed out."
        logger.warning("Weather provider timeout: %s", safe_msg)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"error": "provider_timeout", "message": safe_msg},
        ) from exc
    except ProviderException as exc:
        safe_msg = sanitize_error_message(exc.message) or "Upstream weather provider returned an error."
        logger.warning("Weather provider error: %s", safe_msg)
        raise HTTPException(
            status_code=exc.status_code if exc.status_code in [502, 503, 504] else status.HTTP_502_BAD_GATEWAY,
            detail={"error": "provider_error", "message": safe_msg},
        ) from exc
    except Exception as exc:
        safe_err = sanitize_error_message(str(exc)) or exc.__class__.__name__
        logger.error("Unexpected error in weather preview: %s", safe_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "message": "An unexpected internal error occurred during weather preview."},
        ) from exc
