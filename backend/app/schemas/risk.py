"""Pydantic schemas for the FloodWatch Risk Assessment Engine.

Supports independent assessment components:
- Rainfall-based surface runoff risk
- River-stage gauge risk (non-operational interface)
- Combined multi-hazard risk (unavailable interface)
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, PrivateAttr

from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualityStatus


class RiskCategory(str, Enum):
    """Categorical flood risk levels for FloodWatch."""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"
    UNAVAILABLE = "UNAVAILABLE"


class RiskConfidence(str, Enum):
    """Assessment confidence derived from data freshness, completeness, and sensor health."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class RainfallTrend(str, Enum):
    """Directional trend of observed daily rainfall totals."""
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    STEADY = "STEADY"
    NO_RAIN = "NO_RAIN"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AntecedentCategory(str, Enum):
    """Experimental antecedent precipitation accumulation category."""
    LOW_ANTECEDENT = "LOW_ANTECEDENT"
    MODERATE_ANTECEDENT = "MODERATE_ANTECEDENT"
    HIGH_ANTECEDENT = "HIGH_ANTECEDENT"
    EXTREME_ANTECEDENT = "EXTREME_ANTECEDENT"
    UNAVAILABLE = "UNAVAILABLE"


class DailyReportingWindow(str, Enum):
    """Daily rainfall reporting window convention."""
    IST_CALENDAR_DAY = "IST_CALENDAR_DAY"  # 00:00 to 24:00 IST (18:30 UTC D-1 to 18:30 UTC D)
    IMD_SYNOPTIC_DAY = "IMD_SYNOPTIC_DAY"  # 08:30 to 08:30 IST next day (03:00 UTC D to 03:00 UTC D+1)


class HydrologicalComponentStatus(str, Enum):
    """Operational status of independent risk engine components."""
    OPERATIONAL = "OPERATIONAL"
    NON_OPERATIONAL = "NON_OPERATIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class DailyRainfallRecord(BaseModel):
    """Historical daily rainfall aggregation in local reporting window.
    
    Rainfall total is Optional[float] to strictly distinguish genuine 0.0 mm dry days
    (with confirmed observations) from missing/unobserved intervals (None).
    """
    date: str  # YYYY-MM-DD representing local window date
    start_time_utc: Optional[datetime] = None
    end_time_utc: Optional[datetime] = None
    rainfall_mm: Optional[float] = None
    observation_count: int
    is_rainy_day: bool  # >= 2.5 mm according to IMD-consistent threshold
    coverage_pct: float
    is_partial_day: bool = False
    reporting_window: str = DailyReportingWindow.IST_CALENDAR_DAY.value


class HistoricalRainfallContext(BaseModel):
    """Historical rainfall accumulations, trends, and data coverage indicators."""
    reference_time: datetime
    window_days: int
    timezone: str = "Asia/Kolkata"
    reporting_window: DailyReportingWindow = DailyReportingWindow.IST_CALENDAR_DAY
    detected_interval_minutes: Optional[float] = None
    accumulation_type: str = "discrete_sampled_depth"
    rainfall_24h_mm: Optional[float] = None
    rainfall_72h_mm: Optional[float] = None
    rainfall_7d_mm: Optional[float] = None
    daily_totals: List[DailyRainfallRecord] = Field(default_factory=list)
    daily_trend: RainfallTrend = RainfallTrend.INSUFFICIENT_DATA
    consecutive_rainy_days: int = 0
    current_day_in_progress: bool = False
    coverage_pct: float = 0.0
    missing_intervals_count: int = 0
    data_gap_warning: Optional[str] = None
    has_sufficient_coverage: bool = False
    terminology_disclaimer: str = (
        "Daily rainfall aggregations are evaluated in Indian Standard Time (Asia/Kolkata, UTC+05:30). "
        "A rainy day is defined using the IMD-consistent 2.5 mm threshold applied to local telemetry, "
        "and does not represent an official IMD station observation."
    )


class AntecedentRainfallIndicator(BaseModel):
    """Experimental Antecedent Rainfall Indicator reflecting preceding surface water accumulation."""
    indicator_category: AntecedentCategory
    indicator_value_mm: Optional[float] = None
    underlying_24h_mm: Optional[float] = None
    underlying_72h_mm: Optional[float] = None
    underlying_7d_mm: Optional[float] = None
    coverage_pct: float
    missing_intervals_count: int
    confidence: RiskConfidence
    explanation: str
    methodology: str = (
        "Multi-window antecedent precipitation index (weighted accumulation: "
        "0.5 * 24h + 0.3 * 72h + 0.2 * 7d observed rainfall)."
    )
    caveat: str = (
        "EXPERIMENTAL ANTECEDENT INDICATOR: Represents accumulated precipitation depth over preceding periods. "
        "Does NOT measure physical soil moisture, infiltration rate, or groundwater elevation."
    )


class RiskEvidenceSegment(BaseModel):
    """Segmented evidence component for transparent risk justification."""
    title: str
    summary: str
    status: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    caveat: Optional[str] = None


class RiskContextExplanation(BaseModel):
    """Comprehensive, public-friendly explanation of rainfall risk assessment."""
    public_summary: str
    contributing_factors: List[str] = Field(default_factory=list)
    impact_advisory: str
    observed_context: RiskEvidenceSegment
    historical_context: RiskEvidenceSegment
    forecast_context: RiskEvidenceSegment
    antecedent_context: RiskEvidenceSegment
    data_quality_context: RiskEvidenceSegment
    limitations_disclaimer: str = (
        "RESEARCH PROTOTYPE: Evaluates numerical weather predictions and local rainfall telemetry only. "
        "Does NOT predict river overflow, embankment breaches, inundation depth, or affected structures. "
        "Official warnings are issued exclusively by IMD and OSDMA."
    )


class RainfallRiskAssessmentResponse(BaseModel):
    """Location-level rainfall-based surface flood risk assessment result."""

    location_id: str
    location_name: str
    district_name: Optional[str] = None
    latitude: float
    longitude: float
    assessment_timestamp: datetime

    # Risk Assessment
    risk_category: RiskCategory
    confidence: RiskConfidence
    assessment_explanation: str

    # Validated Inputs
    recent_observed_rainfall_mm: Optional[float] = None
    forecast_6h_precipitation_mm: Optional[float] = None
    forecast_24h_precipitation_mm: Optional[float] = None

    # Telemetry and Quality Context
    observation_freshness: FreshnessState
    observation_age_minutes: Optional[float] = None
    observation_timestamp: Optional[datetime] = None
    forecast_completeness_pct: float
    active_forecast_hours: int
    quality_status: QualityStatus
    quality_warnings: List[str] = Field(default_factory=list)

    # Historical Context & Antecedent Indicators (Phase 2.4.2)
    historical_context: Optional[HistoricalRainfallContext] = None
    antecedent_indicator: Optional[AntecedentRainfallIndicator] = None

    # Location-Based Context and Plain-Language Explanation (Phase 2.4.4)
    risk_context: Optional[RiskContextExplanation] = None

    # Provenance and Metadata
    threshold_version: str
    component_status: HydrologicalComponentStatus = HydrologicalComponentStatus.OPERATIONAL
    prototype_disclaimer: str

    # Internal threshold drivers retained across context rebuilds without altering API schema
    _threshold_drivers: List[str] = PrivateAttr(default_factory=list)


class HistoricalRainfallResponse(BaseModel):
    """Detailed historical rainfall analytics and antecedent indicators for a single location."""
    location_id: str
    location_name: str
    district_name: Optional[str] = None
    latitude: float
    longitude: float
    assessment_timestamp: datetime
    history: HistoricalRainfallContext
    antecedent: AntecedentRainfallIndicator
    disclaimer: str


class HistoricalRainfallSummaryResponse(BaseModel):
    """Pipeline-wide historical rainfall analytics across all pilot locations."""
    total_locations: int
    window_days: int
    assessment_timestamp: datetime
    locations: List[HistoricalRainfallResponse] = Field(default_factory=list)
    disclaimer: str


class ComponentReadiness(BaseModel):
    """Status description for an individual hydrological risk component."""
    component_name: str
    status: HydrologicalComponentStatus
    description: str
    reason: str


class RainfallRiskSummaryResponse(BaseModel):
    """Pipeline-wide rainfall risk overview across all pilot monitoring locations."""

    total_locations: int
    highest_risk_category: RiskCategory
    category_counts: Dict[str, int]
    threshold_version: str
    assessment_timestamp: datetime
    assessments: List[RainfallRiskAssessmentResponse] = Field(default_factory=list)
    component_readiness: List[ComponentReadiness] = Field(default_factory=list)
    prototype_disclaimer: str
