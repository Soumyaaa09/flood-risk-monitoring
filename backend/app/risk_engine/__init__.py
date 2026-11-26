"""Risk Engine package for FloodWatch.

Independent risk assessment components for:
1. Rainfall-based surface runoff risk (operational)
2. River-stage gauge flood risk (non-operational interface)
3. Combined multi-hazard inundation risk (unavailable interface)
"""

from app.risk_engine.config import RainfallRiskConfig, RainfallThresholdBands, get_risk_config
from app.risk_engine.historical_analyzer import HistoricalRainfallAnalyzer
from app.risk_engine.interfaces import (
    BaseRiskAssessor,
    CombinedRiskAssessorInterface,
    RiverStageRiskAssessorInterface,
)
from app.risk_engine.rainfall_assessor import RainfallRiskAssessor, RainfallRiskInput

__all__ = [
    "BaseRiskAssessor",
    "CombinedRiskAssessorInterface",
    "RiverStageRiskAssessorInterface",
    "RainfallRiskAssessor",
    "RainfallRiskInput",
    "RainfallRiskConfig",
    "RainfallThresholdBands",
    "HistoricalRainfallAnalyzer",
    "get_risk_config",
]
