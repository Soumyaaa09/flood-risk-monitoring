from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

from app.core.sanitizer import sanitize_error_message


class IngestionStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class FreshnessState(str, Enum):
    FRESH = "fresh"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class IngestionLocationResult(BaseModel):
    """Result of ingestion for an individual monitoring location."""
    location_id: str
    location_name: str
    district_name: Optional[str] = None
    status: IngestionStatus
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    error_message: Optional[str] = None
    data_freshness_timestamp: Optional[datetime] = None


class IngestionTriggerResponse(BaseModel):
    """Summary response returned when an ingestion run is triggered."""
    status: str
    trigger_timestamp: datetime
    total_locations: int
    successful_locations: int
    failed_locations: int
    total_records_fetched: int
    total_records_inserted: int
    total_records_updated: int
    results: List[IngestionLocationResult] = Field(default_factory=list)


class IngestionRunResponse(BaseModel):
    """Database representation of an ingestion run."""
    id: str
    data_source_id: str
    location_id: Optional[str] = None
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    data_freshness_timestamp: Optional[datetime] = None
    idempotency_key: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("error_message")
    @classmethod
    def sanitize_err_msg(cls, v: Optional[str]) -> Optional[str]:
        return sanitize_error_message(v)


class LocationSummary(BaseModel):
    """Registered monitoring location with district and coordinates."""
    id: str
    district_id: str
    district_name: Optional[str] = None
    basin_id: Optional[str] = None
    name: str
    location_type: str
    latitude: float
    longitude: float
    elevation_m: Optional[float] = None
    is_official_gauge: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ObservationRecord(BaseModel):
    """Standardized observation or forecast record."""
    id: Optional[str] = None
    location_id: str
    data_source_id: str
    ingestion_run_id: Optional[str] = None
    valid_at: datetime
    fetched_at: datetime
    forecast_reference_time: Optional[datetime] = None
    observation_type: str
    measurement_type: str = "rainfall"
    value: float
    unit: str = "mm"
    quality_flag: str = "unverified"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LocationLatestWeather(BaseModel):
    """Latest current analysis and active forecast summary for a location."""
    location_id: str
    location_name: str
    district_name: Optional[str] = None
    latitude: float
    longitude: float
    freshness: FreshnessState
    data_freshness_timestamp: Optional[datetime] = None
    current_analysis: Optional[ObservationRecord] = None
    active_forecast_count: int = 0
    forecast_horizon_hours: int = 0
    next_24h_precipitation_mm: Optional[float] = None
    prototype_notice: str = (
        "FloodWatch is a research and decision-support prototype, "
        "NOT an official emergency-warning service."
    )


class SourceHealthResponse(BaseModel):
    """Health check response for upstream and database sources."""
    status: str
    provider: str
    connectivity: str
    timestamp: datetime
    service_role_configured: bool
    details: Dict[str, Any] = Field(default_factory=dict)
