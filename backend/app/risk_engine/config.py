"""Rainfall Flood Risk Engine Thresholds and Configuration.

PROVISIONAL RESEARCH PARAMETERS:
Thresholds in this module are experimental research parameters adapted from meteorological
precipitation accumulation scales. They do NOT constitute official flood-warning criteria
from the India Meteorological Department (IMD), Central Water Commission (CWC),
or Odisha State Disaster Management Authority (OSDMA).

Changes to thresholds are segregated here from core assessment and application logic.
"""

from typing import Dict
from pydantic import BaseModel, Field

from app.core.config import (
    OBSERVATION_FRESHNESS_DELAYED_MINUTES,
    OBSERVATION_FRESHNESS_FRESH_MINUTES,
)


class RainfallThresholdBands(BaseModel):
    """Provisional precipitation thresholds for surface flood-risk categorization.
    
    Categorization evaluates:
    1. 24-hour accumulated forecast precipitation (mm)
    2. 6-hour accumulated forecast precipitation (mm)
    3. Recent observed rainfall rate / nowcast (mm)
    """

    # VERY_HIGH: Corresponds to Very Heavy to Extremely Heavy rainfall bands
    very_high_24h_mm: float = 115.6
    very_high_6h_mm: float = 45.0
    very_high_recent_mm: float = 35.0

    # HIGH: Corresponds to Heavy rainfall accumulation band
    high_24h_mm: float = 64.5
    high_6h_mm: float = 25.0
    high_recent_mm: float = 15.0

    # MODERATE: Corresponds to Moderate rainfall accumulation band
    moderate_24h_mm: float = 15.6
    moderate_6h_mm: float = 10.0
    moderate_recent_mm: float = 5.0


class RainfallRiskConfig(BaseModel):
    """Configuration structure for the rainfall-based flood-risk assessment engine."""

    version: str = "v0.1-provisional-research"
    name: str = "Mahanadi Delta Pilot Rainfall Surface Water Risk Parameters"
    bands: RainfallThresholdBands = Field(default_factory=RainfallThresholdBands)

    # Data Reliability Minimums
    min_forecast_completeness_pct: float = 50.0
    min_forecast_hours: int = 6
    stale_observation_threshold_minutes: float = OBSERVATION_FRESHNESS_DELAYED_MINUTES
    delayed_observation_threshold_minutes: float = OBSERVATION_FRESHNESS_FRESH_MINUTES

    disclaimer: str = (
        "PROVISIONAL RESEARCH PARAMETERS: FloodWatch rainfall risk categories are experimental "
        "surface-water indicators based on atmospheric precipitation forecasts and nowcasts. "
        "They do NOT represent official disaster or flood warnings issued by the India Meteorological "
        "Department (IMD), Central Water Commission (CWC), or Odisha State Disaster Management Authority "
        "(OSDMA). Atmospheric rainfall alone does not confirm riverine flooding or inundation."
    )

    def to_dict(self) -> Dict:
        return self.model_dump()


# Default singleton configuration for the FloodWatch prototype
DEFAULT_RISK_CONFIG = RainfallRiskConfig()


def get_risk_config() -> RainfallRiskConfig:
    """Return the active rainfall risk assessment configuration."""
    return DEFAULT_RISK_CONFIG
