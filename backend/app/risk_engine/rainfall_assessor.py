"""Rainfall-Based Flood Risk Assessment Engine for FloodWatch.

Independent risk assessment component evaluating atmospheric precipitation observations
and forward-looking numerical weather forecasts against configurable research threshold bands.

Strict Safety and Hydrological Guarantees:
1. Rejects negative rainfall values and physics violations.
2. Preserves genuine 0.0 mm rainfall as LOW risk (with appropriate confidence).
3. Never converts missing or null values into zero.
4. Refuses confident classification when observation is stale (>360m) or unavailable.
5. Flags incomplete forecast horizons (<50% completeness).
6. Segregates rainfall accumulation from river stage or discharge metrics.
7. Explains whether risk is driven by recent observed intensity, 6h forecast, or 24h forecast.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple

from app.risk_engine.config import RainfallRiskConfig, get_risk_config
from app.risk_engine.explanation_engine import RiskExplanationEngine
from app.risk_engine.interfaces import BaseRiskAssessor
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualitySeverity, QualityStatus
from app.schemas.risk import (
    ComponentReadiness,
    HydrologicalComponentStatus,
    RainfallRiskAssessmentResponse,
    RiskCategory,
    RiskConfidence,
)


@dataclass
class RainfallRiskInput:
    """Consolidated input structure for rainfall risk assessment."""
    location_id: str
    location_name: str
    district_name: Optional[str]
    latitude: float
    longitude: float
    recent_observed_rainfall_mm: Optional[float]
    forecast_6h_precipitation_mm: Optional[float]
    forecast_24h_precipitation_mm: Optional[float]
    observation_freshness: FreshnessState
    observation_age_minutes: Optional[float]
    observation_timestamp: Optional[datetime]
    forecast_completeness_pct: float
    active_forecast_hours: int
    quality_status: QualityStatus
    quality_warnings: List[str]


class RainfallRiskAssessor(BaseRiskAssessor):
    """Operational rainfall-based flood-risk assessment engine."""

    def __init__(self, config: Optional[RainfallRiskConfig] = None):
        self.config = config or get_risk_config()

    @property
    def component_name(self) -> str:
        return "Rainfall Surface Water Risk Assessor"

    @property
    def is_operational(self) -> bool:
        return True

    def get_readiness(self) -> ComponentReadiness:
        return ComponentReadiness(
            component_name=self.component_name,
            status=HydrologicalComponentStatus.OPERATIONAL,
            description="Evaluates surface water runoff potential from recent rainfall and 24h forecast horizons.",
            reason="Operational with Open-Meteo numerical weather forecasts and pilot station nowcasts.",
        )

    def assess_location_risk(
        self,
        risk_input: RainfallRiskInput,
        assessment_time: Optional[datetime] = None,
    ) -> RainfallRiskAssessmentResponse:
        """Execute risk categorization for an individual monitoring location."""
        ts = assessment_time or datetime.now(timezone.utc)
        bands = self.config.bands

        # Collect warning annotations
        warnings: List[str] = list(risk_input.quality_warnings)

        # ---------------------------------------------------------------------
        # 1. Physics Violations & Invalid Value Guard
        # ---------------------------------------------------------------------
        obs_val = risk_input.recent_observed_rainfall_mm
        f24_val = risk_input.forecast_24h_precipitation_mm
        f6_val = risk_input.forecast_6h_precipitation_mm

        for name, val in [("Observed rainfall", obs_val), ("24h forecast", f24_val), ("6h forecast", f6_val)]:
            if val is not None:
                if math.isnan(val) or math.isinf(val):
                    return self._build_unavailable_response(
                        risk_input,
                        ts,
                        explanation=f"Assessment rejected: {name} contains NaN or infinite values.",
                        warnings=warnings,
                    )
                if val < 0.0:
                    return self._build_unavailable_response(
                        risk_input,
                        ts,
                        explanation=f"Assessment rejected: {name} is negative ({val} mm), violating physics.",
                        warnings=warnings,
                    )

        # ---------------------------------------------------------------------
        # 2. Complete Missing Data Guard (No Fake Zeroes)
        # ---------------------------------------------------------------------
        if obs_val is None and f24_val is None:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    "Risk UNAVAILABLE: Both recent observed rainfall and 24h forecast precipitation are missing. "
                    "FloodWatch never converts missing data into zero rainfall."
                ),
                warnings=warnings,
            )

        # ---------------------------------------------------------------------
        # 3. Observation Freshness & Staleness Guard
        # ---------------------------------------------------------------------
        if risk_input.observation_freshness == FreshnessState.UNAVAILABLE:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    "Risk UNAVAILABLE: Rainfall observation timestamp is unavailable or exceeds acceptable clock skew. "
                    "Unreliable telemetry prevents safe flood-risk estimation."
                ),
                warnings=warnings,
            )

        if risk_input.observation_freshness == FreshnessState.STALE:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    f"Risk UNAVAILABLE: Observation is STALE ({risk_input.observation_age_minutes or '>360'}m old). "
                    f"FloodWatch refuses confident risk classification on obsolete telemetry."
                ),
                warnings=warnings,
            )

        # ---------------------------------------------------------------------
        # 4. Incomplete Forecast Horizon Guard
        # ---------------------------------------------------------------------
        if risk_input.active_forecast_hours < self.config.min_forecast_hours:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    f"Risk UNAVAILABLE: Active forecast horizon is only {risk_input.active_forecast_hours}h "
                    f"(minimum {self.config.min_forecast_hours}h required)."
                ),
                warnings=warnings,
            )

        if risk_input.forecast_completeness_pct < self.config.min_forecast_completeness_pct:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    f"Risk UNAVAILABLE: Forecast completeness is {risk_input.forecast_completeness_pct:.1f}% "
                    f"(minimum {self.config.min_forecast_completeness_pct}% required). Excessive missing intervals."
                ),
                warnings=warnings,
            )

        # ---------------------------------------------------------------------
        # 4b. Required Observed Telemetry & Data Quality Guard (MED-03)
        # ---------------------------------------------------------------------
        if obs_val is None:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    "Risk UNAVAILABLE: Recent observed rainfall telemetry is missing or invalid. "
                    "FloodWatch never converts missing observations into zero or classifies risk as LOW without valid observed data."
                ),
                warnings=warnings,
            )

        if risk_input.quality_status == QualityStatus.FAIL:
            return self._build_unavailable_response(
                risk_input,
                ts,
                explanation=(
                    "Risk UNAVAILABLE: Input telemetry failed data quality validation (QualityStatus.FAIL). "
                    "FloodWatch refuses risk classification on failed observation or forecast quality."
                ),
                warnings=warnings,
            )

        # ---------------------------------------------------------------------
        # 5. Determine Confidence Level
        # ---------------------------------------------------------------------
        confidence = RiskConfidence.HIGH

        if risk_input.observation_freshness == FreshnessState.DELAYED:
            confidence = RiskConfidence.LOW
            warnings.append(
                f"Observation is DELAYED ({risk_input.observation_age_minutes:.1f}m old). "
                f"Assessment carries reduced confidence."
            )

        if risk_input.forecast_completeness_pct < 85.0:
            if confidence == RiskConfidence.HIGH:
                confidence = RiskConfidence.MEDIUM
            warnings.append(
                f"Forecast horizon completeness is {risk_input.forecast_completeness_pct:.1f}%. "
                f"Some forecast steps were interpolated or missing."
            )

        if risk_input.quality_status == QualityStatus.WARNING:
            if confidence == RiskConfidence.HIGH:
                confidence = RiskConfidence.MEDIUM

        # ---------------------------------------------------------------------
        # 6. Evaluate Categorical Risk Against Provisional Thresholds
        # ---------------------------------------------------------------------
        category, drivers = self._evaluate_thresholds(obs_val, f6_val, f24_val, bands)

        # Construct contextual explanation
        explanation = self._build_explanation(category, obs_val, f6_val, f24_val, drivers, confidence)
        risk_context = RiskExplanationEngine.generate_explanation(
            category=category,
            confidence=confidence,
            location_name=risk_input.location_name,
            district_name=risk_input.district_name,
            recent_observed_mm=obs_val,
            forecast_6h_mm=f6_val,
            forecast_24h_mm=f24_val,
            observation_freshness=risk_input.observation_freshness,
            observation_age_minutes=risk_input.observation_age_minutes,
            forecast_completeness_pct=risk_input.forecast_completeness_pct,
            active_forecast_hours=risk_input.active_forecast_hours,
            quality_status=risk_input.quality_status,
            quality_warnings=warnings,
            drivers=drivers,
        )

        response = RainfallRiskAssessmentResponse(
            location_id=risk_input.location_id,
            location_name=risk_input.location_name,
            district_name=risk_input.district_name,
            latitude=risk_input.latitude,
            longitude=risk_input.longitude,
            assessment_timestamp=ts,
            risk_category=category,
            confidence=confidence,
            assessment_explanation=explanation,
            recent_observed_rainfall_mm=obs_val,
            forecast_6h_precipitation_mm=f6_val,
            forecast_24h_precipitation_mm=f24_val,
            observation_freshness=risk_input.observation_freshness,
            observation_age_minutes=risk_input.observation_age_minutes,
            observation_timestamp=risk_input.observation_timestamp,
            forecast_completeness_pct=risk_input.forecast_completeness_pct,
            active_forecast_hours=risk_input.active_forecast_hours,
            quality_status=risk_input.quality_status,
            quality_warnings=warnings,
            risk_context=risk_context,
            threshold_version=self.config.version,
            component_status=HydrologicalComponentStatus.OPERATIONAL,
            prototype_disclaimer=self.config.disclaimer,
        )
        response._threshold_drivers = list(dict.fromkeys(drivers))
        return response

    def _evaluate_thresholds(
        self,
        obs_val: Optional[float],
        f6_val: Optional[float],
        f24_val: Optional[float],
        bands: Any,
    ) -> Tuple[RiskCategory, List[str]]:
        """Compare available rainfall quantities against hierarchical research threshold bands."""
        # 1. VERY HIGH
        very_high_drivers: List[str] = []
        if f24_val is not None and f24_val >= bands.very_high_24h_mm:
            very_high_drivers.append(f"24h forecast ({f24_val:.1f} mm >= {bands.very_high_24h_mm} mm)")
        if f6_val is not None and f6_val >= bands.very_high_6h_mm:
            very_high_drivers.append(f"6h forecast ({f6_val:.1f} mm >= {bands.very_high_6h_mm} mm)")
        if obs_val is not None and obs_val >= bands.very_high_recent_mm:
            very_high_drivers.append(f"recent observed ({obs_val:.1f} mm >= {bands.very_high_recent_mm} mm)")

        if very_high_drivers:
            return RiskCategory.VERY_HIGH, very_high_drivers

        # 2. HIGH
        high_drivers: List[str] = []
        if f24_val is not None and f24_val >= bands.high_24h_mm:
            high_drivers.append(f"24h forecast ({f24_val:.1f} mm >= {bands.high_24h_mm} mm)")
        if f6_val is not None and f6_val >= bands.high_6h_mm:
            high_drivers.append(f"6h forecast ({f6_val:.1f} mm >= {bands.high_6h_mm} mm)")
        if obs_val is not None and obs_val >= bands.high_recent_mm:
            high_drivers.append(f"recent observed ({obs_val:.1f} mm >= {bands.high_recent_mm} mm)")

        if high_drivers:
            return RiskCategory.HIGH, high_drivers

        # 3. MODERATE
        moderate_drivers: List[str] = []
        if f24_val is not None and f24_val >= bands.moderate_24h_mm:
            moderate_drivers.append(f"24h forecast ({f24_val:.1f} mm >= {bands.moderate_24h_mm} mm)")
        if f6_val is not None and f6_val >= bands.moderate_6h_mm:
            moderate_drivers.append(f"6h forecast ({f6_val:.1f} mm >= {bands.moderate_6h_mm} mm)")
        if obs_val is not None and obs_val >= bands.moderate_recent_mm:
            moderate_drivers.append(f"recent observed ({obs_val:.1f} mm >= {bands.moderate_recent_mm} mm)")

        if moderate_drivers:
            return RiskCategory.MODERATE, moderate_drivers

        # 4. LOW
        return RiskCategory.LOW, ["all precipitation metrics remain below moderate research threshold (15.6 mm / 24h)"]

    def _build_explanation(
        self,
        category: RiskCategory,
        obs_val: Optional[float],
        f6_val: Optional[float],
        f24_val: Optional[float],
        drivers: List[str],
        confidence: RiskConfidence,
    ) -> str:
        """Formulate explicit, transparent hydrological reasoning."""
        drivers_text = "; ".join(drivers)
        obs_text = f"{obs_val:.2f} mm" if obs_val is not None else "N/A"
        f6_text = f"{f6_val:.2f} mm" if f6_val is not None else "N/A"
        f24_text = f"{f24_val:.2f} mm" if f24_val is not None else "N/A"

        if category == RiskCategory.VERY_HIGH:
            desc = f"VERY HIGH surface flood risk. Triggered by {drivers_text}."
        elif category == RiskCategory.HIGH:
            desc = f"HIGH surface flood risk. Triggered by {drivers_text}."
        elif category == RiskCategory.MODERATE:
            desc = f"MODERATE surface flood risk. Triggered by {drivers_text}."
        else:
            if obs_val == 0.0 and f24_val == 0.0:
                desc = "LOW surface flood risk. Zero rainfall observed and zero rainfall forecast over the next 24 hours."
            else:
                desc = f"LOW surface flood risk. Precipitation accumulations remain below moderate thresholds ({drivers_text})."

        metrics_summary = f"[Observed: {obs_text}, 6h Forecast: {f6_text}, 24h Forecast: {f24_text}; Confidence: {confidence.value}]."
        caveat = (
            " NOTE: Rainfall accumulation indicates local surface runoff potential only; "
            "it does not account for river stage elevation, embankment breach, or tidal storm surge."
        )

        return f"{desc} {metrics_summary}{caveat}"

    def _build_unavailable_response(
        self,
        risk_input: RainfallRiskInput,
        assessment_time: datetime,
        explanation: str,
        warnings: List[str],
    ) -> RainfallRiskAssessmentResponse:
        """Construct safe UNAVAILABLE classification response."""
        risk_context = RiskExplanationEngine.generate_explanation(
            category=RiskCategory.UNAVAILABLE,
            confidence=RiskConfidence.NONE,
            location_name=risk_input.location_name,
            district_name=risk_input.district_name,
            recent_observed_mm=risk_input.recent_observed_rainfall_mm,
            forecast_6h_mm=risk_input.forecast_6h_precipitation_mm,
            forecast_24h_mm=risk_input.forecast_24h_precipitation_mm,
            observation_freshness=risk_input.observation_freshness,
            observation_age_minutes=risk_input.observation_age_minutes,
            forecast_completeness_pct=risk_input.forecast_completeness_pct,
            active_forecast_hours=risk_input.active_forecast_hours,
            quality_status=QualityStatus.FAIL,
            quality_warnings=warnings,
            drivers=[explanation],
        )

        return RainfallRiskAssessmentResponse(
            location_id=risk_input.location_id,
            location_name=risk_input.location_name,
            district_name=risk_input.district_name,
            latitude=risk_input.latitude,
            longitude=risk_input.longitude,
            assessment_timestamp=assessment_time,
            risk_category=RiskCategory.UNAVAILABLE,
            confidence=RiskConfidence.NONE,
            assessment_explanation=explanation,
            recent_observed_rainfall_mm=risk_input.recent_observed_rainfall_mm,
            forecast_6h_precipitation_mm=risk_input.forecast_6h_precipitation_mm,
            forecast_24h_precipitation_mm=risk_input.forecast_24h_precipitation_mm,
            observation_freshness=risk_input.observation_freshness,
            observation_age_minutes=risk_input.observation_age_minutes,
            observation_timestamp=risk_input.observation_timestamp,
            forecast_completeness_pct=risk_input.forecast_completeness_pct,
            active_forecast_hours=risk_input.active_forecast_hours,
            quality_status=QualityStatus.FAIL,
            quality_warnings=warnings,
            risk_context=risk_context,
            threshold_version=self.config.version,
            component_status=HydrologicalComponentStatus.OPERATIONAL,
            prototype_disclaimer=self.config.disclaimer,
        )
