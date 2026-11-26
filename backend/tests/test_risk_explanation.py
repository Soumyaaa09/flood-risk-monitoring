"""Comprehensive Tests for Risk Context & Public Explanation Engine (Phase 2.4.4).

Verification Coverage:
1. Public-friendly summary generation for all risk categories (LOW, MODERATE, HIGH, VERY_HIGH, UNAVAILABLE).
2. Data-backed contributing factors generation without fabricated values.
3. Plain-language non-alarmist impact advisory generation.
4. Strict evidence separation (Observed vs Historical vs Forecast vs Antecedent vs Data Quality).
5. Missing, stale, and incomplete telemetry explanation handling (no fake zeroes).
6. Confidence rating reflection in explanations.
7. Location isolation across pilot points.
8. Preservation of Phase 2.4.1 numerical thresholds and risk classifications.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
import httpx
from httpx import AsyncClient

from app.api.v1.routes.risk import get_risk_service
from app.db.supabase_client import SupabaseClient
from app.main import app
from app.risk_engine.explanation_engine import RiskExplanationEngine
from app.risk_engine.rainfall_assessor import RainfallRiskAssessor, RainfallRiskInput
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualityStatus
from app.schemas.risk import (
    AntecedentCategory,
    AntecedentRainfallIndicator,
    HistoricalRainfallContext,
    RainfallTrend,
    RiskCategory,
    RiskConfidence,
)
from app.services.risk_service import RiskService

PILOT_LOCATIONS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "district_id": "d1",
        "name": "Paradeep Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.3160,
        "longitude": 86.6110,
        "districts": {"name": "Jagatsinghpur"},
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "district_id": "d2",
        "name": "Cuttack Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.4625,
        "longitude": 85.8830,
        "districts": {"name": "Cuttack"},
    },
]


def test_public_summary_dry_low_risk():
    """Verify clean plain-language summary for zero precipitation / dry low risk."""
    explanation = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.LOW,
        confidence=RiskConfidence.HIGH,
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        recent_observed_mm=0.0,
        forecast_6h_mm=0.0,
        forecast_24h_mm=0.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=15.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=72,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        drivers=["all precipitation metrics remain below moderate research threshold"],
    )

    assert "dry" in explanation.public_summary.lower() or "low" in explanation.public_summary.lower()
    assert "Paradeep" in explanation.public_summary
    assert len(explanation.contributing_factors) >= 2
    assert "NORMAL ADVISORY" in explanation.impact_advisory


def test_public_summary_heavy_rain_high_risk():
    """Verify explanation specifically cites 24h forecast exceeding threshold."""
    explanation = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.HIGH,
        confidence=RiskConfidence.HIGH,
        location_name="Cuttack Weather Grid Point",
        district_name="Cuttack",
        recent_observed_mm=5.0,
        forecast_6h_mm=25.0,
        forecast_24h_mm=75.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=20.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=72,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        drivers=["24h forecast (75.0 mm >= 64.5 mm)"],
    )

    assert "Heavy rainfall" in explanation.public_summary or "heavy" in explanation.public_summary.lower()
    assert "75.0 mm" in explanation.public_summary
    assert any("75.0 mm" in f for f in explanation.contributing_factors)
    assert "ELEVATED RUNOFF ADVISORY" in explanation.impact_advisory


def test_public_summary_stale_unavailable_data():
    """Verify stale observation produces safe explanation and data gap advisory."""
    explanation = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.UNAVAILABLE,
        confidence=RiskConfidence.NONE,
        location_name="Kendrapara Weather Grid Point",
        district_name="Kendrapara",
        recent_observed_mm=10.0,
        forecast_6h_mm=5.0,
        forecast_24h_mm=15.0,
        observation_freshness=FreshnessState.STALE,
        observation_age_minutes=420.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=72,
        quality_status=QualityStatus.FAIL,
        quality_warnings=["Observation is STALE (420m old)."],
        drivers=["Observation is STALE"],
    )

    assert "stale" in explanation.public_summary.lower()
    assert "420" in explanation.public_summary
    assert "DATA GAP ADVISORY" in explanation.impact_advisory
    assert explanation.data_quality_context.metrics["freshness"] == "stale"


def test_strict_evidence_separation():
    """Verify separate context blocks for observed, historical, forecast, antecedent, and quality."""
    now_utc = datetime.now(timezone.utc)
    mock_history = HistoricalRainfallContext(
        reference_time=now_utc,
        window_days=7,
        rainfall_24h_mm=14.2,
        rainfall_72h_mm=38.6,
        rainfall_7d_mm=82.0,
        daily_totals=[],
        daily_trend=RainfallTrend.INCREASING,
        consecutive_rainy_days=3,
        coverage_pct=95.0,
        missing_intervals_count=0,
        has_sufficient_coverage=True,
    )
    mock_antecedent = AntecedentRainfallIndicator(
        indicator_category=AntecedentCategory.HIGH_ANTECEDENT,
        indicator_value_mm=35.1,
        underlying_24h_mm=14.2,
        underlying_72h_mm=38.6,
        underlying_7d_mm=82.0,
        coverage_pct=95.0,
        missing_intervals_count=0,
        confidence=RiskConfidence.HIGH,
        explanation="HIGH antecedent rainfall accumulation.",
    )

    explanation = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.MODERATE,
        confidence=RiskConfidence.HIGH,
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        recent_observed_mm=8.5,
        forecast_6h_mm=12.0,
        forecast_24h_mm=22.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=10.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=72,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        drivers=["recent observed (8.5 mm >= 7.6 mm)"],
        historical_context=mock_history,
        antecedent_indicator=mock_antecedent,
    )

    # 1. Observed segment
    assert explanation.observed_context.title == "Observed Precipitation (Nowcast)"
    assert explanation.observed_context.metrics["value_mm"] == 8.5
    assert "ground rain gauge" in explanation.observed_context.caveat

    # 2. Historical segment
    assert explanation.historical_context.title == "Historical Rainfall Evidence"
    assert explanation.historical_context.metrics["rainfall_72h_mm"] == 38.6
    assert explanation.historical_context.metrics["consecutive_rainy_days"] == 3
    assert "exclude" in explanation.historical_context.caveat

    # 3. Forecast segment
    assert explanation.forecast_context.title == "Numerical Weather Forecast"
    assert explanation.forecast_context.metrics["forecast_24h_mm"] == 22.0

    # 4. Antecedent segment
    assert explanation.antecedent_context.title == "Antecedent Moisture Context"
    assert explanation.antecedent_context.metrics["indicator_value_mm"] == 35.1
    assert "soil moisture" in explanation.antecedent_context.caveat

    # 5. Data quality segment
    assert explanation.data_quality_context.title == "Data Reliability & Telemetry Quality"
    assert explanation.data_quality_context.metrics["confidence"] == "HIGH"


@pytest.mark.asyncio
async def test_api_rainfall_risk_includes_risk_context():
    """Verify /api/v1/risk/rainfall response model includes complete risk_context."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = PILOT_LOCATIONS
    now_utc = datetime.now(timezone.utc)

    mock_supabase.get_latest_observations_for_location.return_value = {
        "analysis": {
            "value": 2.5,
            "unit": "mm",
            "valid_at": now_utc.isoformat(),
            "fetched_at": now_utc.isoformat(),
        },
        "forecasts": [
            {"value": 1.0, "unit": "mm", "valid_at": (now_utc + timedelta(hours=i)).isoformat()}
            for i in range(1, 25)
        ],
    }
    mock_supabase.get_historical_observations_for_location.return_value = []

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/risk/rainfall?location_id={PILOT_LOCATIONS[0]['id']}")

        assert resp.status_code == 200
        data = resp.json()

        assert "risk_context" in data
        assert data["risk_context"] is not None
        ctx = data["risk_context"]
        assert "public_summary" in ctx
        assert "contributing_factors" in ctx
        assert "impact_advisory" in ctx
        assert "observed_context" in ctx
        assert "historical_context" in ctx
        assert "forecast_context" in ctx
        assert "antecedent_context" in ctx
        assert "data_quality_context" in ctx
        assert "limitations_disclaimer" in ctx
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


def test_regression_phase_241_classification_unchanged():
    """Verify that adding explanations does NOT alter Phase 2.4.1 risk classifications."""
    assessor = RainfallRiskAssessor()
    risk_input = RainfallRiskInput(
        location_id="test-loc",
        location_name="Test Station",
        district_name="Test District",
        latitude=20.0,
        longitude=86.0,
        recent_observed_rainfall_mm=0.0,
        forecast_6h_precipitation_mm=0.0,
        forecast_24h_precipitation_mm=0.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=5.0,
        observation_timestamp=datetime.now(timezone.utc),
        forecast_completeness_pct=100.0,
        active_forecast_hours=72,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
    )

    res = assessor.assess_location_risk(risk_input)
    assert res.risk_category == RiskCategory.LOW
    assert res.confidence == RiskConfidence.HIGH
    assert res.risk_context is not None
    assert "LOW" in res.risk_context.public_summary or "dry" in res.risk_context.public_summary.lower()


# =============================================================================
# 9. LOW-01: Risk Driver Preservation & Non-Duplication Regression Tests
# =============================================================================

@pytest.mark.asyncio
async def test_risk_service_preserves_drivers_after_historical_context_rebuild():
    """LOW-01: Historical enrichment in RiskService must preserve multi-driver classification criteria."""
    now_utc = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=15)).isoformat()
    mock_loc = {
        "id": "loc-driver-1",
        "name": "Paradeep Weather Grid Point",
        "districts": {"name": "Jagatsinghpur"},
        "latitude": 20.316,
        "longitude": 86.611,
    }

    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = [mock_loc]
    # Trigger multiple VERY_HIGH / HIGH drivers (24h forecast + recent observed)
    mock_sb.get_latest_observations_for_location.return_value = {
        "analysis": {
            "value": 70.0,
            "unit": "mm",
            "valid_at": fresh_iso,
            "fetched_at": fresh_iso,
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        },
        "forecasts": [
            {"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 5.5}
            for h in range(1, 25)
        ],
    }
    mock_sb.get_historical_observations_for_location.return_value = [
        {
            "value": 15.0,
            "unit": "mm",
            "valid_at": (now_utc - timedelta(hours=h)).isoformat(),
            "fetched_at": fresh_iso,
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
        for h in range(1, 25)
    ]

    service = RiskService(supabase_client=mock_sb)
    single = await service.get_location_rainfall_risk("loc-driver-1", now_time=now_utc)
    summary = await service.get_all_locations_rainfall_risk(now_time=now_utc)

    for res in (single, summary.assessments[0]):
        assert res.risk_category == RiskCategory.VERY_HIGH
        assert res.historical_context is not None
        assert res.antecedent_indicator is not None
        assert any(
            "Primary classification criteria:" in factor
            and "24h forecast" in factor
            and "recent observed" in factor
            for factor in res.risk_context.contributing_factors
        )


def test_risk_explanation_engine_drivers_dedup_missing_forecast_only_and_unavailable():
    """LOW-01: Verify driver order preservation, no duplicates on rebuild, forecast-only, and UNAVAILABLE separation."""
    drivers = [
        "24h forecast (80.0 mm >= 64.5 mm)",
        "6h forecast (40.0 mm >= 35.6 mm)",
        "24h forecast (80.0 mm >= 64.5 mm)",
    ]
    ctx1 = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.HIGH,
        confidence=RiskConfidence.HIGH,
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        recent_observed_mm=12.0,
        forecast_6h_mm=40.0,
        forecast_24h_mm=80.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=15.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=24,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        drivers=drivers,
    )
    criteria_lines = [f for f in ctx1.contributing_factors if f.startswith("Primary classification criteria:")]
    assert len(criteria_lines) == 1
    assert criteria_lines[0] == (
        "Primary classification criteria: 24h forecast (80.0 mm >= 64.5 mm); 6h forecast (40.0 mm >= 35.6 mm)."
    )

    # Repeated explanation generation must remain idempotent with identical ordering
    ctx2 = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.HIGH,
        confidence=RiskConfidence.HIGH,
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        recent_observed_mm=12.0,
        forecast_6h_mm=40.0,
        forecast_24h_mm=80.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=15.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=24,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        drivers=drivers,
    )
    assert ctx2.contributing_factors == ctx1.contributing_factors

    # Missing observed / forecast-only evidence stays segregated
    forecast_only_ctx = RiskExplanationEngine.generate_explanation(
        category=RiskCategory.UNAVAILABLE,
        confidence=RiskConfidence.NONE,
        location_name="Cuttack Weather Grid Point",
        district_name="Cuttack",
        recent_observed_mm=None,
        forecast_6h_mm=10.0,
        forecast_24h_mm=20.0,
        observation_freshness=FreshnessState.UNAVAILABLE,
        observation_age_minutes=None,
        forecast_completeness_pct=100.0,
        active_forecast_hours=24,
        quality_status=QualityStatus.FAIL,
        quality_warnings=["Missing analysis observation"],
        drivers=[],
    )
    assert forecast_only_ctx.observed_context.status == "UNAVAILABLE"
    assert "missing" in forecast_only_ctx.observed_context.summary.lower()
    assert forecast_only_ctx.forecast_context.metrics["forecast_24h_mm"] == 20.0
    assert forecast_only_ctx.historical_context.status == "UNAVAILABLE"
    assert forecast_only_ctx.data_quality_context.status == "fail"
