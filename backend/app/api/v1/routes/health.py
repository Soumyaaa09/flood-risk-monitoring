from fastapi import APIRouter, Response, status
from app.core.config import get_settings
from app.db.session import check_database_connectivity
from app.schemas.health import HealthResponse, DatabaseHealth

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service & Database Health Check",
    description=(
        "Check the operational health of FloodWatch backend and its secure connectivity "
        "to the Supabase database without exposing sensitive credentials or keys."
    ),
)
async def get_health() -> HealthResponse:
    settings = get_settings()
    db_result = await check_database_connectivity()

    db_health = DatabaseHealth(**db_result)
    overall_status = "ok" if db_health.status == "connected" else "degraded"

    return HealthResponse(
        status=overall_status,
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        database=db_health,
    )


@router.get(
    "/health/liveness",
    summary="Process Liveness Probe",
    description=(
        "Lightweight process health probe for Kubernetes, Docker, and load balancers. "
        "Returns HTTP 200 as long as the Uvicorn application loop is responsive."
    ),
)
async def get_liveness():
    settings = get_settings()
    return {
        "status": "alive",
        "service": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
    }


@router.get(
    "/health/readiness",
    summary="Dependency Readiness Probe",
    description=(
        "Dependency readiness check for orchestrators. Returns HTTP 200 when database connectivity "
        "is verified, or HTTP 503 if downstream dependencies are unready to serve traffic."
    ),
)
async def get_readiness(response: Response):
    db_result = await check_database_connectivity()
    if db_result.get("status") == "connected":
        return {
            "status": "ready",
            "database": "connected",
            "provider": db_result.get("provider", "supabase_postgrest"),
        }
    
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "not_ready",
        "database": "disconnected",
        "detail": "Downstream database connectivity is degraded or unverified.",
    }
