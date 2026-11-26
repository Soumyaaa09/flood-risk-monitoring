from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.services.ingestion_scheduler import get_ingestion_scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    from app.core.http_clients import close_http_clients, init_http_clients

    scheduler = None
    scheduler_started = False
    try:
        init_http_clients()
        scheduler = get_ingestion_scheduler()
        scheduler.start()
        scheduler_started = True
        yield
    finally:
        # Shutdown actions: Graceful termination of scheduler tasks and HTTPX connection pools
        try:
            if scheduler is not None and scheduler_started:
                await scheduler.stop()
        finally:
            await close_http_clients()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "FloodWatch Decision-Support & Flood-Risk Monitoring API (Phase 1 Foundation).\n\n"
        "**Pilot Districts:** Jagatsinghpur (Paradeep area), Cuttack, Kendrapara.\n\n"
        "**DISCLAIMER:** This application is a research and decision-support prototype, "
        "NOT an official emergency-warning service. Do not rely on it for life-safety warnings."
    ),
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# CORS Middleware: Disallow credentials if wildcard origin is configured
has_wildcard_cors = "*" in settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=not has_wildcard_cors,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(api_v1_router, prefix="/api")


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "docs": "/docs" if settings.docs_enabled else "disabled in production",
        "health": "/api/v1/health",
        "prototype_notice": (
            "FloodWatch is a research and decision-support prototype, "
            "NOT an official emergency-warning service."
        ),
    }


def run_app() -> None:
    """Authoritative production launcher for FloodWatch FastAPI backend.
    
    Passes validated configuration (including the authoritative worker count) directly
    to the Uvicorn process manager to ensure consistency between application settings
    and multi-worker execution.
    """
    import uvicorn
    app_settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=app_settings.HOST,
        port=app_settings.PORT,
        workers=app_settings.WORKERS,
        log_level="debug" if app_settings.DEBUG else "info",
    )


if __name__ == "__main__":
    run_app()
