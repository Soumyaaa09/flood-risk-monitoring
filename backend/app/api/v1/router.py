from fastapi import APIRouter
from app.api.v1.routes.alerts import router as alerts_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.ingestion import router as ingestion_router
from app.api.v1.routes.monitoring import router as monitoring_router
from app.api.v1.routes.reports import router as reports_router
from app.api.v1.routes.risk import router as risk_router
from app.api.v1.routes.weather import router as weather_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(weather_router)
api_v1_router.include_router(ingestion_router)
api_v1_router.include_router(monitoring_router)
api_v1_router.include_router(risk_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(reports_router)
