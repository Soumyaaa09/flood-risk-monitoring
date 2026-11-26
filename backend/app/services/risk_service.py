"""Risk Assessment Coordination Service for FloodWatch.

Coordinates data retrieval, quality validation, and execution of the modular risk engines
across pilot monitoring locations (Paradeep, Cuttack, Kendrapara).
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.risk_engine.config import RainfallRiskConfig, get_risk_config
from app.risk_engine.explanation_engine import RiskExplanationEngine
from app.risk_engine.historical_analyzer import HistoricalRainfallAnalyzer
from app.risk_engine.interfaces import (
    CombinedRiskAssessorInterface,
    RiverStageRiskAssessorInterface,
)
from app.risk_engine.rainfall_assessor import RainfallRiskAssessor, RainfallRiskInput
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualitySeverity, QualityStatus
from app.schemas.risk import (
    AntecedentRainfallIndicator,
    ComponentReadiness,
    HistoricalRainfallContext,
    HistoricalRainfallResponse,
    HistoricalRainfallSummaryResponse,
    RainfallRiskAssessmentResponse,
    RainfallRiskSummaryResponse,
    RiskCategory,
)
from app.services.data_quality import (
    DataQualityValidator,
    FreshnessEvaluator,
    normalize_to_utc,
    parse_utc_timestamp,
)

logger = logging.getLogger(__name__)

# Bounded per-request concurrency when querying pilot locations from Supabase
MAX_CONCURRENT_LOCATION_QUERIES = 5
_UNSET_FALLBACK = object()


class RiskService:
    """Read-only service coordinating flood-risk assessments and historical rainfall analytics."""

    def __init__(
        self,
        supabase_client: Optional[SupabaseClient] = None,
        risk_config: Optional[RainfallRiskConfig] = None,
    ):
        self.supabase = supabase_client or SupabaseClient()
        self.config = risk_config or get_risk_config()
        self.rainfall_assessor = RainfallRiskAssessor(config=self.config)
        self.river_stage_assessor = RiverStageRiskAssessorInterface()
        self.combined_assessor = CombinedRiskAssessorInterface()

    def get_component_readiness(self) -> List[ComponentReadiness]:
        """Return operational status and capabilities across all risk components."""
        return [
            self.rainfall_assessor.get_readiness(),
            self.river_stage_assessor.get_readiness(),
            self.combined_assessor.get_readiness(),
        ]

    async def get_location_history_context(
        self,
        location_id: str,
        days: int = 7,
        now_time: Optional[datetime] = None,
        fallback_analysis: Any = _UNSET_FALLBACK,
        raw_observations: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[HistoricalRainfallContext, AntecedentRainfallIndicator]:
        """Fetch historical records and calculate time-series context and antecedent index."""
        ts = normalize_to_utc(now_time) or datetime.now(timezone.utc)
        raw_obs = (
            raw_observations
            if raw_observations is not None
            else await self.supabase.get_historical_observations_for_location(
                location_id=location_id, days=days
            )
        )
        # Reuse already-fetched analysis when supplied; only re-fetch for standalone callers.
        if not raw_obs:
            if fallback_analysis is not _UNSET_FALLBACK:
                if isinstance(fallback_analysis, dict):
                    raw_obs = [fallback_analysis]
            else:
                latest = await self.supabase.get_latest_observations_for_location(location_id=location_id)
                if isinstance(latest, dict) and latest.get("analysis"):
                    raw_obs = [latest["analysis"]]

        history = HistoricalRainfallAnalyzer.analyze_history(
            raw_observations=raw_obs or [],
            reference_time=ts,
            window_days=days,
        )
        antecedent = HistoricalRainfallAnalyzer.calculate_antecedent_indicator(history)
        return history, antecedent

    async def build_rainfall_input_for_location(
        self,
        location: Dict[str, Any],
        now_time: Optional[datetime] = None,
        obs_data: Optional[Dict[str, Any]] = None,
    ) -> RainfallRiskInput:
        """Fetch latest telemetry and construct validated RainfallRiskInput for a location."""
        loc_id = str(location["id"])
        loc_name = str(location["name"])
        district_name = (
            location.get("districts", {}).get("name")
            if isinstance(location.get("districts"), dict)
            else location.get("district_name")
        )
        lat = float(location["latitude"])
        lon = float(location["longitude"])
        now_utc = normalize_to_utc(now_time) or datetime.now(timezone.utc)

        # 1. Location metadata validation
        loc_issues = DataQualityValidator.validate_location_metadata(location)

        # 2. Fetch latest observation data (or reuse pre-fetched request-scoped telemetry)
        if obs_data is None:
            obs_data = await self.supabase.get_latest_observations_for_location(location_id=loc_id)
        raw_analysis = obs_data.get("analysis")
        raw_forecasts = obs_data.get("forecasts", [])

        # 3. Evaluate Recent Observed Rainfall
        recent_val: Optional[float] = None
        obs_freshness_state = FreshnessState.UNAVAILABLE
        obs_dt: Optional[datetime] = None
        obs_age_minutes: Optional[float] = None

        if raw_analysis:
            # Validate physical value
            raw_val = raw_analysis.get("value")
            val_issues = DataQualityValidator.validate_observation_value(
                raw_val,
                measurement_type="rainfall",
                unit=raw_analysis.get("unit", "mm"),
                field_name="analysis.value",
            )
            loc_issues.extend(val_issues)
            if raw_val is not None:
                try:
                    recent_val = round(float(raw_val), 4)
                except (ValueError, TypeError):
                    recent_val = None

            # Validate phenomenon timestamp (valid_at)
            raw_valid = raw_analysis.get("valid_at")
            v_dt, v_issues = DataQualityValidator.validate_timestamp(raw_valid, field_name="analysis.valid_at")
            loc_issues.extend(v_issues)
            if v_dt:
                obs_dt = v_dt
                obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate_observation(
                    obs_dt, reference_time=now_utc, config=self.config
                )
            else:
                obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate_observation(None)
        else:
            obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate_observation(None)

        # 4. Evaluate Forecast Horizon
        forecast_status, fc_issues, fc_meta = DataQualityValidator.validate_forecast_horizon(
            raw_forecasts, reference_time=now_utc, min_hours=self.config.min_forecast_hours
        )
        loc_issues.extend(fc_issues)

        # 5. Calculate 6h and 24h Forward Accumulated Forecast Precipitation
        f6_accum = 0.0
        f24_accum = 0.0
        valid_6h_points = 0
        valid_24h_points = 0

        for fc in raw_forecasts:
            if not isinstance(fc, dict):
                continue
            v_fc = parse_utc_timestamp(fc.get("valid_at"))
            if v_fc is None:
                continue

            val = fc.get("value")
            if val is None or v_fc is None:
                continue

            delta_sec = (v_fc - now_utc).total_seconds()
            if 0 <= delta_sec <= 21600:  # Next 6 hours
                f6_accum += float(val)
                valid_6h_points += 1
            if 0 <= delta_sec <= 86400:  # Next 24 hours
                f24_accum += float(val)
                valid_24h_points += 1

        f6_total = round(f6_accum, 2) if valid_6h_points > 0 else None
        f24_total = round(f24_accum, 2) if valid_24h_points > 0 else None

        # 6. Overall Location Quality Status
        has_errors = any(i.severity in [QualitySeverity.ERROR, QualitySeverity.CRITICAL] for i in loc_issues)
        has_warnings = any(i.severity == QualitySeverity.WARNING for i in loc_issues)

        if has_errors or obs_freshness_state == FreshnessState.UNAVAILABLE:
            quality_status = QualityStatus.FAIL
        elif has_warnings or obs_freshness_state in [FreshnessState.DELAYED, FreshnessState.STALE]:
            quality_status = QualityStatus.WARNING
        else:
            quality_status = QualityStatus.PASS

        quality_warnings = [i.message for i in loc_issues if i.severity == QualitySeverity.WARNING]

        return RainfallRiskInput(
            location_id=loc_id,
            location_name=loc_name,
            district_name=district_name,
            latitude=lat,
            longitude=lon,
            recent_observed_rainfall_mm=recent_val,
            forecast_6h_precipitation_mm=f6_total,
            forecast_24h_precipitation_mm=f24_total,
            observation_freshness=obs_freshness_state,
            observation_age_minutes=obs_age_minutes,
            observation_timestamp=obs_dt,
            forecast_completeness_pct=fc_meta.get("completeness_pct", 100.0),
            active_forecast_hours=fc_meta.get("horizon_hours", len(raw_forecasts)),
            quality_status=quality_status,
            quality_warnings=quality_warnings,
        )

    async def _assess_location_with_history(
        self,
        location: Dict[str, Any],
        assessment_time: Optional[datetime] = None,
        history_now_time: Optional[datetime] = None,
    ) -> RainfallRiskAssessmentResponse:
        """Assess rainfall risk and enrich with historical context using concurrent request-scoped fetches."""
        loc_id = str(location["id"])

        # Fetch latest telemetry and historical observations concurrently; strict propagation on primary telemetry errors.
        obs_data, raw_hist_result = await asyncio.gather(
            self.supabase.get_latest_observations_for_location(location_id=loc_id),
            self.supabase.get_historical_observations_for_location(location_id=loc_id, days=7),
            return_exceptions=True,
        )
        if isinstance(obs_data, BaseException):
            raise obs_data

        risk_input = await self.build_rainfall_input_for_location(
            location, now_time=assessment_time, obs_data=obs_data
        )
        res = self.rainfall_assessor.assess_location_risk(risk_input, assessment_time=assessment_time)

        # Attach historical context and antecedent indicator (Phase 2.4.2 & 2.4.4) without duplicate DB reads
        try:
            if isinstance(raw_hist_result, BaseException):
                raise raw_hist_result
            raw_analysis = obs_data.get("analysis") if isinstance(obs_data, dict) else None
            history, antecedent = await self.get_location_history_context(
                loc_id,
                days=7,
                now_time=history_now_time if history_now_time is not None else assessment_time,
                fallback_analysis=raw_analysis,
                raw_observations=raw_hist_result,
            )
            res.historical_context = history
            res.antecedent_indicator = antecedent
            preserved_drivers = list(getattr(res, "_threshold_drivers", []) or [])
            res.risk_context = RiskExplanationEngine.generate_explanation(
                category=res.risk_category,
                confidence=res.confidence,
                location_name=res.location_name,
                district_name=res.district_name,
                recent_observed_mm=res.recent_observed_rainfall_mm,
                forecast_6h_mm=res.forecast_6h_precipitation_mm,
                forecast_24h_mm=res.forecast_24h_precipitation_mm,
                observation_freshness=res.observation_freshness,
                observation_age_minutes=res.observation_age_minutes,
                forecast_completeness_pct=res.forecast_completeness_pct,
                active_forecast_hours=res.active_forecast_hours,
                quality_status=res.quality_status,
                quality_warnings=res.quality_warnings,
                drivers=preserved_drivers,
                historical_context=history,
                antecedent_indicator=antecedent,
            )
        except Exception as exc:
            logger.warning("Failed to attach historical context to location risk: %s", exc)

        return res

    async def get_location_rainfall_risk(
        self, location_id: str, now_time: Optional[datetime] = None
    ) -> Optional[RainfallRiskAssessmentResponse]:
        """Assess rainfall flood risk for a specific pilot location, including historical context."""
        pilot_locations = await self.supabase.get_pilot_locations()
        matching = [l for l in pilot_locations if str(l["id"]) == location_id]
        if not matching:
            return None

        return await self._assess_location_with_history(
            matching[0], assessment_time=now_time, history_now_time=now_time
        )

    async def get_all_locations_rainfall_risk(
        self, now_time: Optional[datetime] = None
    ) -> RainfallRiskSummaryResponse:
        """Produce risk assessments across all registered pilot monitoring locations with historical context."""
        ts = now_time or datetime.now(timezone.utc)
        pilot_locations = await self.supabase.get_pilot_locations()

        category_counts = {
            RiskCategory.LOW.value: 0,
            RiskCategory.MODERATE.value: 0,
            RiskCategory.HIGH.value: 0,
            RiskCategory.VERY_HIGH.value: 0,
            RiskCategory.UNAVAILABLE.value: 0,
        }

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOCATION_QUERIES)

        async def _assess_bounded(location: Dict[str, Any]) -> RainfallRiskAssessmentResponse:
            async with semaphore:
                return await self._assess_location_with_history(
                    location, assessment_time=ts, history_now_time=ts
                )

        assessments: List[RainfallRiskAssessmentResponse] = list(
            await asyncio.gather(*(_assess_bounded(loc) for loc in pilot_locations))
        )

        for res in assessments:
            category_counts[res.risk_category.value] = category_counts.get(res.risk_category.value, 0) + 1

        # Derive pipeline-wide highest risk category
        if category_counts[RiskCategory.VERY_HIGH.value] > 0:
            highest_risk = RiskCategory.VERY_HIGH
        elif category_counts[RiskCategory.HIGH.value] > 0:
            highest_risk = RiskCategory.HIGH
        elif category_counts[RiskCategory.MODERATE.value] > 0:
            highest_risk = RiskCategory.MODERATE
        elif category_counts[RiskCategory.LOW.value] > 0:
            highest_risk = RiskCategory.LOW
        else:
            highest_risk = RiskCategory.UNAVAILABLE

        return RainfallRiskSummaryResponse(
            total_locations=len(assessments),
            highest_risk_category=highest_risk,
            category_counts=category_counts,
            threshold_version=self.config.version,
            assessment_timestamp=ts,
            assessments=assessments,
            component_readiness=self.get_component_readiness(),
            prototype_disclaimer=self.config.disclaimer,
        )

    async def _build_location_rainfall_history_response(
        self,
        location: Dict[str, Any],
        days: int,
        ts: datetime,
    ) -> HistoricalRainfallResponse:
        """Construct HistoricalRainfallResponse for an already-loaded pilot location."""
        loc_id = str(location["id"])
        history, antecedent = await self.get_location_history_context(loc_id, days=days, now_time=ts)

        district_name = (
            location.get("districts", {}).get("name")
            if isinstance(location.get("districts"), dict)
            else location.get("district_name")
        )

        return HistoricalRainfallResponse(
            location_id=loc_id,
            location_name=str(location["name"]),
            district_name=district_name,
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            assessment_timestamp=ts,
            history=history,
            antecedent=antecedent,
            disclaimer=self.config.disclaimer,
        )

    async def get_location_rainfall_history(
        self,
        location_id: str,
        days: int = 7,
        now_time: Optional[datetime] = None,
    ) -> Optional[HistoricalRainfallResponse]:
        """Produce detailed historical rainfall analysis and antecedent indicators for a location."""
        ts = normalize_to_utc(now_time) or datetime.now(timezone.utc)
        pilot_locations = await self.supabase.get_pilot_locations()
        matching = [l for l in pilot_locations if str(l["id"]) == location_id]
        if not matching:
            return None

        return await self._build_location_rainfall_history_response(matching[0], days=days, ts=ts)

    async def get_all_locations_rainfall_history(
        self,
        days: int = 7,
        now_time: Optional[datetime] = None,
    ) -> HistoricalRainfallSummaryResponse:
        """Produce historical rainfall analysis across all registered pilot monitoring locations."""
        ts = normalize_to_utc(now_time) or datetime.now(timezone.utc)
        pilot_locations = await self.supabase.get_pilot_locations()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOCATION_QUERIES)

        async def _fetch_history_bounded(location: Dict[str, Any]) -> HistoricalRainfallResponse:
            async with semaphore:
                return await self._build_location_rainfall_history_response(location, days=days, ts=ts)

        loc_responses: List[HistoricalRainfallResponse] = list(
            await asyncio.gather(*(_fetch_history_bounded(loc) for loc in pilot_locations))
        )

        return HistoricalRainfallSummaryResponse(
            total_locations=len(loc_responses),
            window_days=days,
            assessment_timestamp=ts,
            locations=loc_responses,
            disclaimer=self.config.disclaimer,
        )


def get_risk_service() -> RiskService:
    """Return default RiskService instance."""
    return RiskService()
