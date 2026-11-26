from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, List, Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = BACKEND_DIR / ".env"

# Canonical Observation Phenomenon (valid_at) Freshness Policy Constants (minutes)
OBSERVATION_FRESHNESS_FRESH_MINUTES: float = 120.0
OBSERVATION_FRESHNESS_DELAYED_MINUTES: float = 360.0
FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS: float = 60.0


def detect_configured_workers(settings: Optional["Settings"] = None) -> int:
    """Best-effort defense-in-depth detection of multi-worker process manager flags.
    
    IMPORTANT ARCHITECTURAL NOTICE:
    Inspecting environment variables (WEB_CONCURRENCY, UVICORN_WORKERS) and sys.argv
    is a best-effort defense-in-depth safeguard. It CANNOT guarantee detection of every
    possible external process supervisor or horizontal container replica topology.
    Therefore, Settings.WORKERS is the single authoritative application worker configuration,
    and supported production launchers must pass Settings.WORKERS directly to the process manager.
    Multiple application replicas require external scheduling (e.g., cron or AWS EventBridge)
    or distributed coordination (e.g., Celery or Redis locks).
    """
    workers_detected = 1
    if settings is not None and getattr(settings, "WORKERS", None) is not None:
        try:
            workers_detected = max(workers_detected, int(settings.WORKERS))
        except (ValueError, TypeError):
            pass

    for env_key in ("WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        val = os.environ.get(env_key)
        if val is not None:
            try:
                workers_detected = max(workers_detected, int(val))
            except (ValueError, TypeError):
                pass

    # Check sys.argv for CLI worker flags (e.g., uvicorn --workers 4 or -w 4)
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg in ("--workers", "-w") and i + 1 < len(argv):
            try:
                workers_detected = max(workers_detected, int(argv[i + 1]))
            except (ValueError, TypeError):
                pass
        elif arg.startswith("--workers="):
            try:
                workers_detected = max(workers_detected, int(arg.split("=", 1)[1]))
            except (ValueError, TypeError):
                pass
        elif arg.startswith("-w="):
            try:
                workers_detected = max(workers_detected, int(arg.split("=", 1)[1]))
            except (ValueError, TypeError):
                pass

    return max(1, workers_detected)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    APP_NAME: str = "FloodWatch API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "127.0.0.1"
    ENABLE_DOCS: Optional[bool] = None

    CORS_ORIGINS: List[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # Open-Meteo Integration
    OPEN_METEO_BASE_URL: str = "https://api.open-meteo.com/v1"
    OPEN_METEO_TIMEOUT_SECONDS: float = 10.0
    OPEN_METEO_MAX_RETRIES: int = 3
    OPEN_METEO_BACKOFF_FACTOR: float = 0.5
    OPEN_METEO_MAX_WAIT_SECONDS: float = 60.0

    # Supabase / Database (No migrations executed without explicit approval)
    DATABASE_URL: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # Ingestion Security & Policy
    INGESTION_TRIGGER_TOKEN: str = "floodwatch-dev-secret-token"
    # Pipeline retrieval (fetched_at) freshness cutoffs in minutes.
    # Observation phenomenon (valid_at) freshness uses RainfallRiskConfig (120m / 360m).
    # FRESHNESS_STALE_MINUTES is retained for compatibility; ages > FRESHNESS_DELAYED_MINUTES are STALE.
    FRESHNESS_FRESH_MINUTES: int = 60
    FRESHNESS_DELAYED_MINUTES: int = 180
    FRESHNESS_STALE_MINUTES: int = 360

    # Automated Ingestion Scheduler (Phase 2.6 & Remediation SCHED-01)
    # WARNING: Single-process constraint. In multi-worker deployments, keep disabled on web workers
    # to avoid race conditions and redundant external API polling.
    ENABLE_INGESTION_SCHEDULER: bool = False
    INGESTION_SCHEDULE_INTERVAL_MINUTES: int = 60
    WORKERS: int = Field(
        default_factory=lambda: int(os.environ.get("WORKERS") or os.environ.get("WEB_CONCURRENCY") or 1),
        ge=1,
        description="Authoritative worker process count for Uvicorn/FastAPI server. Note: If > 1, ENABLE_INGESTION_SCHEDULER must be false.",
    )

    @property
    def is_production(self) -> bool:
        """Helper to determine if current environment is production."""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def docs_enabled(self) -> bool:
        """Determine whether Swagger / OpenAPI documentation is enabled."""
        if self.ENABLE_DOCS is not None:
            return self.ENABLE_DOCS
        # By default, enable docs in non-production environments
        return not self.is_production

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        """Parse comma-separated strings or JSON-encoded lists into a List[str]."""
        if isinstance(v, str):
            trimmed = v.strip()
            if trimmed.startswith("[") and trimmed.endswith("]"):
                try:
                    return json.loads(trimmed)
                except Exception:
                    pass
            return [origin.strip() for origin in trimmed.split(",") if origin.strip()]
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, v: List[str]) -> List[str]:
        """Verify CORS origins do not combine wildcard '*' with credentialed requests."""
        if "*" in v and len(v) > 1:
            raise ValueError("CORS_ORIGINS cannot contain wildcard '*' alongside specific domain origins.")
        return v

    @field_validator("WORKERS", mode="before")
    @classmethod
    def resolve_workers(cls, v: Any) -> Any:
        """Resolve authoritative worker count from settings or standard environment variables."""
        if v is not None and str(v).strip():
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
        web_conc = os.environ.get("WEB_CONCURRENCY")
        if web_conc and web_conc.strip():
            try:
                return int(web_conc)
            except (ValueError, TypeError):
                pass
        return 1

    @field_validator("INGESTION_SCHEDULE_INTERVAL_MINUTES")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("INGESTION_SCHEDULE_INTERVAL_MINUTES must be a positive integer >= 1.")
        return v

    @model_validator(mode="after")
    def validate_scheduler_worker_concurrency(self) -> "Settings":
        """Fail-safe validation (SCHED-01): Prevent scheduler activation in multi-worker configurations."""
        if self.ENABLE_INGESTION_SCHEDULER:
            configured_workers = max(self.WORKERS, detect_configured_workers(self))
            if configured_workers > 1:
                raise ValueError(
                    f"Scheduler activation rejected: ENABLE_INGESTION_SCHEDULER cannot be enabled when "
                    f"multiple worker processes are configured (authoritative WORKERS={self.WORKERS}, "
                    f"detected {configured_workers} workers). "
                    "The in-process scheduler uses an asyncio lock that cannot coordinate across separate processes. "
                    "In multi-worker or multi-replica deployments, keep ENABLE_INGESTION_SCHEDULER=false and invoke "
                    "ingestion via an external orchestrator (e.g., cron or AWS EventBridge) calling /api/v1/ingestion/trigger."
                )
        return self

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        """Strict production configuration enforcement.
        
        Ensures debug mode is off, no wildcard CORS is permitted, default dev tokens are rejected,
        and database connectivity variables are explicitly provided.
        """
        if self.is_production:
            if self.DEBUG:
                raise ValueError(
                    "Production safety violation: DEBUG must be set to False when ENVIRONMENT='production'."
                )

            if "*" in self.CORS_ORIGINS:
                raise ValueError(
                    "Production safety violation: Wildcard '*' in CORS_ORIGINS is strictly prohibited in production."
                )

            # Production token validation: reject empty, default dev token, and weak tokens (< 16 chars)
            # Never print token values in validation errors or logs.
            token = (self.INGESTION_TRIGGER_TOKEN or "").strip()
            if not token:
                raise ValueError(
                    "Production safety violation: INGESTION_TRIGGER_TOKEN must not be empty or whitespace in production."
                )

            if token == "floodwatch-dev-secret-token":
                raise ValueError(
                    "Production safety violation: Default development secret token cannot be used for "
                    "INGESTION_TRIGGER_TOKEN in production. Provide a dedicated random secret."
                )

            if len(token) < 16:
                raise ValueError(
                    "Production safety violation: INGESTION_TRIGGER_TOKEN must be at least 16 characters long in production."
                )

            supabase_url = (self.SUPABASE_URL or "").strip()
            if (
                not supabase_url
                or "your-project" in supabase_url
                or not (supabase_url.startswith("https://") or supabase_url.startswith("http://"))
            ):
                raise ValueError(
                    "Production safety violation: A valid SUPABASE_URL must be configured when ENVIRONMENT='production'."
                )

            anon_key = (self.SUPABASE_ANON_KEY or "").strip()
            if not anon_key or "your-supabase" in anon_key or len(anon_key) < 16:
                raise ValueError(
                    "Production safety violation: A valid SUPABASE_ANON_KEY must be configured when ENVIRONMENT='production'."
                )

            service_key = (self.SUPABASE_SERVICE_ROLE_KEY or "").strip()
            if not service_key or "your-supabase" in service_key or len(service_key) < 16:
                raise ValueError(
                    "Production safety violation: A valid SUPABASE_SERVICE_ROLE_KEY must be configured when ENVIRONMENT='production'."
                )

        return self


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
