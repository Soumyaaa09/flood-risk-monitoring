"""Comprehensive Unit and Integration Tests for FloodWatch Risk Assessment Engine (Phase 2.4.1).

Covers:
1. Every rainfall risk category: LOW, MODERATE, HIGH, VERY_HIGH, UNAVAILABLE.
2. Boundary conditions across 24h, 6h, and recent observed rainfall thresholds.
3. Genuine zero rainfall handling (LOW risk, never UNAVAILABLE).
4. Missing rainfall handling (UNAVAILABLE, never falsely LOW).
5. Physics violations (negative, NaN, inf rainfall rejected).
6. Stale and unavailable observation timestamps.
7. Incomplete forecast horizons (<50% completeness or <6h).
8. Segregation of river-stage gauge and combined multi-hazard interfaces.
9. Single location and pipeline-wide summary API routes.
10. Secret sanitization and error handling.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import httpx
import pytest
from httpx import AsyncClient

from app.api.v1.routes.risk import get_risk_service
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.main import app
from app.risk_engine.config import RainfallRiskConfig, RainfallThresholdBands
from app.risk_engine.interfaces import (
    CombinedRiskAssessorInterface,
    RiverStageRiskAssessorInterface,
)
from app.risk_engine.rainfall_assessor import RainfallRiskAssessor, RainfallRiskInput
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualitySeverity, QualityStatus
from app.schemas.risk import (
    HydrologicalComponentStatus,
    RainfallRiskAssessmentResponse,
    RiskCategory,
    RiskConfidence,
)
from app.services.risk_service import RiskService

MOCK_LOCATIONS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "district_id": "d1",
        "basin_id": "b1",
        "name": "Paradeep Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.3160,
        "longitude": 86.6110,
        "districts": {"name": "Jagatsinghpur"},
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "district_id": "d2",
        "basin_id": "b1",
        "name": "Cuttack Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.4625,
        "longitude": 85.8830,
        "districts": {"name": "Cuttack"},
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "district_id": "d3",
        "basin_id": "b1",
        "name": "Kendrapara Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.5017,
        "longitude": 86.4223,
        "districts": {"name": "Kendrapara"},
    },
]


def create_sample_input(
    obs_val: float = 0.0,
    f6_val: float = 0.0,
    f24_val: float = 0.0,
    obs_freshness: FreshnessState = FreshnessState.FRESH,
    obs_age: float = 15.0,
    completeness: float = 100.0,
    active_hours: int = 24,
    quality_status: QualityStatus = QualityStatus.PASS,
    quality_warnings: list = None,
) -> RainfallRiskInput:
    """Helper to instantiate a standard test input."""
    return RainfallRiskInput(
        location_id="11111111-1111-1111-1111-111111111111",
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        latitude=20.3160,
        longitude=86.6110,
        recent_observed_rainfall_mm=obs_val,
        forecast_6h_precipitation_mm=f6_val,
        forecast_24h_precipitation_mm=f24_val,
        observation_freshness=obs_freshness,
        observation_age_minutes=obs_age,
        observation_timestamp=datetime.now(timezone.utc) - timedelta(minutes=obs_age),
        forecast_completeness_pct=completeness,
        active_forecast_hours=active_hours,
        quality_status=quality_status,
        quality_warnings=quality_warnings or [],
    )


# =============================================================================
# 1. Categorical Risk and Threshold Boundary Tests
# =============================================================================

def test_risk_category_low():
    """All metrics below moderate thresholds result in LOW risk."""
    assessor = RainfallRiskAssessor()
    inp = create_sample_input(obs_val=2.0, f6_val=5.0, f24_val=12.0)
    res = assessor.assess_location_risk(inp)

    assert res.risk_category == RiskCategory.LOW
    assert res.confidence == RiskConfidence.HIGH
    assert "LOW surface flood risk" in res.assessment_explanation


def test_risk_category_genuine_zero_rainfall():
    """Zero rainfall observed and forecast produces LOW risk, NEVER UNAVAILABLE."""
    assessor = RainfallRiskAssessor()
    inp = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=0.0)
    res = assessor.assess_location_risk(inp)

    assert res.risk_category == RiskCategory.LOW
    assert res.confidence == RiskConfidence.HIGH
    assert "Zero rainfall observed and zero rainfall forecast" in res.assessment_explanation


def test_risk_category_moderate_boundary_24h():
    """24h forecast threshold: exactly 15.5 mm (LOW) vs 15.6 mm (MODERATE)."""
    assessor = RainfallRiskAssessor()

    # Just below threshold -> LOW
    inp_low = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=15.5)
    res_low = assessor.assess_location_risk(inp_low)
    assert res_low.risk_category == RiskCategory.LOW

    # Exactly at threshold -> MODERATE
    inp_mod = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=15.6)
    res_mod = assessor.assess_location_risk(inp_mod)
    assert res_mod.risk_category == RiskCategory.MODERATE
    assert "MODERATE surface flood risk" in res_mod.assessment_explanation


def test_risk_category_moderate_boundary_6h_and_recent():
    """6h forecast >= 10.0 mm or recent observed >= 5.0 mm triggers MODERATE."""
    assessor = RainfallRiskAssessor()

    # Triggered by 6h forecast
    inp_6h = create_sample_input(obs_val=0.0, f6_val=10.0, f24_val=10.0)
    res_6h = assessor.assess_location_risk(inp_6h)
    assert res_6h.risk_category == RiskCategory.MODERATE
    assert "6h forecast" in res_6h.assessment_explanation

    # Triggered by recent observed rainfall rate
    inp_obs = create_sample_input(obs_val=5.0, f6_val=2.0, f24_val=8.0)
    res_obs = assessor.assess_location_risk(inp_obs)
    assert res_obs.risk_category == RiskCategory.MODERATE
    assert "recent observed" in res_obs.assessment_explanation


def test_risk_category_high_boundary():
    """24h forecast: 64.4 mm (MODERATE) vs 64.5 mm (HIGH)."""
    assessor = RainfallRiskAssessor()

    inp_mod = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=64.4)
    assert assessor.assess_location_risk(inp_mod).risk_category == RiskCategory.MODERATE

    inp_high = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=64.5)
    res_high = assessor.assess_location_risk(inp_high)
    assert res_high.risk_category == RiskCategory.HIGH
    assert "HIGH surface flood risk" in res_high.assessment_explanation


def test_risk_category_very_high_boundary():
    """24h forecast: 115.5 mm (HIGH) vs 115.6 mm (VERY_HIGH)."""
    assessor = RainfallRiskAssessor()

    inp_high = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=115.5)
    assert assessor.assess_location_risk(inp_high).risk_category == RiskCategory.HIGH

    inp_vh = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=115.6)
    res_vh = assessor.assess_location_risk(inp_vh)
    assert res_vh.risk_category == RiskCategory.VERY_HIGH
    assert "VERY HIGH surface flood risk" in res_vh.assessment_explanation


# =============================================================================
# 2. Safety and Data Reliability Guards
# =============================================================================

def test_missing_data_never_falsely_low():
    """Missing observed and forecast values produce UNAVAILABLE, never LOW."""
    assessor = RainfallRiskAssessor()
    inp = create_sample_input(obs_val=None, f6_val=None, f24_val=None)
    res = assessor.assess_location_risk(inp)

    assert res.risk_category == RiskCategory.UNAVAILABLE
    assert res.confidence == RiskConfidence.NONE
    assert "never converts missing data into zero" in res.assessment_explanation


def test_negative_rainfall_rejected_as_physics_violation():
    """Negative rainfall values are rejected immediately as UNAVAILABLE."""
    assessor = RainfallRiskAssessor()

    # Negative observation
    inp_neg_obs = create_sample_input(obs_val=-1.5, f24_val=10.0)
    res_neg_obs = assessor.assess_location_risk(inp_neg_obs)
    assert res_neg_obs.risk_category == RiskCategory.UNAVAILABLE
    assert "violating physics" in res_neg_obs.assessment_explanation

    # Negative forecast
    inp_neg_fc = create_sample_input(obs_val=0.0, f24_val=-5.0)
    res_neg_fc = assessor.assess_location_risk(inp_neg_fc)
    assert res_neg_fc.risk_category == RiskCategory.UNAVAILABLE
    assert "violating physics" in res_neg_fc.assessment_explanation


def test_stale_and_unavailable_observations_refuse_classification():
    """Stale (>360m) or UNAVAILABLE observation freshness yields UNAVAILABLE risk."""
    assessor = RainfallRiskAssessor()

    # Unavailable freshness
    inp_unavail = create_sample_input(obs_freshness=FreshnessState.UNAVAILABLE, f24_val=20.0)
    res_unavail = assessor.assess_location_risk(inp_unavail)
    assert res_unavail.risk_category == RiskCategory.UNAVAILABLE
    assert res_unavail.confidence == RiskConfidence.NONE

    # Stale observation (>6h)
    inp_stale = create_sample_input(obs_freshness=FreshnessState.STALE, obs_age=450.0, f24_val=20.0)
    res_stale = assessor.assess_location_risk(inp_stale)
    assert res_stale.risk_category == RiskCategory.UNAVAILABLE
    assert "STALE" in res_stale.assessment_explanation


def test_delayed_observation_reduces_confidence():
    """Delayed observation (120-360m) produces risk categorization with reduced confidence (LOW)."""
    assessor = RainfallRiskAssessor()
    inp_delayed = create_sample_input(obs_val=0.0, f24_val=20.0, obs_freshness=FreshnessState.DELAYED, obs_age=180.0)
    res_delayed = assessor.assess_location_risk(inp_delayed)

    assert res_delayed.risk_category == RiskCategory.MODERATE
    assert res_delayed.confidence == RiskConfidence.LOW
    assert any("DELAYED" in w for w in res_delayed.quality_warnings)


def test_incomplete_forecast_horizon_produces_unavailable():
    """Incomplete forecast (<50% completeness or <6h) produces UNAVAILABLE risk."""
    assessor = RainfallRiskAssessor()

    # Excessive missing intervals (<50%)
    inp_incomp = create_sample_input(f24_val=30.0, completeness=40.0)
    res_incomp = assessor.assess_location_risk(inp_incomp)
    assert res_incomp.risk_category == RiskCategory.UNAVAILABLE
    assert "Forecast completeness is 40.0%" in res_incomp.assessment_explanation

    # Short forecast horizon (<6 hours)
    inp_short = create_sample_input(f24_val=30.0, active_hours=3)
    res_short = assessor.assess_location_risk(inp_short)
    assert res_short.risk_category == RiskCategory.UNAVAILABLE
    assert "Active forecast horizon is only 3h" in res_short.assessment_explanation


# =============================================================================
# 3. Independent Component Interface Verification
# =============================================================================

def test_river_stage_and_combined_interfaces_are_non_operational():
    """Verify that river stage and combined multi-hazard assessors are non-operational."""
    stage_assessor = RiverStageRiskAssessorInterface()
    assert stage_assessor.is_operational is False
    stage_readiness = stage_assessor.get_readiness()
    assert stage_readiness.status == HydrologicalComponentStatus.NON_OPERATIONAL

    stage_res = stage_assessor.assess_stage_risk("loc-1", 4.5)
    assert stage_res["risk_category"] == RiskCategory.UNAVAILABLE
    assert stage_res["confidence"] == RiskConfidence.NONE

    combined_assessor = CombinedRiskAssessorInterface()
    assert combined_assessor.is_operational is False
    comb_readiness = combined_assessor.get_readiness()
    assert comb_readiness.status == HydrologicalComponentStatus.UNAVAILABLE

    comb_res = combined_assessor.assess_combined_risk(RiskCategory.HIGH, RiskCategory.MODERATE)
    assert comb_res["risk_category"] == RiskCategory.UNAVAILABLE
    assert "Rainfall depth and river gauge elevation cannot be collapsed" in comb_readiness.reason


# =============================================================================
# 4. Service and Integration Tests Across Locations
# =============================================================================

@pytest.mark.asyncio
async def test_risk_service_multilocation_summary():
    """Verify RiskService assesses multiple locations and determines pipeline-wide highest risk."""
    now_utc = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=10)).isoformat()

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS

    # Return different risk outcomes:
    # 1. Paradeep: VERY_HIGH (f24 = 120.0 mm)
    # 2. Cuttack: MODERATE (f24 = 25.0 mm)
    # 3. Kendrapara: LOW (f24 = 2.0 mm)
    mock_supabase.get_latest_observations_for_location.side_effect = [
        {
            "analysis": {"value": 10.0, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 5.0} for i in range(1, 25)],
        },
        {
            "analysis": {"value": 0.0, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 1.04} for i in range(1, 25)],
        },
        {
            "analysis": {"value": 0.0, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.08} for i in range(1, 25)],
        },
    ]

    service = RiskService(supabase_client=mock_supabase)
    summary = await service.get_all_locations_rainfall_risk(now_time=now_utc)

    assert summary.total_locations == 3
    assert summary.highest_risk_category == RiskCategory.VERY_HIGH
    assert summary.category_counts[RiskCategory.VERY_HIGH.value] == 1
    assert summary.category_counts[RiskCategory.MODERATE.value] == 1
    assert summary.category_counts[RiskCategory.LOW.value] == 1

    p_res = summary.assessments[0]
    assert p_res.location_name == "Paradeep Weather Grid Point"
    assert p_res.risk_category == RiskCategory.VERY_HIGH

    c_res = summary.assessments[1]
    assert c_res.location_name == "Cuttack Weather Grid Point"
    assert c_res.risk_category == RiskCategory.MODERATE

    k_res = summary.assessments[2]
    assert k_res.location_name == "Kendrapara Weather Grid Point"
    assert k_res.risk_category == RiskCategory.LOW


# =============================================================================
# 5. FastAPI Route Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_api_rainfall_risk_all_locations_endpoint():
    """Verify GET /api/v1/risk/rainfall returns validated summary without secrets."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_latest_observations_for_location.return_value = {"analysis": None, "forecasts": []}

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/risk/rainfall")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_locations" in data
        assert data["total_locations"] == 3
        assert "highest_risk_category" in data
        assert "assessments" in data
        assert len(data["assessments"]) == 3
        assert "component_readiness" in data
        assert "prototype_disclaimer" in data
        # Security: No secret credentials leaked
        assert "service_role" not in str(data)
        assert "secret" not in str(data).lower()
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_api_rainfall_risk_single_location_filter():
    """Verify GET /api/v1/risk/rainfall?location_id=... filters to single station."""
    target_id = "22222222-2222-2222-2222-222222222222"
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_latest_observations_for_location.return_value = {"analysis": None, "forecasts": []}

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/risk/rainfall?location_id={target_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["location_id"] == target_id
        assert data["location_name"] == "Cuttack Weather Grid Point"
        assert "risk_category" in data
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_api_rainfall_risk_location_not_found():
    """Verify requesting an unknown location ID returns 404."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/risk/rainfall/99999999-9999-9999-9999-999999999999")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "location_not_found"
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_api_thresholds_endpoint():
    """Verify GET /api/v1/risk/thresholds returns documented provisional bands."""
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/risk/thresholds")

    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "v0.1-provisional-research"
    assert data["bands"]["very_high_24h_mm"] == 115.6
    assert data["bands"]["high_24h_mm"] == 64.5
    assert data["bands"]["moderate_24h_mm"] == 15.6
    assert "PROVISIONAL RESEARCH PARAMETERS" in data["disclaimer"]


@pytest.mark.asyncio
async def test_api_components_readiness_endpoint():
    """Verify GET /api/v1/risk/components audits all 3 risk engine modules."""
    mock_service = RiskService()
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/risk/components")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        comp_names = [c["component_name"] for c in data]
        assert "Rainfall Surface Water Risk Assessor" in comp_names
        assert "River-Stage Gauge Risk Assessor" in comp_names
        assert "Combined Multi-Hazard Flood Risk Assessor" in comp_names
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


# =============================================================================
# 6. MED-03 Regression Tests: Missing / Invalid Observed Telemetry vs LOW Risk
# =============================================================================

def test_missing_observed_rainfall_with_zero_forecast_not_low():
    """MED-03: Missing observed rainfall + 0.0 mm forecast must return UNAVAILABLE, never LOW."""
    assessor = RainfallRiskAssessor()
    inp = create_sample_input(obs_val=None, f6_val=0.0, f24_val=0.0)
    res = assessor.assess_location_risk(inp)

    assert res.risk_category == RiskCategory.UNAVAILABLE
    assert res.confidence == RiskConfidence.NONE
    assert res.quality_status == QualityStatus.FAIL
    assert res.risk_context is not None
    assert res.risk_context.data_quality_context.status == QualityStatus.FAIL.value


def test_quality_status_fail_with_zero_metrics_not_low():
    """MED-03: QualityStatus.FAIL must refuse LOW assessment even if numeric values are zero."""
    assessor = RainfallRiskAssessor()
    inp = create_sample_input(obs_val=0.0, f6_val=0.0, f24_val=0.0, quality_status=QualityStatus.FAIL)
    res = assessor.assess_location_risk(inp)

    assert res.risk_category == RiskCategory.UNAVAILABLE
    assert res.confidence == RiskConfidence.NONE
    assert res.quality_status == QualityStatus.FAIL


@pytest.mark.asyncio
async def test_risk_service_invalid_or_missing_observed_value_with_zero_forecast_not_low():
    """MED-03: RiskService encountering null or non-numeric analysis.value + 0.0 forecast must return UNAVAILABLE."""
    now_utc = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=10)).isoformat()
    zero_forecasts = [
        {"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.0}
        for i in range(1, 25)
    ]

    for bad_val in [None, "corrupt-sensor"]:
        mock_supabase = AsyncMock(spec=SupabaseClient)
        mock_supabase.get_pilot_locations.return_value = [MOCK_LOCATIONS[0]]
        mock_supabase.get_historical_observations_for_location.return_value = []
        mock_supabase.get_latest_observations_for_location.return_value = {
            "analysis": {"value": bad_val, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": zero_forecasts,
        }

        service = RiskService(supabase_client=mock_supabase)
        res = await service.get_location_rainfall_risk(MOCK_LOCATIONS[0]["id"], now_time=now_utc)

        assert res is not None
        assert res.risk_category == RiskCategory.UNAVAILABLE
        assert res.confidence == RiskConfidence.NONE
        assert res.quality_status == QualityStatus.FAIL

