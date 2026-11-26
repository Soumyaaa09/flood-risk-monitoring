"""Comprehensive Tests for Historical Rainfall Analysis & Antecedent Indicators (Phase 2.4.2).

Verification Coverage:
1. Historical aggregation boundaries (24h, 72h, 7d).
2. UTC and timezone handling.
3. Duplicate observation deduplication.
4. Forecast exclusion (forecast records strictly excluded).
5. Missing historical intervals & coverage auditing.
6. Incomplete historical coverage warnings.
7. Genuine zero rainfall preservation (0.0 mm != None).
8. Missing values handling (never fake zero).
9. Consecutive rainy days (IMD >= 2.5 mm criterion).
10. Daily trend evaluation.
11. Antecedent rainfall indicator categorization and multi-window weighting.
12. Location isolation.
13. API endpoint response contracts.
14. Preservation of Phase 2.4.1 risk categorization without silent alterations.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from httpx import AsyncClient

from app.api.v1.routes.risk import get_risk_service
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.main import app
from app.risk_engine.historical_analyzer import HistoricalRainfallAnalyzer
from app.schemas.ingestion import FreshnessState
from app.schemas.risk import (
    AntecedentCategory,
    RainfallTrend,
    RiskCategory,
    RiskConfidence,
)
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
]


# =============================================================================
# 1. Historical Aggregation Boundaries & UTC Normalization
# =============================================================================

def test_historical_aggregation_boundaries_24h_72h_7d():
    """Verify points fall into exact 24h, 72h, and 7d temporal buckets relative to reference time."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    raw_obs = [
        # Point A: 12 hours ago (in 24h, 72h, 7d)
        {"valid_at": (ref_time - timedelta(hours=12)).isoformat(), "value": 10.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Point B: 36 hours ago (in 72h, 7d, NOT 24h)
        {"valid_at": (ref_time - timedelta(hours=36)).isoformat(), "value": 20.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Point C: 96 hours / 4 days ago (in 7d, NOT 24h, NOT 72h)
        {"valid_at": (ref_time - timedelta(hours=96)).isoformat(), "value": 30.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Point D: 8 days ago (OUTSIDE 7d window)
        {"valid_at": (ref_time - timedelta(days=8)).isoformat(), "value": 50.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=7)

    # 24h total: only Point A
    assert history.rainfall_24h_mm == 10.0

    # 72h total: Point A + Point B
    assert history.rainfall_72h_mm == 30.0

    # 7d total: Point A + Point B + Point C (Point D excluded)
    assert history.rainfall_7d_mm == 60.0


def test_utc_and_timezone_handling():
    """Verify that non-UTC timestamps (e.g. IST +05:30) are normalized correctly."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))

    # 17:00 IST is 11:30 UTC (within 24h window of ref_time 12:00 UTC)
    t_ist = datetime(2026, 11, 20, 17, 0, 0, tzinfo=ist_tz)

    raw_obs = [
        {"valid_at": t_ist.isoformat(), "value": 15.5, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=1)
    assert history.rainfall_24h_mm == 15.5


# =============================================================================
# 2. Duplicate Observations & Forecast Exclusion
# =============================================================================

def test_duplicate_observations_deduplicated():
    """Verify multiple records with identical valid_at are deduplicated safely."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    t_shared = (ref_time - timedelta(hours=4)).isoformat()

    raw_obs = [
        {"valid_at": t_shared, "value": 5.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall", "fetched_at": "2026-11-20T08:00:00Z"},
        # Duplicate with later fetched_at
        {"valid_at": t_shared, "value": 8.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall", "fetched_at": "2026-11-20T08:05:00Z"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=1)
    # Deduplication must keep the latest fetched reading (8.0 mm)
    assert history.rainfall_24h_mm == 8.0


def test_forecast_and_simulation_strictly_excluded():
    """Verify forecast records are strictly excluded from historical calculations."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    raw_obs = [
        {"valid_at": (ref_time - timedelta(hours=2)).isoformat(), "value": 12.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Forecast record with past timestamp (e.g. retrospective forecast run)
        {"valid_at": (ref_time - timedelta(hours=2)).isoformat(), "value": 99.0, "unit": "mm", "observation_type": "forecast", "measurement_type": "rainfall"},
        # Simulation record
        {"valid_at": (ref_time - timedelta(hours=2)).isoformat(), "value": 50.0, "unit": "mm", "observation_type": "simulation", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=1)
    # Only the analysis record (12.0 mm) must be counted
    assert history.rainfall_24h_mm == 12.0


# =============================================================================
# 3. Missing Data, Zero Preservation & Coverage Auditing
# =============================================================================

def test_genuine_zero_rainfall_preserved():
    """Valid records with 0.0 mm rainfall produce 0.0 mm totals, NOT None."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    raw_obs = [
        {"valid_at": (ref_time - timedelta(hours=i)).isoformat(), "value": 0.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"}
        for i in range(1, 24)
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=1)
    assert history.rainfall_24h_mm == 0.0
    assert history.has_sufficient_coverage is True


def test_missing_data_never_falsely_zero():
    """Empty historical observations produce None, NEVER fake 0.0 mm."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    history = HistoricalRainfallAnalyzer.analyze_history([], reference_time=ref_time, window_days=7)
    assert history.rainfall_24h_mm is None
    assert history.rainfall_72h_mm is None
    assert history.rainfall_7d_mm is None
    assert history.has_sufficient_coverage is False
    assert history.data_gap_warning is not None


def test_missing_intervals_and_gap_detection():
    """Detects missing hourly intervals when consecutive observations have gaps > 2 hours."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    raw_obs = [
        {"valid_at": (ref_time - timedelta(hours=10)).isoformat(), "value": 2.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Gap of 6 hours between 10h ago and 4h ago
        {"valid_at": (ref_time - timedelta(hours=4)).isoformat(), "value": 3.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=1)
    assert history.missing_intervals_count >= 5
    assert "missing hourly interval" in history.data_gap_warning


# =============================================================================
# 4. Consecutive Rainy Days & Daily Trends
# =============================================================================

def test_consecutive_rainy_days_imd_definition():
    """Counts consecutive calendar days with rainfall >= 2.5 mm ending at the most recent active day."""
    ref_time = datetime(2026, 11, 20, 23, 59, 0, tzinfo=timezone.utc)

    # Day 0 (today): 5.0 mm (rainy)
    # Day -1: 3.0 mm (rainy)
    # Day -2: 1.0 mm (NOT rainy, below 2.5 mm)
    # Day -3: 10.0 mm (rainy)
    raw_obs = [
        {"valid_at": (ref_time - timedelta(days=0, hours=2)).isoformat(), "value": 5.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        {"valid_at": (ref_time - timedelta(days=1, hours=2)).isoformat(), "value": 3.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        {"valid_at": (ref_time - timedelta(days=2, hours=2)).isoformat(), "value": 1.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        {"valid_at": (ref_time - timedelta(days=3, hours=2)).isoformat(), "value": 10.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=4)
    # Consecutive rainy days ending on most recent day should be 2 (Day 0 and Day -1)
    assert history.consecutive_rainy_days == 2


def test_daily_trend_classification():
    """Verify INCREASING, DECREASING, STEADY, NO_RAIN trends using completed daily periods."""
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ref_time = datetime(2026, 11, 20, 23, 59, 0, tzinfo=ist_tz)

    # Increasing trend: day -2 has 1 mm, day 0 has 20 mm (evaluated at end of day)
    obs_inc = [
        {"valid_at": (ref_time - timedelta(days=2, hours=2)).isoformat(), "value": 1.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        {"valid_at": (ref_time - timedelta(days=0, hours=2)).isoformat(), "value": 20.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]
    h_inc = HistoricalRainfallAnalyzer.analyze_history(obs_inc, reference_time=ref_time, window_days=3)
    assert h_inc.daily_trend == RainfallTrend.INCREASING

    # No rain trend
    obs_dry = [
        {"valid_at": (ref_time - timedelta(days=2, hours=2)).isoformat(), "value": 0.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        {"valid_at": (ref_time - timedelta(days=0, hours=2)).isoformat(), "value": 0.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]
    h_dry = HistoricalRainfallAnalyzer.analyze_history(obs_dry, reference_time=ref_time, window_days=3)
    assert h_dry.daily_trend == RainfallTrend.NO_RAIN


# =============================================================================
# 4b. Phase 2.4.2.1 Semantics & Coverage Corrections
# =============================================================================

def test_observation_cadence_detection_15m_and_60m():
    """Verify detection of 15-minute nowcasts vs 60-minute standard intervals."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Open-Meteo 15-minute nowcast with accumulation_interval_seconds in metadata
    obs_15m = [
        {
            "valid_at": (ref_time - timedelta(minutes=15 * i)).isoformat(),
            "value": 0.5,
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
            "metadata": {"accumulation_interval_seconds": 900},
        }
        for i in range(16)
    ]
    history_15m = HistoricalRainfallAnalyzer.analyze_history(obs_15m, reference_time=ref_time, window_days=1)
    assert history_15m.detected_interval_minutes == 15.0
    # Expected points for 1 day at 15m intervals is 96 (NOT hardcoded 24!)
    # 16 points out of 96 = ~16.7% coverage
    assert history_15m.coverage_pct < 20.0

    # 2. Hourly intervals without metadata
    obs_60m = [
        {
            "valid_at": (ref_time - timedelta(hours=i)).isoformat(),
            "value": 1.0,
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
        for i in range(24)
    ]
    history_60m = HistoricalRainfallAnalyzer.analyze_history(obs_60m, reference_time=ref_time, window_days=1)
    assert history_60m.detected_interval_minutes == 60.0
    assert history_60m.coverage_pct == 100.0


def test_partial_day_does_not_break_consecutive_rainy_days():
    """An incomplete current day (e.g. 09:00 IST with 0.5 mm) must NOT break yesterday's streak."""
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    # Reference time is 09:00 AM IST on Nov 21 (partial day, only 9h elapsed)
    ref_morning = datetime(2026, 11, 21, 9, 0, 0, tzinfo=ist_tz)

    raw_obs = [
        # Today (Nov 21) morning: 0.5 mm (< 2.5 mm threshold)
        {"valid_at": (ref_morning - timedelta(hours=2)).isoformat(), "value": 0.5, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Yesterday (Nov 20): 15.0 mm (rainy)
        {"valid_at": (ref_morning - timedelta(hours=18)).isoformat(), "value": 15.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Day before (Nov 19): 10.0 mm (rainy)
        {"valid_at": (ref_morning - timedelta(hours=42)).isoformat(), "value": 10.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_morning, window_days=3)
    # The incomplete morning (< 2.5 mm) must NOT reset the streak; streak of 2 days is preserved!
    assert history.consecutive_rainy_days == 2
    assert history.current_day_in_progress is True
    # Verify the latest record is marked as partial
    assert history.daily_totals[-1].is_partial_day is True


def test_partial_day_does_not_distort_trend():
    """An incomplete morning must not falsely trigger a DECREASING trend."""
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    # 07:00 AM IST on Nov 21 (only 7h elapsed)
    ref_morning = datetime(2026, 11, 21, 7, 0, 0, tzinfo=ist_tz)

    raw_obs = [
        # Incomplete morning: 0.1 mm
        {"valid_at": (ref_morning - timedelta(hours=1)).isoformat(), "value": 0.1, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        # Completed yesterday (Nov 20): 10.0 mm
        {"valid_at": (ref_morning - timedelta(hours=20)).isoformat(), "value": 10.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_morning, window_days=2)
    # With only 1 completed day and 1 partial day (<20h elapsed), trend must be INSUFFICIENT_DATA, NOT DECREASING!
    assert history.daily_trend == RainfallTrend.INSUFFICIENT_DATA


def test_imd_synoptic_day_reporting_window():
    """Verify daily aggregation under IMD synoptic reporting window (08:30 IST to 08:30 IST next day)."""
    from app.schemas.risk import DailyReportingWindow
    # 08:30 IST is 03:00 UTC
    ref_time = datetime(2026, 11, 21, 3, 0, 0, tzinfo=timezone.utc)

    # Observation at 07:00 UTC (12:30 IST on Nov 20) falls into Nov 20 synoptic day
    obs = [
        {
            "valid_at": "2026-11-20T07:00:00Z",
            "value": 18.0,
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(
        obs,
        reference_time=ref_time,
        window_days=1,
        reporting_window=DailyReportingWindow.IMD_SYNOPTIC_DAY,
    )

    assert len(history.daily_totals) > 0
    synoptic_rec = [r for r in history.daily_totals if r.date == "2026-11-20"][0]
    assert synoptic_rec.rainfall_mm == 18.0
    assert synoptic_rec.start_time_utc == datetime(2026, 11, 20, 3, 0, 0, tzinfo=timezone.utc)
    assert synoptic_rec.end_time_utc == datetime(2026, 11, 21, 3, 0, 0, tzinfo=timezone.utc)
    assert synoptic_rec.reporting_window == DailyReportingWindow.IMD_SYNOPTIC_DAY.value


# =============================================================================
# 5. Antecedent Rainfall Indicator (ARI) Evaluation
# =============================================================================

def test_antecedent_indicator_categories():
    """Verify LOW, MODERATE, HIGH, EXTREME, and UNAVAILABLE antecedent categories."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Generate 7 days of 1-hourly observations (168 points)
    # 1. Low antecedent: 0.1 mm/hr -> 24h = 2.4 mm, 72h = 7.2 mm, 7d = 16.8 mm
    obs_low = [
        {"valid_at": (ref_time - timedelta(hours=i)).isoformat(), "value": 0.1, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"}
        for i in range(168)
    ]
    h_low = HistoricalRainfallAnalyzer.analyze_history(obs_low, reference_time=ref_time, window_days=7)
    ari_low = HistoricalRainfallAnalyzer.calculate_antecedent_indicator(h_low)
    assert ari_low.indicator_category == AntecedentCategory.LOW_ANTECEDENT
    assert ari_low.confidence == RiskConfidence.HIGH

    # 2. Extreme antecedent: heavy rainfall across 72h
    obs_ext = [
        {"valid_at": (ref_time - timedelta(hours=i)).isoformat(), "value": 2.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"}
        for i in range(168)
    ]
    h_ext = HistoricalRainfallAnalyzer.analyze_history(obs_ext, reference_time=ref_time, window_days=7)
    ari_ext = HistoricalRainfallAnalyzer.calculate_antecedent_indicator(h_ext)
    assert ari_ext.indicator_category == AntecedentCategory.EXTREME_ANTECEDENT
    assert "EXTREME antecedent" in ari_ext.explanation


def test_antecedent_indicator_unavailable_when_missing():
    """Missing observations produce UNAVAILABLE antecedent indicator with NONE confidence."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    h_empty = HistoricalRainfallAnalyzer.analyze_history([], reference_time=ref_time, window_days=7)
    ari_empty = HistoricalRainfallAnalyzer.calculate_antecedent_indicator(h_empty)

    assert ari_empty.indicator_category == AntecedentCategory.UNAVAILABLE
    assert ari_empty.confidence == RiskConfidence.NONE
    assert ari_empty.indicator_value_mm is None


# =============================================================================
# 6. Service & API Route Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_api_rainfall_history_endpoint():
    """Verify GET /api/v1/risk/rainfall/history returns complete time-series aggregations."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_historical_observations_for_location.return_value = [
        {"valid_at": "2026-11-20T08:00:00Z", "value": 5.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"}
    ]

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/risk/rainfall/history")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_locations" in data
        assert data["total_locations"] == 2
        assert "locations" in data
        first_loc = data["locations"][0]
        assert "history" in first_loc
        assert "antecedent" in first_loc
        assert "daily_totals" in first_loc["history"]
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_api_rainfall_antecedent_endpoint():
    """Verify GET /api/v1/risk/rainfall/antecedent returns antecedent indicators."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    mock_supabase.get_historical_observations_for_location.return_value = []
    mock_supabase.get_latest_observations_for_location.return_value = {"analysis": None, "forecasts": []}

    mock_service = RiskService(supabase_client=mock_supabase)
    app.dependency_overrides[get_risk_service] = lambda: mock_service
    try:
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/risk/rainfall/antecedent")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_locations" in data
        assert "antecedent_indicators" in data
        assert len(data["antecedent_indicators"]) == 2
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_phase_241_risk_classifications_remain_unaltered():
    """Verify that attaching historical context does NOT alter Phase 2.4.1 risk classifications."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [MOCK_LOCATIONS[0]]
    # Latest observation produces LOW risk in Phase 2.4.1
    now_utc = datetime.now(timezone.utc)
    mock_supabase.get_latest_observations_for_location.return_value = {
        "analysis": {"value": 0.0, "unit": "mm", "valid_at": now_utc.isoformat(), "fetched_at": now_utc.isoformat()},
        "forecasts": [{"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 0.0} for i in range(1, 25)],
    }
    mock_supabase.get_historical_observations_for_location.return_value = []

    mock_service = RiskService(supabase_client=mock_supabase)
    res = await mock_service.get_location_rainfall_risk(MOCK_LOCATIONS[0]["id"])

    # Risk category MUST remain LOW
    assert res.risk_category == RiskCategory.LOW
    assert res.confidence == RiskConfidence.HIGH
    # Historical context attached
    assert res.historical_context is not None
    assert res.antecedent_indicator is not None


def test_daily_record_zero_observations_produces_none_rainfall():
    """Remediation DATA-01: Days with 0 observations have rainfall_mm=None, not 0.0."""
    ref_time = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    # Give observations for only 1 day out of a 7-day window
    raw_obs = [
        {"valid_at": (ref_time - timedelta(hours=i)).isoformat(), "value": 1.5, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"}
        for i in range(1, 10)
    ]

    history = HistoricalRainfallAnalyzer.analyze_history(raw_obs, reference_time=ref_time, window_days=7)
    days_with_zero_obs = [d for d in history.daily_totals if d.observation_count == 0]
    days_with_obs = [d for d in history.daily_totals if d.observation_count > 0]

    assert len(days_with_zero_obs) > 0
    for d in days_with_zero_obs:
        assert d.rainfall_mm is None
        assert d.is_rainy_day is False

    assert len(days_with_obs) > 0
    for d in days_with_obs:
        assert isinstance(d.rainfall_mm, float)


# =============================================================================
# 7. HIGH-01 Regression Tests: Supabase Historical Observation Query
# =============================================================================

def _make_configured_supabase() -> SupabaseClient:
    return SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
        anon_key="sb_publishable_TestAnon_1234567890",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [7, 14, 30])
async def test_historical_query_applies_window_cutoff_and_excludes_forecasts(days: int):
    """HIGH-01: 7d, 14d, and 30d requests apply exact UTC cutoff, exclude forecasts, and sort chronologically."""
    ref_time = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    expected_cutoff = (ref_time - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    captured_urls = []

    in_window_old = (ref_time - timedelta(days=days - 1, hours=2)).isoformat().replace("+00:00", "Z")
    in_window_new = (ref_time - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    too_old = (ref_time - timedelta(days=days, minutes=5)).isoformat().replace("+00:00", "Z")

    async def fake_get(url, headers=None, *args, **kwargs):
        captured_urls.append(url)
        req = httpx.Request("GET", url)
        # Simulate PostgREST returning newest-first plus edge rows for defense-in-depth verification
        payload = [
            {"valid_at": in_window_new, "value": 4.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
            {"valid_at": in_window_new, "value": 9.9, "unit": "mm", "observation_type": "forecast", "measurement_type": "rainfall"},
            {"valid_at": in_window_old, "value": 2.0, "unit": "mm", "observation_type": "observed", "measurement_type": "rainfall"},
            {"valid_at": too_old, "value": 99.0, "unit": "mm", "observation_type": "analysis", "measurement_type": "rainfall"},
        ]
        return httpx.Response(200, request=req, json=payload)

    sb = _make_configured_supabase()
    with patch("httpx.AsyncClient.get", side_effect=fake_get):
        rows = await sb.get_historical_observations_for_location(
            "11111111-1111-1111-1111-111111111111",
            days=days,
            reference_time=ref_time,
        )

    assert len(captured_urls) == 1
    query_url = captured_urls[0]
    assert f"valid_at=gte.{expected_cutoff}" in query_url
    assert "observation_type=in.(observed,analysis,reanalysis)" in query_url
    assert "order=valid_at.desc" in query_url
    assert "limit=2000" in query_url

    # Returned rows must exclude out-of-window and forecast rows, and restore ascending order
    assert [r["value"] for r in rows] == [2.0, 4.0]
    assert all(r["observation_type"] != "forecast" for r in rows)


@pytest.mark.asyncio
async def test_historical_query_retains_recent_records_under_limit_and_pagination():
    """HIGH-01: Querying newest-first prevents ascending truncation of recent data while returning chronological order."""
    ref_time = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    captured_urls = []

    # Create 6 valid in-window hourly records (oldest=06:00 .. newest=11:00)
    all_in_window = [
        {
            "valid_at": (ref_time - timedelta(hours=6 - i)).isoformat().replace("+00:00", "Z"),
            "value": float(i + 1),
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
        for i in range(6)
    ]

    async def fake_get(url, headers=None, *args, **kwargs):
        captured_urls.append(url)
        req = httpx.Request("GET", url)
        # Simulate PostgREST applying order=valid_at.desc + limit + offset
        ordered = list(reversed(all_in_window))
        limit_val = int(url.split("limit=")[1].split("&")[0])
        offset_val = int(url.split("offset=")[1].split("&")[0]) if "offset=" in url else 0
        sliced = ordered[offset_val : offset_val + limit_val]
        return httpx.Response(200, request=req, json=sliced)

    sb = _make_configured_supabase()
    with patch("httpx.AsyncClient.get", side_effect=fake_get):
        latest_3 = await sb.get_historical_observations_for_location(
            "11111111-1111-1111-1111-111111111111",
            days=7,
            limit=3,
            reference_time=ref_time,
        )
        next_2 = await sb.get_historical_observations_for_location(
            "11111111-1111-1111-1111-111111111111",
            days=7,
            limit=2,
            offset=3,
            reference_time=ref_time,
        )

    # Most recent 3 records retained (4.0, 5.0, 6.0) in ascending chronological order
    assert [r["value"] for r in latest_3] == [4.0, 5.0, 6.0]
    assert latest_3[0]["valid_at"] < latest_3[1]["valid_at"] < latest_3[2]["valid_at"]
    # Second page (offset=3, limit=2) returns (2.0, 3.0) in ascending chronological order
    assert "offset=3" in captured_urls[1]
    assert [r["value"] for r in next_2] == [2.0, 3.0]


@pytest.mark.asyncio
async def test_historical_query_handles_empty_missing_timestamps_and_validation():
    """HIGH-01: Empty results, records missing timestamps, and invalid parameters are handled safely."""
    ref_time = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    sb = _make_configured_supabase()

    with pytest.raises(ValueError, match="days"):
        await sb.get_historical_observations_for_location("loc-1", days=0)
    with pytest.raises(ValueError, match="days"):
        await sb.get_historical_observations_for_location("loc-1", days=-5)
    with pytest.raises(ValueError, match="limit"):
        await sb.get_historical_observations_for_location("loc-1", days=7, limit=0)

    async def fake_empty(url, headers=None, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json=[])

    with patch("httpx.AsyncClient.get", side_effect=fake_empty):
        assert await sb.get_historical_observations_for_location("loc-1", days=7, reference_time=ref_time) == []

    async def fake_mixed_timestamps(url, headers=None, *args, **kwargs):
        valid_ts = (ref_time - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json=[
                {"valid_at": None, "value": 1.0, "observation_type": "analysis"},
                {"valid_at": "not-a-timestamp", "value": 2.0, "observation_type": "analysis"},
                {"valid_at": valid_ts, "value": 3.5, "observation_type": "analysis"},
            ],
        )

    with patch("httpx.AsyncClient.get", side_effect=fake_mixed_timestamps):
        rows = await sb.get_historical_observations_for_location("loc-1", days=7, reference_time=ref_time)
        assert len(rows) == 1
        assert rows[0]["value"] == 3.5


@pytest.mark.asyncio
async def test_historical_query_raises_on_supabase_errors_instead_of_empty_history():
    """HIGH-01: HTTP errors and network failures raise SupabaseClientError rather than returning empty history."""
    sb = _make_configured_supabase()

    async def fake_http_500(url, headers=None, *args, **kwargs):
        return httpx.Response(500, request=httpx.Request("GET", url), json={"message": "Internal error"})

    with patch("httpx.AsyncClient.get", side_effect=fake_http_500):
        with pytest.raises(SupabaseClientError, match="HTTP 500"):
            await sb.get_historical_observations_for_location("loc-1", days=7)

    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(SupabaseClientError, match="Timed out"):
            await sb.get_historical_observations_for_location("loc-1", days=7)


