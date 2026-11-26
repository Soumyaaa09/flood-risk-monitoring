"""Risk Explanation and Public-Friendly Context Engine for FloodWatch.

Transforms technical hydrological assessment metrics, multi-window historical telemetry,
and numerical forecast horizons into clear, transparent, evidence-based public explanations.

Strict Principles:
1. Explanations derive strictly from authentic telemetry, forecasts, and threshold drivers.
2. No invented numbers, probabilities, or simulated flood depth.
3. Observed, historical, forecast, antecedent, and data-quality evidence remain strictly segregated.
4. Plain-language summaries explain *why* a location is in a specific risk category.
5. Clear research disclaimers: no claims of confirmed flooding, river overflow, or evacuation orders.
"""

from typing import Any, Dict, List, Optional
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualityStatus
from app.schemas.risk import (
    AntecedentCategory,
    AntecedentRainfallIndicator,
    HistoricalRainfallContext,
    RainfallTrend,
    RiskCategory,
    RiskConfidence,
    RiskContextExplanation,
    RiskEvidenceSegment,
)


class RiskExplanationEngine:
    """Generates structured, plain-language risk explanations and evidence segments."""

    @classmethod
    def generate_explanation(
        cls,
        category: RiskCategory,
        confidence: RiskConfidence,
        location_name: str,
        district_name: Optional[str],
        recent_observed_mm: Optional[float],
        forecast_6h_mm: Optional[float],
        forecast_24h_mm: Optional[float],
        observation_freshness: FreshnessState,
        observation_age_minutes: Optional[float],
        forecast_completeness_pct: float,
        active_forecast_hours: int,
        quality_status: QualityStatus,
        quality_warnings: List[str],
        drivers: List[str],
        historical_context: Optional[HistoricalRainfallContext] = None,
        antecedent_indicator: Optional[AntecedentRainfallIndicator] = None,
    ) -> RiskContextExplanation:
        """Construct public-friendly summary, contributing factors, impact advisory, and evidence segments."""

        loc_label = location_name.replace(" Weather Grid Point", "")
        dist_label = f" ({district_name})" if district_name else ""

        # ---------------------------------------------------------------------
        # 1. Public-Friendly Summary
        # ---------------------------------------------------------------------
        public_summary = cls._generate_public_summary(
            category=category,
            obs_val=recent_observed_mm,
            f6_val=forecast_6h_mm,
            f24_val=forecast_24h_mm,
            freshness=observation_freshness,
            age_minutes=observation_age_minutes,
            active_hours=active_forecast_hours,
            loc_label=loc_label,
            history=historical_context,
            antecedent=antecedent_indicator,
        )

        # ---------------------------------------------------------------------
        # 2. Main Contributing Factors (Bullet Points)
        # ---------------------------------------------------------------------
        factors = cls._generate_contributing_factors(
            category=category,
            obs_val=recent_observed_mm,
            f6_val=forecast_6h_mm,
            f24_val=forecast_24h_mm,
            freshness=observation_freshness,
            age_minutes=observation_age_minutes,
            forecast_completeness=forecast_completeness_pct,
            active_hours=active_forecast_hours,
            history=historical_context,
            antecedent=antecedent_indicator,
            drivers=drivers,
        )

        # ---------------------------------------------------------------------
        # 3. Plain-Language Impact Advisory
        # ---------------------------------------------------------------------
        impact_advisory = cls._generate_impact_advisory(category=category, confidence=confidence)

        # ---------------------------------------------------------------------
        # 4. Strictly Segregated Evidence Blocks
        # ---------------------------------------------------------------------
        observed_segment = cls._build_observed_segment(
            obs_val=recent_observed_mm,
            freshness=observation_freshness,
            age_minutes=observation_age_minutes,
        )

        historical_segment = cls._build_historical_segment(history=historical_context)

        forecast_segment = cls._build_forecast_segment(
            f6_val=forecast_6h_mm,
            f24_val=forecast_24h_mm,
            active_hours=active_forecast_hours,
            completeness=forecast_completeness_pct,
        )

        antecedent_segment = cls._build_antecedent_segment(antecedent=antecedent_indicator)

        quality_segment = cls._build_quality_segment(
            confidence=confidence,
            freshness=observation_freshness,
            quality_status=quality_status,
            warnings=quality_warnings,
        )

        return RiskContextExplanation(
            public_summary=public_summary,
            contributing_factors=factors,
            impact_advisory=impact_advisory,
            observed_context=observed_segment,
            historical_context=historical_segment,
            forecast_context=forecast_segment,
            antecedent_context=antecedent_segment,
            data_quality_context=quality_segment,
        )

    # -------------------------------------------------------------------------
    # Internal Generator Helpers
    # -------------------------------------------------------------------------

    @classmethod
    def _generate_public_summary(
        cls,
        category: RiskCategory,
        obs_val: Optional[float],
        f6_val: Optional[float],
        f24_val: Optional[float],
        freshness: FreshnessState,
        age_minutes: Optional[float],
        active_hours: int,
        loc_label: str,
        history: Optional[HistoricalRainfallContext],
        antecedent: Optional[AntecedentRainfallIndicator],
    ) -> str:
        """Create plain-language summary reflecting current conditions."""
        if category == RiskCategory.UNAVAILABLE:
            if obs_val is None and f24_val is None:
                return f"Available rainfall observations and forecast predictions are missing for {loc_label}; risk cannot be reliably determined."
            if freshness == FreshnessState.STALE:
                return f"Rainfall telemetry for {loc_label} is stale ({round(age_minutes or 0)} minutes old); assessment paused to prevent obsolete guidance."
            if active_hours < 24:
                return f"Forecast horizon for {loc_label} is incomplete ({active_hours}h available; minimum 24h required); insufficient data for a forward assessment."
            return f"Available telemetry is insufficient to calculate a reliable rainfall flood-risk assessment for {loc_label}."

        if category == RiskCategory.VERY_HIGH:
            lead = f"Extreme rainfall conditions detected at {loc_label}."
            if f24_val is not None and f24_val >= 115.6:
                return f"{lead} Heavy to torrential precipitation ({f24_val:.1f} mm) is forecast over the next 24 hours, indicating severe surface water runoff potential."
            if obs_val is not None and obs_val >= 64.5:
                return f"{lead} Very heavy rainfall ({obs_val:.1f} mm) has been recorded recently, generating immediate intense surface water accumulation."
            return f"{lead} Multi-period precipitation accumulations exceed extreme research thresholds."

        if category == RiskCategory.HIGH:
            lead = f"Heavy rainfall conditions detected at {loc_label}."
            if f24_val is not None and f24_val >= 64.5:
                return f"{lead} Forecast rainfall of {f24_val:.1f} mm over the next 24 hours is expected to create substantial surface water accumulation."
            if obs_val is not None and obs_val >= 35.6:
                return f"{lead} Heavy rainfall ({obs_val:.1f} mm) was recorded recently, significantly elevating catchment runoff potential."
            return f"{lead} Elevated precipitation metrics exceed high runoff thresholds."

        if category == RiskCategory.MODERATE:
            if f24_val is not None and f24_val >= 15.6:
                return f"Moderate rainfall ({f24_val:.1f} mm) is forecast over the next 24 hours at {loc_label}, which may lead to localized water accumulation."
            if obs_val is not None and obs_val >= 7.6:
                return f"Moderate rainfall ({obs_val:.1f} mm) was recorded recently at {loc_label}, creating mild surface runoff."
            return f"Moderate rainfall accumulations detected at {loc_label}; conditions indicate localized runoff readiness."

        # LOW Risk
        if (obs_val == 0.0 or obs_val is None) and (f24_val == 0.0 or f24_val is None):
            return f"Rainfall conditions are currently dry at {loc_label}, with zero precipitation observed and zero forecast over the next 24 hours."
        return f"Rainfall conditions are currently low at {loc_label}. Minimal surface water runoff is anticipated based on current weather model data."

    @classmethod
    def _generate_contributing_factors(
        cls,
        category: RiskCategory,
        obs_val: Optional[float],
        f6_val: Optional[float],
        f24_val: Optional[float],
        freshness: FreshnessState,
        age_minutes: Optional[float],
        forecast_completeness: float,
        active_hours: int,
        history: Optional[HistoricalRainfallContext],
        antecedent: Optional[AntecedentRainfallIndicator],
        drivers: List[str],
    ) -> List[str]:
        """Derive explicit, data-backed factor bullet points."""
        factors: List[str] = []

        # 1. Observed rainfall factor
        if obs_val is not None:
            age_str = f", recorded {round(age_minutes)}m ago" if age_minutes is not None else ""
            factors.append(f"Recent observed rainfall is {obs_val:.1f} mm ({freshness.value.lower()} telemetry{age_str}).")
        else:
            factors.append("Recent observed rainfall telemetry is currently unavailable.")

        # 2. Forecast factor
        if f24_val is not None:
            f6_str = f" ({f6_val:.1f} mm in next 6h)" if f6_val is not None else ""
            factors.append(f"Forward 24-hour forecast projects {f24_val:.1f} mm of rainfall{f6_str}.")
        else:
            factors.append(f"Forecast coverage is limited ({active_hours}h active, {forecast_completeness:.1f}% complete).")

        # 3. Historical trend and antecedent accumulation factor
        if history and (history.rainfall_24h_mm is not None or history.rainfall_72h_mm is not None):
            r72 = f"{history.rainfall_72h_mm:.1f} mm" if history.rainfall_72h_mm is not None else "N/A"
            trend_str = history.daily_trend.value.lower().replace("_", " ")
            factors.append(f"Historical 72-hour precipitation total is {r72} with a {trend_str} daily trend.")

        # 4. Antecedent indicator factor
        if antecedent and antecedent.indicator_value_mm is not None:
            cat_str = antecedent.indicator_category.value.lower().replace("_", " ")
            factors.append(f"Antecedent Rainfall Indicator is {cat_str} ({antecedent.indicator_value_mm:.1f} mm weighted index).")

        # 5. Threshold driver summary (preserve meaningful order, remove duplicates)
        if drivers:
            unique_drivers = list(
                dict.fromkeys(d.strip() for d in drivers if isinstance(d, str) and d.strip())
            )
            if unique_drivers:
                factors.append(f"Primary classification criteria: {'; '.join(unique_drivers)}.")

        return factors

    @classmethod
    def _generate_impact_advisory(cls, category: RiskCategory, confidence: RiskConfidence) -> str:
        """Provide transparent, non-alarmist decision-support guidance."""
        if category == RiskCategory.UNAVAILABLE:
            return (
                "DATA GAP ADVISORY: Telemetry is insufficient or stale. FloodWatch cannot verify surface runoff conditions. "
                "Consult official India Meteorological Department (IMD) bulletins for current weather."
            )
        if category == RiskCategory.VERY_HIGH:
            return (
                "SEVERE RUNOFF ADVISORY: High surface runoff potential expected. Low-lying depressions, roadside channels, "
                "and poorly drained areas may experience rapid water accumulation. Follow guidance from district disaster management authorities."
            )
        if category == RiskCategory.HIGH:
            return (
                "ELEVATED RUNOFF ADVISORY: Significant surface water runoff likely. Water pooling expected in vulnerable low-lying spots. "
                "Monitor local rainfall telemetry and official OSDMA weather releases."
            )
        if category == RiskCategory.MODERATE:
            return (
                "AWARENESS ADVISORY: Mild to moderate runoff possible in local catchments. Routine drainage expected to cope, but monitor if rain intensifies."
            )
        return (
            "NORMAL ADVISORY: No significant surface runoff concerns detected. Catchment precipitation is within normal limits."
        )

    # -------------------------------------------------------------------------
    # Segment Builders
    # -------------------------------------------------------------------------

    @classmethod
    def _build_observed_segment(
        cls,
        obs_val: Optional[float],
        freshness: FreshnessState,
        age_minutes: Optional[float],
    ) -> RiskEvidenceSegment:
        if obs_val is None:
            summary = "Recent rainfall measurement is missing from the data stream."
            status = "UNAVAILABLE"
        else:
            age_txt = f"{round(age_minutes)} min ago" if age_minutes is not None else "unknown age"
            summary = f"Current nowcast records {obs_val:.1f} mm of rainfall ({freshness.value}, {age_txt})."
            status = freshness.value

        return RiskEvidenceSegment(
            title="Observed Precipitation (Nowcast)",
            summary=summary,
            status=status,
            metrics={"value_mm": obs_val, "age_minutes": age_minutes, "freshness": freshness.value},
            caveat="Model nowcast estimate derived from Open-Meteo; not an official physical ground rain gauge.",
        )

    @classmethod
    def _build_historical_segment(
        cls, history: Optional[HistoricalRainfallContext]
    ) -> RiskEvidenceSegment:
        if not history or (history.rainfall_24h_mm is None and history.rainfall_72h_mm is None):
            return RiskEvidenceSegment(
                title="Historical Rainfall Evidence",
                summary="Insufficient historical observations recorded over preceding days.",
                status="UNAVAILABLE",
                metrics={},
                caveat="Historical calculations strictly exclude numerical forecast records.",
            )

        r24 = history.rainfall_24h_mm
        r72 = history.rainfall_72h_mm
        r7d = history.rainfall_7d_mm
        summary = (
            f"Preceding accumulations: 24h: {r24 if r24 is not None else 'N/A'} mm, "
            f"72h: {r72 if r72 is not None else 'N/A'} mm, "
            f"7d: {r7d if r7d is not None else 'N/A'} mm. Trend: {history.daily_trend.value}."
        )

        return RiskEvidenceSegment(
            title="Historical Rainfall Evidence",
            summary=summary,
            status=history.daily_trend.value,
            metrics={
                "rainfall_24h_mm": r24,
                "rainfall_72h_mm": r72,
                "rainfall_7d_mm": r7d,
                "consecutive_rainy_days": history.consecutive_rainy_days,
                "coverage_pct": history.coverage_pct,
            },
            caveat="Calculated strictly from verified historical observations; forecasts are excluded.",
        )

    @classmethod
    def _build_forecast_segment(
        cls,
        f6_val: Optional[float],
        f24_val: Optional[float],
        active_hours: int,
        completeness: float,
    ) -> RiskEvidenceSegment:
        if f24_val is None:
            summary = "24-hour forward forecast precipitation is unavailable or incomplete."
            status = "INCOMPLETE"
        else:
            f6_txt = f"{f6_val:.1f} mm (6h)" if f6_val is not None else "N/A"
            summary = f"Forward projections indicate {f24_val:.1f} mm over 24h and {f6_txt}."
            status = "ACTIVE"

        return RiskEvidenceSegment(
            title="Numerical Weather Forecast",
            summary=summary,
            status=status,
            metrics={
                "forecast_6h_mm": f6_val,
                "forecast_24h_mm": f24_val,
                "horizon_hours": active_hours,
                "completeness_pct": completeness,
            },
            caveat="Numerical weather forecast model estimates are subject to meteorological uncertainty.",
        )

    @classmethod
    def _build_antecedent_segment(
        cls, antecedent: Optional[AntecedentRainfallIndicator]
    ) -> RiskEvidenceSegment:
        if not antecedent or antecedent.indicator_value_mm is None:
            return RiskEvidenceSegment(
                title="Antecedent Moisture Context",
                summary="Antecedent indicator is unavailable due to incomplete preceding telemetry.",
                status=AntecedentCategory.UNAVAILABLE.value,
                metrics={},
                caveat="Empirical precipitation accumulation index; does not simulate physical soil moisture.",
            )

        summary = (
            f"Preceding accumulation index is {antecedent.indicator_value_mm:.1f} mm "
            f"({antecedent.indicator_category.value.replace('_', ' ')})."
        )

        return RiskEvidenceSegment(
            title="Antecedent Moisture Context",
            summary=summary,
            status=antecedent.indicator_category.value,
            metrics={
                "indicator_value_mm": antecedent.indicator_value_mm,
                "category": antecedent.indicator_category.value,
                "confidence": antecedent.confidence.value,
            },
            caveat="Weighted empirical accumulation (0.5*24h + 0.3*72h + 0.2*7d). Does NOT measure soil moisture.",
        )

    @classmethod
    def _build_quality_segment(
        cls,
        confidence: RiskConfidence,
        freshness: FreshnessState,
        quality_status: QualityStatus,
        warnings: List[str],
    ) -> RiskEvidenceSegment:
        summary = (
            f"Data confidence is {confidence.value}. Observation freshness is {freshness.value}. "
            f"{len(warnings)} quality notice(s) recorded."
        )

        return RiskEvidenceSegment(
            title="Data Reliability & Telemetry Quality",
            summary=summary,
            status=quality_status.value,
            metrics={
                "confidence": confidence.value,
                "freshness": freshness.value,
                "quality_status": quality_status.value,
                "warnings_count": len(warnings),
            },
            caveat="Missing or stale telemetry is never substituted with zero rainfall.",
        )
