from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.ingestion import FreshnessState, IngestionRunResponse


class QualitySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class QualityStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class DataQualityIssue(BaseModel):
    """Specific data quality validation failure or alert."""
    severity: QualitySeverity
    code: str
    message: str
    field: Optional[str] = None
    value: Optional[Any] = None
    timestamp: Optional[datetime] = None


class LocationQualityHealth(BaseModel):
    """Location-level data freshness, observation values, and quality assessment."""
    location_id: str
    location_name: str
    district_name: Optional[str] = None
    latitude: float
    longitude: float

    # Dual Freshness Evaluation:
    # 1. Observation freshness: physical valid_at timestamp (when rain fell/was measured)
    observation_freshness: FreshnessState = FreshnessState.UNAVAILABLE
    observation_timestamp: Optional[datetime] = None
    observation_age_minutes: Optional[float] = None

    # 2. Ingestion freshness: provider retrieval fetched_at timestamp (when pipeline ran)
    ingestion_freshness: FreshnessState = FreshnessState.UNAVAILABLE
    ingestion_timestamp: Optional[datetime] = None
    ingestion_age_minutes: Optional[float] = None

    # Backward compatibility: 'freshness' strictly mirrors observation_freshness
    freshness: FreshnessState = FreshnessState.UNAVAILABLE
    data_freshness_timestamp: Optional[datetime] = None
    age_minutes: Optional[float] = None

    current_rainfall_mm: Optional[float] = None
    active_forecast_hours: int = 0
    next_24h_precipitation_mm: Optional[float] = None
    quality_status: QualityStatus = QualityStatus.PASS
    quality_issues: List[DataQualityIssue] = Field(default_factory=list)
    completeness_percentage: float = 100.0


class FreshnessSummary(BaseModel):
    """Summary of data freshness states across all pilot locations."""
    # Physical observation freshness counts
    fresh_count: int = 0
    delayed_count: int = 0
    stale_count: int = 0
    unavailable_count: int = 0
    overall_state: FreshnessState = FreshnessState.UNAVAILABLE

    # Ingestion pipeline retrieval freshness counts
    ingestion_fresh_count: int = 0
    ingestion_delayed_count: int = 0
    ingestion_stale_count: int = 0
    ingestion_unavailable_count: int = 0
    ingestion_overall_state: FreshnessState = FreshnessState.UNAVAILABLE


class IngestionHealthSummary(BaseModel):
    """Pipeline-wide ingestion execution and timeliness metrics."""
    latest_attempt_at: Optional[datetime] = None
    latest_attempt_status: Optional[str] = None
    latest_success_at: Optional[datetime] = None
    current_run_status: str = "unknown"
    recent_runs_count: int = 0
    successful_runs_count: int = 0
    failed_runs_count: int = 0
    stale_started_runs_count: int = 0
    average_duration_seconds: Optional[float] = None
    total_records_ingested_recent: int = 0


class SchedulerStatusResponse(BaseModel):
    """Operational status and execution history of the background weather ingestion scheduler."""
    enabled: bool
    interval_minutes: int
    state: str  # "running", "idle", "stopped", "disabled"
    is_cycle_in_progress: bool = False
    last_cycle_start: Optional[datetime] = None
    last_cycle_completed: Optional[datetime] = None
    last_cycle_outcome: Optional[str] = None  # "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "SKIPPED", None
    last_cycle_duration_seconds: Optional[float] = None
    next_expected_cycle: Optional[datetime] = None
    total_cycles_executed: int = 0
    location_outcomes: List[Dict[str, Any]] = Field(default_factory=list)
    single_process_limitation_notice: str = (
        "In-process scheduler operates strictly within a single application process. "
        "Multi-worker (e.g. uvicorn --workers > 1) or distributed container deployments require "
        "an external cron runner or distributed lock to prevent overlapping ingestion."
    )


class MonitoringSummaryResponse(BaseModel):
    """Top-level monitoring overview for dashboard and health alerts."""
    system_health: str = "healthy"  # healthy, degraded, critical
    freshness_summary: FreshnessSummary
    ingestion_summary: IngestionHealthSummary
    location_healths: List[LocationQualityHealth] = Field(default_factory=list)
    scheduler: Optional[SchedulerStatusResponse] = None
    prototype_notice: str = (
        "FloodWatch is a research and decision-support prototype, "
        "NOT an official emergency-warning service."
    )
    timestamp: datetime


class MonitoringLocationsResponse(BaseModel):
    """Detailed location-wise monitoring metrics and quality findings."""
    total_locations: int
    locations: List[LocationQualityHealth] = Field(default_factory=list)
    timestamp: datetime


class MonitoringIngestionHealthResponse(BaseModel):
    """Operational monitoring of the ingestion pipeline and data source connectivity."""
    status: str
    provider: str
    connectivity: str
    service_role_configured: bool
    recent_runs: List[IngestionRunResponse] = Field(default_factory=list)
    stale_runs: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime
    details: Dict[str, Any] = Field(default_factory=dict)
