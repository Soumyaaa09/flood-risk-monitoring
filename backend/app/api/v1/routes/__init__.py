from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.weather import router as weather_router

__all__ = ["health_router", "weather_router"]
