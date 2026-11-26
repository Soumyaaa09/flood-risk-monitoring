"""Modular Risk Engine Component Interfaces for FloodWatch.

Strict Architecture Principles:
1. Rainfall-based risk, river-stage gauge risk, and discharge monitoring are INDEPENDENT components.
2. DO NOT combine rainfall, water level, and discharge into one unified numerical index or collapsed scale.
3. River-stage risk is declared but marked NON-OPERATIONAL until genuine hydrological gauge observations exist.
4. Combined multi-hazard assessment is UNAVAILABLE until physical stage and hydrodynamic models exist.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from app.schemas.risk import (
    ComponentReadiness,
    HydrologicalComponentStatus,
    RainfallRiskAssessmentResponse,
    RiskCategory,
    RiskConfidence,
)


class BaseRiskAssessor(ABC):
    """Abstract base class for FloodWatch hazard risk assessment modules."""

    @property
    @abstractmethod
    def component_name(self) -> str:
        """Human-readable component identifier."""
        pass

    @property
    @abstractmethod
    def is_operational(self) -> bool:
        """Whether this risk component currently accepts live observations."""
        pass

    @abstractmethod
    def get_readiness(self) -> ComponentReadiness:
        """Return the component operational readiness status."""
        pass


class RiverStageRiskAssessorInterface(BaseRiskAssessor):
    """Interface for river-stage gauge risk assessment.
    
    NON-OPERATIONAL: Currently in stub/contract state until genuine physical river
    gauge telemetry (e.g. from Central Water Commission or Odisha DoWR) is integrated.
    """

    @property
    def component_name(self) -> str:
        return "River-Stage Gauge Risk Assessor"

    @property
    def is_operational(self) -> bool:
        return False

    def get_readiness(self) -> ComponentReadiness:
        return ComponentReadiness(
            component_name=self.component_name,
            status=HydrologicalComponentStatus.NON_OPERATIONAL,
            description="Evaluates river embankment overtopping and gauge datum exceedances.",
            reason=(
                "Non-operational: Physical river gauge telemetry (m datum-referenced) is not yet integrated. "
                "Only virtual atmospheric rainfall monitoring points are currently active."
            ),
        )

    def assess_stage_risk(self, location_id: str, water_level_m: Optional[float]) -> Dict[str, Any]:
        """Refuse operational stage evaluation and return safe UNAVAILABLE classification."""
        return {
            "component": self.component_name,
            "status": HydrologicalComponentStatus.NON_OPERATIONAL,
            "risk_category": RiskCategory.UNAVAILABLE,
            "confidence": RiskConfidence.NONE,
            "explanation": (
                "River-stage risk assessment is non-operational. FloodWatch does not yet ingest "
                "live physical river gauge observations."
            ),
            "timestamp": datetime.now(timezone.utc),
        }


class CombinedRiskAssessorInterface(BaseRiskAssessor):
    """Interface for combined multi-hazard flood assessment.
    
    UNAVAILABLE: FloodWatch strictly prohibits collapsing rainfall depth (mm),
    river stage height (m), and volumetric discharge (m³/s) into a single scalar score.
    """

    @property
    def component_name(self) -> str:
        return "Combined Multi-Hazard Flood Risk Assessor"

    @property
    def is_operational(self) -> bool:
        return False

    def get_readiness(self) -> ComponentReadiness:
        return ComponentReadiness(
            component_name=self.component_name,
            status=HydrologicalComponentStatus.UNAVAILABLE,
            description="Multi-factor inundation assessment combining river stage, rainfall, and surge.",
            reason=(
                "Unavailable: Hydrodynamic delta modeling and river stage telemetry required. "
                "Rainfall depth and river gauge elevation cannot be collapsed into a single numerical score."
            ),
        )

    def assess_combined_risk(
        self,
        rainfall_risk: RiskCategory,
        river_stage_risk: Optional[RiskCategory] = None,
        surge_risk: Optional[RiskCategory] = None,
    ) -> Dict[str, Any]:
        """Refuse unified collapse and return UNAVAILABLE."""
        return {
            "component": self.component_name,
            "status": HydrologicalComponentStatus.UNAVAILABLE,
            "risk_category": RiskCategory.UNAVAILABLE,
            "confidence": RiskConfidence.NONE,
            "explanation": (
                "Combined multi-hazard risk assessment is unavailable. FloodWatch maintains strict segregation "
                "between atmospheric rainfall accumulation and river gauge stages. Unified numerical index "
                "combination is physically invalid without calibrated 2D hydrodynamic delta modeling."
            ),
            "timestamp": datetime.now(timezone.utc),
        }
