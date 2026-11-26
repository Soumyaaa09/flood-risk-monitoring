from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from httpx import AsyncClient, Response

from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.main import app
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualitySeverity, QualityStatus
from app.services.data_quality import (
    DataQualityValidator,
    FreshnessEvaluator,
    normalize_to_utc,
)
from app.services.ingestion_service import IngestionService
from app.services.monitoring_service import MonitoringService
from app.services.risk_service import RiskService

MOCK_LOCATIONS = [
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
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "district_id": "d3",
        "name": "Kendrapara Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.5017,
        "longitude": 86.4223,
        "districts": {"name": "Kendrapara"},
    },
]


# =============================================================================
# 1. Freshness Boundary Tests
# =============================================================================

def test_freshness_boundary_fresh():
    """Age <= 120 minutes produces FRESH."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    # Exactly 0 minutes
    state, age = FreshnessEvaluator.evaluate(now, reference_time=now)
    assert state == FreshnessState.FRESH
    assert age == 0.0

    # 60 minutes
    t_60 = now - timedelta(minutes=60)
    state, age = FreshnessEvaluator.evaluate(t_60, reference_time=now)
    assert state == FreshnessState.FRESH
    assert age == 60.0

    # Exactly 120 minutes (boundary)
    t_120 = now - timedelta(minutes=120)
    state, age = FreshnessEvaluator.evaluate(t_120, reference_time=now)
    assert state == FreshnessState.FRESH
    assert age == 120.0


def test_freshness_boundary_delayed():
    """Age > 120 and <= 360 minutes produces DELAYED."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    # 120.1 minutes
    t_just_delayed = now - timedelta(minutes=120, seconds=6)
    state, age = FreshnessEvaluator.evaluate(t_just_delayed, reference_time=now)
    assert state == FreshnessState.DELAYED
    assert age == 120.1

    # Exactly 360 minutes (boundary)
    t_360 = now - timedelta(minutes=360)
    state, age = FreshnessEvaluator.evaluate(t_360, reference_time=now)
    assert state == FreshnessState.DELAYED
    assert age == 360.0


def test_freshness_boundary_stale():
    """Age > 360 minutes produces STALE."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    # 360.5 minutes
    t_stale = now - timedelta(minutes=360, seconds=30)
    state, age = FreshnessEvaluator.evaluate(t_stale, reference_time=now)
    assert state == FreshnessState.STALE
    assert age == 360.5

    # 24 hours old
    t_day = now - timedelta(hours=24)
    state, age = FreshnessEvaluator.evaluate(t_day, reference_time=now)
    assert state == FreshnessState.STALE
    assert age == 1440.0


def test_freshness_missing_and_future_timestamps():
    """Missing or future timestamps beyond 60s clock skew produce UNAVAILABLE."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    # None / Missing
    state, age = FreshnessEvaluator.evaluate(None, reference_time=now)
    assert state == FreshnessState.UNAVAILABLE
    assert age is None

    # Future > 60 seconds (clock skew violation)
    t_future_bad = now + timedelta(seconds=61)
    state, age = FreshnessEvaluator.evaluate(t_future_bad, reference_time=now)
    assert state == FreshnessState.UNAVAILABLE
    assert age is None

    # Future within 60 seconds tolerance (clock skew permitted as fresh with age 0)
    t_future_ok = now + timedelta(seconds=30)
    state, age = FreshnessEvaluator.evaluate(t_future_ok, reference_time=now)
    assert state == FreshnessState.FRESH
    assert age == 0.0


# =============================================================================
# 2. UTC Normalization Tests
# =============================================================================

def test_utc_normalization_and_timezones():
    """Verify naive, UTC, and non-UTC timezone offsets (e.g. IST +05:30) normalize accurately."""
    # 1. Naive datetime
    naive = datetime(2026, 11, 19, 12, 0, 0)
    norm_naive = normalize_to_utc(naive)
    assert norm_naive.tzinfo == timezone.utc
    assert norm_naive.hour == 12

    # 2. Non-UTC offset (IST +05:30)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_dt = datetime(2026, 11, 19, 17, 30, 0, tzinfo=ist_tz)
    norm_ist = normalize_to_utc(ist_dt)
    assert norm_ist.tzinfo == timezone.utc
    assert norm_ist.hour == 12  # 17:30 IST is 12:00 UTC

    # 3. Freshness evaluation with non-UTC offset timestamp
    ref_utc = datetime(2026, 11, 19, 13, 0, 0, tzinfo=timezone.utc)
    state, age = FreshnessEvaluator.evaluate(ist_dt, reference_time=ref_utc)
    assert state == FreshnessState.FRESH
    assert age == 60.0  # 13:00 UTC vs 12:00 UTC = 60 minutes


# =============================================================================
# 3. Physical Observation Quality Validation Tests
# =============================================================================

def test_observation_missing_and_negative_values():
    """Verify missing, negative, and invalid values produce explicit validation errors."""
    # 1. Null / Missing
    issues = DataQualityValidator.validate_observation_value(None, measurement_type="rainfall")
    assert any(i.code == "MISSING_OBSERVATION_VALUE" for i in issues)

    # 2. Genuine zero rainfall is VALID (not an error)
    issues_zero = DataQualityValidator.validate_observation_value(0.0, measurement_type="rainfall")
    assert len(issues_zero) == 0

    # 3. Negative rainfall produces error
    issues_neg = DataQualityValidator.validate_observation_value(-2.5, measurement_type="rainfall")
    assert any(i.code == "NEGATIVE_RAINFALL_VALUE" for i in issues_neg)

    # 4. Water level can be negative (datum referenced)
    issues_stage = DataQualityValidator.validate_observation_value(-0.5, measurement_type="water_level", unit="m")
    assert len(issues_stage) == 0

    # 5. Non-finite values
    issues_nan = DataQualityValidator.validate_observation_value(float("nan"), measurement_type="rainfall")
    assert any(i.code == "NON_FINITE_OBSERVATION_VALUE" for i in issues_nan)


def test_observation_unexpected_units():
    """Unexpected physical units generate warnings."""
    # Unexpected unit for rainfall
    issues = DataQualityValidator.validate_observation_value(10.0, measurement_type="rainfall", unit="inches")
    assert any(i.code == "UNEXPECTED_MEASUREMENT_UNIT" for i in issues)

    # Correct unit
    issues_ok = DataQualityValidator.validate_observation_value(10.0, measurement_type="rainfall", unit="mm")
    assert len(issues_ok) == 0


def test_duplicate_observations_detection():
    """Duplicate observations in a batch are detected."""
    batch = [
        {"location_id": "loc-1", "data_source_id": "ds-1", "measurement_type": "rainfall", "observation_type": "analysis", "valid_at": "2026-11-19T12:00:00Z"},
        {"location_id": "loc-1", "data_source_id": "ds-1", "measurement_type": "rainfall", "observation_type": "analysis", "valid_at": "2026-11-19T12:00:00Z"},
    ]
    issues = DataQualityValidator.check_duplicate_observations(batch)
    assert len(issues) == 1
    assert issues[0].code == "DUPLICATE_OBSERVATION_IN_BATCH"


def test_forecast_horizon_validation():
    """Incomplete forecast horizons and sequence gaps trigger quality warnings."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Incomplete horizon (< 24 hours)
    short_forecasts = [
        {"valid_at": (now + timedelta(hours=i)).isoformat(), "value": 0.5}
        for i in range(1, 10)  # only 9 hours
    ]
    status, issues, meta = DataQualityValidator.validate_forecast_horizon(short_forecasts, reference_time=now, min_hours=24)
    assert status == QualityStatus.WARNING
    assert any(i.code == "INCOMPLETE_FORECAST_HORIZON" for i in issues)
    assert meta["horizon_hours"] == 9

    # 2. Chronological gap (> 1 hour between points)
    gap_forecasts = [
        {"valid_at": (now + timedelta(hours=1)).isoformat(), "value": 1.0},
        {"valid_at": (now + timedelta(hours=5)).isoformat(), "value": 1.0},  # 4-hour gap
    ]
    _, issues_gap, meta_gap = DataQualityValidator.validate_forecast_horizon(gap_forecasts, reference_time=now)
    assert any(i.code == "FORECAST_TIMESTEP_GAP" for i in issues_gap)
    assert meta_gap["gaps_detected"] == 1


def test_location_metadata_validation():
    """Invalid geographic coordinates and missing required metadata are caught."""
    # Invalid latitude
    bad_loc = {"id": "l-1", "name": "Invalid Point", "latitude": 120.0, "longitude": 86.6}
    issues = DataQualityValidator.validate_location_metadata(bad_loc)
    assert any(i.code == "INVALID_LATITUDE" for i in issues)

    # Invalid longitude
    bad_lon = {"id": "l-1", "name": "Invalid Point", "latitude": 20.0, "longitude": -200.0}
    issues_lon = DataQualityValidator.validate_location_metadata(bad_lon)
    assert any(i.code == "INVALID_LONGITUDE" for i in issues_lon)

    # Valid location
    good_loc = {"id": "l-1", "name": "Paradeep", "latitude": 20.3160, "longitude": 86.6110}
    assert len(DataQualityValidator.validate_location_metadata(good_loc)) == 0


# =============================================================================
# 4. Monitoring Service Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_monitoring_summary_and_location_health():
    """Verify get_monitoring_summary aggregates all 3 pilot locations, calculates
    freshness correctly, and identifies ingestion pipeline metrics.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS

    now_utc = datetime.now(timezone.utc)
    fresh_time = (now_utc - timedelta(minutes=30)).isoformat()
    delayed_time = (now_utc - timedelta(minutes=180)).isoformat()

    # Loc 1 (Paradeep): Fresh
    # Loc 2 (Cuttack): Delayed
    # Loc 3 (Kendrapara): Unavailable (None)
    mock_supabase.get_latest_observations_for_location.side_effect = [
        {
            "analysis": {"value": 2.5, "unit": "mm", "valid_at": fresh_time, "fetched_at": fresh_time, "data_source_id": "ds-1"},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 1.0} for i in range(1, 25)],
        },
        {
            "analysis": {"value": 0.0, "unit": "mm", "valid_at": delayed_time, "fetched_at": delayed_time, "data_source_id": "ds-1"},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.0} for i in range(1, 25)],
        },
        {"analysis": None, "forecasts": []},
    ]

    mock_supabase.get_ingestion_runs.return_value = [
        {
            "id": "run-latest-1",
            "data_source_id": "ds-1",
            "status": "completed",
            "started_at": (now_utc - timedelta(minutes=35)).isoformat(),
            "completed_at": (now_utc - timedelta(minutes=34, seconds=50)).isoformat(),
            "records_inserted": 25,
            "records_updated": 0,
        }
    ]
    mock_supabase.get_stale_started_runs.return_value = []

    service = MonitoringService(supabase_client=mock_supabase)
    summary = await service.get_monitoring_summary()

    # Verification: 3 locations evaluated
    assert len(summary.location_healths) == 3

    p_loc = summary.location_healths[0]
    assert p_loc.location_name == "Paradeep Weather Grid Point"
    assert p_loc.freshness == FreshnessState.FRESH
    assert p_loc.current_rainfall_mm == 2.5
    assert p_loc.active_forecast_hours == 24

    c_loc = summary.location_healths[1]
    assert c_loc.location_name == "Cuttack Weather Grid Point"
    assert c_loc.freshness == FreshnessState.DELAYED
    assert c_loc.current_rainfall_mm == 0.0

    k_loc = summary.location_healths[2]
    assert k_loc.location_name == "Kendrapara Weather Grid Point"
    assert k_loc.freshness == FreshnessState.UNAVAILABLE
    assert k_loc.current_rainfall_mm is None

    # Freshness summary counts
    assert summary.freshness_summary.fresh_count == 1
    assert summary.freshness_summary.delayed_count == 1
    assert summary.freshness_summary.unavailable_count == 1
    assert summary.freshness_summary.overall_state == FreshnessState.UNAVAILABLE

    # Ingestion health summary
    assert summary.ingestion_summary.recent_runs_count == 1
    assert summary.ingestion_summary.successful_runs_count == 1
    assert summary.ingestion_summary.average_duration_seconds == 10.0


# =============================================================================
# 5. Monitoring API Route & Security Tests
# =============================================================================

from app.api.v1.routes.monitoring import get_monitoring_service
from app.core.config import get_settings


@pytest.mark.asyncio
async def test_freshness_separation_stale_observation_with_recent_ingestion():
    """Verify that an old observation retrieved recently is classified as STALE observation freshness

    even though its ingestion fetch timestamp is FRESH.
    """
    now_utc = datetime.now(timezone.utc)
    stale_obs_time = (now_utc - timedelta(hours=8)).isoformat()
    recent_fetch_time = (now_utc - timedelta(minutes=5)).isoformat()

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_latest_observations_for_location.return_value = {
        "analysis": {
            "value": 1.2,
            "unit": "mm",
            "valid_at": stale_obs_time,
            "fetched_at": recent_fetch_time,
            "data_source_id": "ds-1",
        },
        "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.0} for i in range(1, 25)],
    }

    service = MonitoringService(supabase_client=mock_supabase)
    lh = await service.get_location_quality_health(MOCK_LOCATIONS[0])

    # Observation freshness must be STALE (>360m)
    assert lh.observation_freshness == FreshnessState.STALE
    assert lh.observation_age_minutes is not None
    assert lh.observation_age_minutes >= 475.0

    # Ingestion retrieval freshness must be FRESH (<=120m)
    assert lh.ingestion_freshness == FreshnessState.FRESH
    assert lh.ingestion_age_minutes is not None
    assert lh.ingestion_age_minutes <= 10.0

    # Authoritative overall freshness mirrors physical observation freshness
    assert lh.freshness == FreshnessState.STALE

    # Discrepancy warning issue must be recorded
    discrepancy_issues = [i for i in lh.quality_issues if i.code == "STALE_OBSERVATION_WITH_RECENT_INGESTION"]
    assert len(discrepancy_issues) == 1
    assert "Observation timestamp" in discrepancy_issues[0].message
    assert lh.quality_status == QualityStatus.WARNING


@pytest.mark.asyncio
async def test_freshness_separation_missing_timestamp_unavailable():
    """Verify missing observation timestamp produces UNAVAILABLE observation freshness

    even if fetched_at is present and recent.
    """
    now_utc = datetime.now(timezone.utc)
    recent_fetch_time = (now_utc - timedelta(minutes=5)).isoformat()

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_latest_observations_for_location.return_value = {
        "analysis": {
            "value": 0.5,
            "unit": "mm",
            "valid_at": None,
            "fetched_at": recent_fetch_time,
            "data_source_id": "ds-1",
        },
        "forecasts": [],
    }

    service = MonitoringService(supabase_client=mock_supabase)
    lh = await service.get_location_quality_health(MOCK_LOCATIONS[0])

    assert lh.observation_freshness == FreshnessState.UNAVAILABLE
    assert lh.observation_age_minutes is None
    assert lh.ingestion_freshness == FreshnessState.FRESH
    assert lh.freshness == FreshnessState.UNAVAILABLE
    assert lh.quality_status == QualityStatus.FAIL


@pytest.mark.asyncio
async def test_api_monitoring_summary_endpoint():
    """Verify GET /api/v1/monitoring/summary is public, returns validated Pydantic model, and leaks no secrets."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_latest_observations_for_location.return_value = {"analysis": None, "forecasts": []}
    mock_supabase.get_ingestion_runs.return_value = []
    mock_supabase.get_stale_started_runs.return_value = []

    mock_service = MonitoringService(supabase_client=mock_supabase)
    app.dependency_overrides[get_monitoring_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/monitoring/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert "system_health" in data
        assert "freshness_summary" in data
        assert "location_healths" in data
        assert len(data["location_healths"]) == 3
        # Security: No service role keys or internal tokens in response
        assert "service_role" not in str(data)
        assert "secret" not in str(data).lower()
    finally:
        app.dependency_overrides.pop(get_monitoring_service, None)


@pytest.mark.asyncio
async def test_api_monitoring_locations_endpoint():
    """Verify GET /api/v1/monitoring/locations is public and returns location-wise quality telemetry."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_latest_observations_for_location.return_value = {"analysis": None, "forecasts": []}

    mock_service = MonitoringService(supabase_client=mock_supabase)
    app.dependency_overrides[get_monitoring_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/monitoring/locations")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_locations"] == 3
        assert len(data["locations"]) == 3
        names = [l["location_name"] for l in data["locations"]]
        assert "Paradeep Weather Grid Point" in names
        assert "Cuttack Weather Grid Point" in names
        assert "Kendrapara Weather Grid Point" in names
        # Check dual freshness fields present in location schema
        first_loc = data["locations"][0]
        assert "observation_freshness" in first_loc
        assert "ingestion_freshness" in first_loc
    finally:
        app.dependency_overrides.pop(get_monitoring_service, None)


@pytest.mark.asyncio
async def test_api_monitoring_ingestion_health_security_unauthorized():
    """Verify GET /api/v1/monitoring/ingestion-health rejects unauthenticated requests with 401."""
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/monitoring/ingestion-health")

    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["error"] == "unauthorized"
    assert "X-Ingestion-Token" in detail["message"]


@pytest.mark.asyncio
async def test_api_monitoring_ingestion_health_security_forbidden():
    """Verify GET /api/v1/monitoring/ingestion-health rejects invalid tokens with 403."""
    headers = {"X-Ingestion-Token": "invalid-token-value"}
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/monitoring/ingestion-health", headers=headers)

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error"] == "forbidden"


@pytest.mark.asyncio
async def test_api_monitoring_ingestion_health_security_authorized():
    """Verify GET /api/v1/monitoring/ingestion-health succeeds with valid X-Ingestion-Token."""
    settings = get_settings()
    valid_token = settings.INGESTION_TRIGGER_TOKEN.strip()

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.base_url = "https://mock.supabase.co"
    mock_supabase.get_ingestion_runs.return_value = []
    mock_supabase.get_stale_started_runs.return_value = []

    mock_service = MonitoringService(supabase_client=mock_supabase)
    app.dependency_overrides[get_monitoring_service] = lambda: mock_service
    try:
        headers = {"X-Ingestion-Token": valid_token}
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/monitoring/ingestion-health", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "Open-Meteo" in data["provider"]
        assert "recent_runs" in data
    finally:
        app.dependency_overrides.pop(get_monitoring_service, None)


# =============================================================================
# 6. MED-02 Regression Tests: Centralized Freshness Policy Consistency
# =============================================================================

@pytest.mark.parametrize(
    "age_minutes,expected_obs_state,expected_ing_state",
    [
        (59.9, FreshnessState.FRESH, FreshnessState.FRESH),
        (60.0, FreshnessState.FRESH, FreshnessState.FRESH),
        (60.1, FreshnessState.FRESH, FreshnessState.DELAYED),
        (119.9, FreshnessState.FRESH, FreshnessState.DELAYED),
        (120.0, FreshnessState.FRESH, FreshnessState.DELAYED),
        (120.1, FreshnessState.DELAYED, FreshnessState.DELAYED),
        (179.9, FreshnessState.DELAYED, FreshnessState.DELAYED),
        (180.0, FreshnessState.DELAYED, FreshnessState.DELAYED),
        (180.1, FreshnessState.DELAYED, FreshnessState.STALE),
        (359.9, FreshnessState.DELAYED, FreshnessState.STALE),
        (360.0, FreshnessState.DELAYED, FreshnessState.STALE),
        (360.1, FreshnessState.STALE, FreshnessState.STALE),
    ],
)
def test_centralized_freshness_policy_boundaries(
    age_minutes: float,
    expected_obs_state: FreshnessState,
    expected_ing_state: FreshnessState,
):
    """MED-02: Verify exact boundary behavior for both observation (120/360) and ingestion (60/180) policies."""
    now = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
    ts = now - timedelta(minutes=age_minutes)

    obs_state, obs_age = FreshnessEvaluator.evaluate_observation(ts, reference_time=now)
    ing_state, ing_age = FreshnessEvaluator.evaluate_ingestion(ts, reference_time=now)

    assert obs_state == expected_obs_state
    assert ing_state == expected_ing_state
    assert obs_age == round(age_minutes, 1)
    assert ing_age == round(age_minutes, 1)


def test_freshness_evaluator_rejects_malformed_missing_and_future_timestamps():
    """MED-02: Missing, blank, malformed, and >60s future timestamps yield UNAVAILABLE."""
    now = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)

    for bad_val in [None, "", "   ", "not-a-valid-timestamp", 12345]:
        obs_state, obs_age = FreshnessEvaluator.evaluate_observation(bad_val, reference_time=now)
        ing_state, ing_age = FreshnessEvaluator.evaluate_ingestion(bad_val, reference_time=now)
        assert obs_state == FreshnessState.UNAVAILABLE
        assert obs_age is None
        assert ing_state == FreshnessState.UNAVAILABLE
        assert ing_age is None

    # +60s future skew tolerance allowed; +61s rejected
    ok_skew = now + timedelta(seconds=60)
    bad_skew = now + timedelta(seconds=61)
    assert FreshnessEvaluator.evaluate_observation(ok_skew, reference_time=now) == (FreshnessState.FRESH, 0.0)
    assert FreshnessEvaluator.evaluate_ingestion(ok_skew, reference_time=now) == (FreshnessState.FRESH, 0.0)
    assert FreshnessEvaluator.evaluate_observation(bad_skew, reference_time=now) == (FreshnessState.UNAVAILABLE, None)
    assert FreshnessEvaluator.evaluate_ingestion(bad_skew, reference_time=now) == (FreshnessState.UNAVAILABLE, None)


@pytest.mark.asyncio
async def test_freshness_consistency_across_ingestion_monitoring_and_risk_services():
    """MED-02: Verify MonitoringService, RiskService, and IngestionService agree on observation vs ingestion freshness."""
    now_utc = datetime.now(timezone.utc)
    # valid_at is 90m old (FRESH under observation policy <=120m)
    # fetched_at is 90m old (DELAYED under ingestion policy 60<90<=180m)
    ts_90m = (now_utc - timedelta(minutes=90)).isoformat()
    forecasts = [
        {"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.0}
        for i in range(1, 25)
    ]
    sample_obs = {
        "analysis": {
            "id": "obs-1",
            "value": 0.0,
            "unit": "mm",
            "valid_at": ts_90m,
            "fetched_at": ts_90m,
            "data_source_id": "ds-1",
        },
        "forecasts": forecasts,
    }

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = [MOCK_LOCATIONS[0]]
    mock_sb.get_latest_observations_for_location.return_value = sample_obs
    mock_sb.get_historical_observations_for_location.return_value = []

    mon_svc = MonitoringService(supabase_client=mock_sb)
    risk_svc = RiskService(supabase_client=mock_sb)
    ing_svc = IngestionService(supabase_client=mock_sb)

    loc_health = await mon_svc.get_location_quality_health(MOCK_LOCATIONS[0])
    risk_res = await risk_svc.get_location_rainfall_risk(MOCK_LOCATIONS[0]["id"], now_time=now_utc)
    latest_weather = await ing_svc.get_latest_weather_for_location(MOCK_LOCATIONS[0]["id"])

    # Observation freshness (valid_at) agrees across MonitoringService & RiskService
    assert loc_health.observation_freshness == FreshnessState.FRESH
    assert risk_res is not None
    assert risk_res.observation_freshness == FreshnessState.FRESH

    # Ingestion freshness (fetched_at) agrees across MonitoringService & IngestionService
    assert loc_health.ingestion_freshness == FreshnessState.DELAYED
    assert latest_weather.freshness == FreshnessState.DELAYED



# =============================================================================
# 7. MED-05A: Fault-Tolerant Forecast & Ingestion Timestamp Parsing
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_ts,should_count_24h,expected_quality_fail",
    [
        ((datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), True, False),
        ((datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(), True, False),
        ((datetime.now(timezone.utc) + timedelta(hours=3)).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(), True, False),
        ((datetime.now(timezone.utc) + timedelta(hours=3)).astimezone(timezone(timedelta(hours=-5))).isoformat(), True, False),
        (None, False, True),
        ("", False, True),
        ("not-a-timestamp", False, True),
        (12345, False, True),
    ],
)
async def test_monitoring_handles_diverse_forecast_timestamps_without_crashing(
    raw_ts, should_count_24h, expected_quality_fail
):
    """Verify valid UTC/offset and malformed/missing/non-string forecast timestamps never crash MonitoringService."""
    now_utc = datetime.now(timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=15)).isoformat()

    forecasts = [
        {"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 1.0}
        for i in range(1, 25)
    ]
    forecasts.append({"valid_at": raw_ts, "value": 5.0})

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = [MOCK_LOCATIONS[0]]
    mock_sb.get_latest_observations_for_location.return_value = {
        "analysis": {
            "id": "obs-med05",
            "value": 2.0,
            "unit": "mm",
            "valid_at": fresh_iso,
            "fetched_at": fresh_iso,
            "data_source_id": "ds-1",
        },
        "forecasts": forecasts,
    }

    service = MonitoringService(supabase_client=mock_sb)
    health = await service.get_location_quality_health(MOCK_LOCATIONS[0])

    if should_count_24h:
        assert health.next_24h_precipitation_mm == 29.0
    else:
        assert health.next_24h_precipitation_mm == 24.0

    if expected_quality_fail:
        assert health.quality_status == QualityStatus.FAIL
    else:
        assert health.quality_status == QualityStatus.PASS


@pytest.mark.asyncio
async def test_monitoring_mixed_and_all_invalid_forecasts_never_report_healthy_and_survive_bad_run_ts(caplog):
    """Mixed/all-invalid forecast records classify FAIL (never PASS/0mm), and malformed run timestamps do not crash."""
    now_utc = datetime.now(timezone.utc)
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = [MOCK_LOCATIONS[0]]
    mock_sb.get_latest_observations_for_location.return_value = {
        "analysis": None,
        "forecasts": [
            {"valid_at": "garbage-iso", "value": 10.0},
            {"valid_at": "", "value": 5.0},
            {"valid_at": None, "value": 2.0},
            {"valid_at": 987654, "value": 1.0},
        ],
    }
    mock_sb.get_ingestion_runs.return_value = [
        {
            "id": "run-bad-ts",
            "status": "completed",
            "started_at": "invalid-run-start",
            "completed_at": "invalid-run-end",
            "records_inserted": 0,
            "records_updated": 0,
        }
    ]
    mock_sb.get_stale_started_runs.return_value = []

    service = MonitoringService(supabase_client=mock_sb)
    with caplog.at_level("WARNING"):
        summary = await service.get_monitoring_summary()

    loc_health = summary.location_healths[0]
    assert loc_health.observation_freshness == FreshnessState.UNAVAILABLE
    assert loc_health.quality_status == QualityStatus.FAIL
    assert loc_health.next_24h_precipitation_mm is None
    assert summary.system_health == "degraded"
    assert summary.ingestion_summary.latest_attempt_at is None
    assert "Skipped 4 forecast record(s)" in caplog.text
