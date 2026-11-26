"""Pydantic schemas for FloodWatch Historical Rainfall Reporting & Data Export (Phase 2.8).

Defines structured report schemas for:
- Standardized multi-day historical rainfall reports in Asia/Kolkata (IST).
- Daily aggregation records with explicit units and missing-data preservation.
- Antecedent rainfall, risk context, and statutory prototype disclosures.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

DEFAULT_REPORT_DISCLOSURE = (
    "FloodWatch is an experimental hydrological decision-support prototype developed for research. "
    "Daily totals are evaluated from numerical weather predictions and local grid telemetry in Asia/Kolkata (IST). "
    "This report is NOT an official measurement of the India Meteorological Department (IMD) or Central Water Commission (CWC). "
    "Consult official statutory authorities (IMD, OSDMA, CWC) for flood alerts and life-safety guidance."
)

VIRTUAL_GRID_DISCLOSURE = (
    "Virtual meteorological grid point coordinates. Does NOT represent a physical river staff gauge or streamflow sensor."
)


class DateRangeInfo(BaseModel):
    """Temporal coverage parameters for the report."""
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    window_days: int
    timezone: str = "Asia/Kolkata"
    reporting_window: str = "IST_CALENDAR_DAY"


class ReportLocationInfo(BaseModel):
    """Geospatial metadata for the reporting pilot station."""
    location_id: str
    location_name: str
    district_name: Optional[str] = None
    latitude: float
    longitude: float
    virtual_grid_disclosure: str = VIRTUAL_GRID_DISCLOSURE


class ReportDailyRecord(BaseModel):
    """Daily rainfall summary for an individual calendar day in Asia/Kolkata."""
    date: str  # YYYY-MM-DD
    rainfall_mm: Optional[float] = None
    observation_count: int = 0
    coverage_pct: float = 0.0
    is_rainy_day: bool = False  # >= 2.5 mm IMD threshold
    is_partial_day: bool = False
    reporting_window: str = "IST_CALENDAR_DAY"
    notes: Optional[str] = None


class ReportAccumulationSummary(BaseModel):
    """Overall aggregation metrics across the reporting period."""
    rainfall_24h_mm: Optional[float] = None
    rainfall_72h_mm: Optional[float] = None
    rainfall_7d_mm: Optional[float] = None
    total_window_rainfall_mm: Optional[float] = None
    rainy_days_count: int = 0
    consecutive_rainy_days: int = 0
    daily_trend: str = "INSUFFICIENT_DATA"
    window_coverage_pct: float = 0.0
    missing_intervals_count: int = 0
    has_sufficient_coverage: bool = False


class ReportAntecedentInfo(BaseModel):
    """Preceding rainfall accumulation index context."""
    category: str
    value_mm: Optional[float] = None
    confidence: str = "LOW"
    explanation: str = ""
    caveat: str = (
        "Empirical antecedent precipitation depth index. "
        "Does NOT measure physical soil saturation, percolation rate, or water table height."
    )


class ReportRiskSummary(BaseModel):
    """Current evaluated surface water runoff potential at report generation time."""
    risk_category: str
    confidence: str
    recent_observed_mm: Optional[float] = None
    forecast_24h_mm: Optional[float] = None
    limitations_disclaimer: str = (
        "Evaluates atmospheric precipitation runoff potential only. "
        "Does NOT predict riverine stage overflow or levee breaches."
    )


class RainfallReportResponse(BaseModel):
    """Complete historical rainfall report payload for API, print, and PDF generation."""
    report_id: str
    generated_at_utc: datetime
    generated_at_ist: str
    location: ReportLocationInfo
    date_range: DateRangeInfo
    summary: ReportAccumulationSummary
    daily_records: List[ReportDailyRecord]
    antecedent_indicator: Optional[ReportAntecedentInfo] = None
    risk_summary: Optional[ReportRiskSummary] = None
    data_quality_warnings: List[str] = Field(default_factory=list)
    methodology_and_disclosures: Dict[str, str] = Field(
        default_factory=lambda: {
            "statutory_disclaimer": DEFAULT_REPORT_DISCLOSURE,
            "virtual_grid_disclosure": VIRTUAL_GRID_DISCLOSURE,
            "rainy_day_threshold": "2.5 mm depth applied to Asia/Kolkata calendar day window (00:00 - 24:00 IST)",
            "data_source": "Open-Meteo numerical weather forecasts and hourly atmospheric analysis models",
        }
    )
