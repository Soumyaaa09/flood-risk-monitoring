"""Tests for Phase 2.8.3 HTTPX client lifecycle, bounded concurrency, and query deduplication."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import warnings

import httpx
import pytest

from app.core.http_clients import (
    OPEN_METEO_POOL_LIMITS,
    SUPABASE_POOL_LIMITS,
    close_http_clients,
    get_http_client_registry_stats,
    get_open_meteo_http_client,
    get_supabase_http_client,
    init_http_clients,
)
from app.db.session import check_database_connectivity
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.ingestion.open_meteo import OpenMeteoWeatherConnector
from app.main import app, lifespan
from app.schemas.risk import RiskCategory
from app.services.monitoring_service import (
    MAX_CONCURRENT_LOCATION_QUERIES as MONITORING_MAX_CONCURRENCY,
    MonitoringService,
)
from app.services.risk_service import (
    MAX_CONCURRENT_LOCATION_QUERIES as RISK_MAX_CONCURRENCY,
    RiskService,
)


def _sample_locations(count: int = 3):
    return [
        {
            "id": f"00000000-0000-0000-0000-{i:012d}",
            "district_id": f"d-{i}",
            "name": f"Pilot Point {i}",
            "location_type": "virtual_weather_point",
            "latitude": 20.0 + i * 0.1,
            "longitude": 86.0 + i * 0.1,
            "districts": {"name": f"District {i}"},
        }
        for i in range(1, count + 1)
    ]


# =============================================================================
# 1. HTTPX Client Lifecycle & Pool Management Tests
# =============================================================================


@pytest.mark.asyncio
async def test_lifespan_initializes_reuses_and_closes_pooled_clients():
    """Verify FastAPI lifespan initializes separate pools once, reuses instances, and closes cleanly."""
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", ResourceWarning)

        async with lifespan(app):
            sb_first = get_supabase_http_client()
            sb_second = get_supabase_http_client()
            om_first = get_open_meteo_http_client()
            om_second = get_open_meteo_http_client()

            assert sb_first is sb_second
            assert om_first is om_second
            assert sb_first is not om_first
            assert not sb_first.is_closed
            assert not om_first.is_closed

            assert sb_first._transport._pool._max_connections == SUPABASE_POOL_LIMITS.max_connections
            assert (
                sb_first._transport._pool._max_keepalive_connections
                == SUPABASE_POOL_LIMITS.max_keepalive_connections
            )
            assert om_first._transport._pool._max_connections == OPEN_METEO_POOL_LIMITS.max_connections
            assert (
                om_first._transport._pool._max_keepalive_connections
                == OPEN_METEO_POOL_LIMITS.max_keepalive_connections
            )

            stats = get_http_client_registry_stats()
            assert set(stats["active_loop_pools"].keys()) == {"supabase", "open_meteo"}

        assert sb_first.is_closed
        assert om_first.is_closed
        post_stats = get_http_client_registry_stats()
        assert post_stats["active_loop_pools"] == {}

    resource_warnings = [w for w in caught_warnings if issubclass(w.category, ResourceWarning)]
    assert resource_warnings == []


@pytest.mark.asyncio
async def test_lifespan_startup_error_cleans_up_initialized_pools():
    """Verify startup failure during lifespan cleanly closes already-initialized clients."""
    with patch("app.main.get_ingestion_scheduler", side_effect=RuntimeError("scheduler boom")):
        with pytest.raises(RuntimeError, match="scheduler boom"):
            async with lifespan(app):
                pass

    stats = get_http_client_registry_stats()
    assert stats["active_loop_pools"] == {}


@pytest.mark.asyncio
async def test_explicit_http_client_injection_reuses_client_and_keeps_it_open():
    """Verify SupabaseClient, OpenMeteoWeatherConnector, and session probe support client injection."""
    seen_requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((str(request.url), dict(request.headers)))
        if "districts" in str(request.url):
            return httpx.Response(200, json=[{"id": "d-1"}])
        if "monitoring_locations" in str(request.url):
            return httpx.Response(200, json=[{"id": "loc-1", "name": "Paradeep"}])
        return httpx.Response(
            200,
            json={
                "latitude": 20.3,
                "longitude": 86.6,
                "timezone": "UTC",
                "utc_offset_seconds": 0,
                "current": {"time": "2026-11-26T06:00", "interval": 900, "precipitation": 0.2},
                "hourly": {"time": ["2026-11-26T07:00"], "precipitation": [0.4]},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        sb = SupabaseClient(
            supabase_url="https://example.supabase.co",
            service_role_key="sb_secret_test_key_value",
            http_client=mock_client,
        )
        locs = await sb.get_pilot_locations()
        assert len(locs) == 1

        om = OpenMeteoWeatherConnector(http_client=mock_client)
        preview = await om.fetch_weather_preview(latitude=20.3, longitude=86.6)
        assert preview.latitude == 20.3

        with patch("app.db.session.get_settings") as mock_settings:
            mock_settings.return_value.SUPABASE_URL = "https://example.supabase.co"
            mock_settings.return_value.SUPABASE_ANON_KEY = "sb_publishable_test_key"
            db_probe = await check_database_connectivity(http_client=mock_client)
            assert db_probe["status"] == "connected"

        # Shared injected client must never be closed by callers
        assert not mock_client.is_closed

    # Verify opaque Supabase key stays in apikey only (never leaked as Authorization Bearer)
    sb_req_headers = seen_requests[0][1]
    assert sb_req_headers.get("apikey") == "sb_secret_test_key_value"
    assert "authorization" not in sb_req_headers


@pytest.mark.asyncio
async def test_close_http_clients_is_idempotent():
    """Verify calling close_http_clients repeatedly is safe and clears pool registrations."""
    init_http_clients()
    first_closed = await close_http_clients()
    second_closed = await close_http_clients()
    assert first_closed == 2
    assert second_closed == 0


# =============================================================================
# 2. Supabase Concurrency & Query Deduplication Tests
# =============================================================================


@pytest.mark.asyncio
async def test_supabase_get_latest_observations_runs_analysis_and_forecast_concurrently():
    """Barrier test proving analysis and forecast GETs run concurrently rather than sequentially."""
    both_started = asyncio.Event()
    in_flight = 0

    async def concurrent_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight
        in_flight += 1
        if in_flight == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        if "observation_type=eq.analysis" in str(request.url):
            return httpx.Response(200, json=[{"value": 1.2, "valid_at": "2026-11-26T06:00:00Z"}])
        return httpx.Response(200, json=[{"value": 0.5, "valid_at": "2026-11-26T07:00:00Z"}])

    transport = httpx.MockTransport(concurrent_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        sb = SupabaseClient(
            supabase_url="https://example.supabase.co",
            service_role_key="sb_secret_test",
            http_client=mock_client,
        )
        res = await sb.get_latest_observations_for_location("loc-1")

    assert res["analysis"]["value"] == 1.2
    assert len(res["forecasts"]) == 1


@pytest.mark.asyncio
async def test_supabase_get_latest_observations_propagates_http_errors():
    """Verify non-200 HTTP responses in get_latest_observations_for_location raise SupabaseClientError."""

    async def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "upstream unavailable"})

    transport = httpx.MockTransport(error_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        sb = SupabaseClient(
            supabase_url="https://example.supabase.co",
            service_role_key="sb_secret_test",
            http_client=mock_client,
        )
        with pytest.raises(SupabaseClientError) as exc_info:
            await sb.get_latest_observations_for_location("loc-1")
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_risk_service_eliminates_duplicate_fallback_and_bounds_concurrency():
    """Verify RiskService removes duplicate fallback telemetry fetches and caps location concurrency."""
    now_utc = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=15)).isoformat()
    locations = _sample_locations(10)

    active_calls = 0
    max_active_calls = 0
    lock = asyncio.Lock()
    latest_calls_by_loc = []
    history_calls_by_loc = []

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = locations

    async def fake_latest(location_id: str, limit: int = 72):
        nonlocal active_calls, max_active_calls
        latest_calls_by_loc.append(location_id)
        async with lock:
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
        try:
            # Deliberately make earlier locations slower to prove output order stays stable
            idx = int(location_id.rsplit("-", 1)[-1])
            await asyncio.sleep(0.02 if idx <= 3 else 0.005)
            return {
                "analysis": {
                    "value": float(idx),
                    "unit": "mm",
                    "valid_at": fresh_iso,
                    "fetched_at": fresh_iso,
                    "observation_type": "analysis",
                    "measurement_type": "rainfall",
                },
                "forecasts": [
                    {"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.1}
                    for h in range(1, 25)
                ],
            }
        finally:
            async with lock:
                active_calls -= 1

    async def fake_history(location_id: str, days: int = 7):
        history_calls_by_loc.append(location_id)
        # Returning empty list previously caused duplicate fallback call to get_latest_observations_for_location
        return []

    mock_sb.get_latest_observations_for_location.side_effect = fake_latest
    mock_sb.get_historical_observations_for_location.side_effect = fake_history

    service = RiskService(supabase_client=mock_sb)
    summary = await service.get_all_locations_rainfall_risk(now_time=now_utc)

    assert summary.total_locations == 10
    # Strict elimination of duplicate fallback queries: 10 latest calls total, not 20
    assert len(latest_calls_by_loc) == 10
    assert len(history_calls_by_loc) == 10
    assert mock_sb.get_pilot_locations.call_count == 1
    # Bounded concurrency respected and parallelism actually exercised
    assert 2 <= max_active_calls <= RISK_MAX_CONCURRENCY
    # Location order preserved despite staggered completion times
    assert [a.location_id for a in summary.assessments] == [loc["id"] for loc in locations]
    # Single observation fallback still reused in-memory analysis without extra query
    assert summary.assessments[0].recent_observed_rainfall_mm == 1.0
    assert summary.assessments[0].historical_context.rainfall_7d_mm == 1.0


@pytest.mark.asyncio
async def test_risk_service_all_history_fetches_pilot_locations_once():
    """Verify get_all_locations_rainfall_history eliminates N+1 get_pilot_locations lookups."""
    locations = _sample_locations(4)
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = locations
    mock_sb.get_historical_observations_for_location.return_value = [
        {
            "valid_at": datetime.now(timezone.utc).isoformat(),
            "value": 3.5,
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
    ]

    service = RiskService(supabase_client=mock_sb)
    history_summary = await service.get_all_locations_rainfall_history(days=7)

    assert history_summary.total_locations == 4
    assert mock_sb.get_pilot_locations.call_count == 1
    assert mock_sb.get_historical_observations_for_location.call_count == 4
    assert [loc.location_id for loc in history_summary.locations] == [loc["id"] for loc in locations]


@pytest.mark.asyncio
async def test_risk_service_concurrent_fanout_propagates_primary_telemetry_failure():
    """Ensure DB error during concurrent location fan-out raises rather than returning fake empty data."""
    locations = _sample_locations(3)
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = locations

    async def flaky_latest(location_id: str, limit: int = 72):
        if location_id.endswith("000000000002"):
            raise SupabaseClientError("Simulated database outage", status_code=503)
        return {"analysis": None, "forecasts": []}

    mock_sb.get_latest_observations_for_location.side_effect = flaky_latest
    mock_sb.get_historical_observations_for_location.return_value = []

    service = RiskService(supabase_client=mock_sb)
    with pytest.raises(SupabaseClientError, match="Simulated database outage"):
        await service.get_all_locations_rainfall_risk()


@pytest.mark.asyncio
async def test_monitoring_summary_concurrent_stages_and_error_propagation():
    """Verify MonitoringService runs independent stages concurrently, bounds locations, and propagates errors."""
    now_utc = datetime.now(timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=10)).isoformat()
    locations = _sample_locations(8)

    stage1_barrier = asyncio.Event()
    stage1_started = 0
    active_loc_calls = 0
    max_loc_calls = 0
    lock = asyncio.Lock()

    mock_sb = AsyncMock(spec=SupabaseClient)

    async def mark_stage1_and_wait():
        nonlocal stage1_started
        stage1_started += 1
        if stage1_started == 3:
            stage1_barrier.set()
        await asyncio.wait_for(stage1_barrier.wait(), timeout=1.0)

    async def fake_locations():
        await mark_stage1_and_wait()
        return locations

    async def fake_runs(limit: int = 20):
        await mark_stage1_and_wait()
        return []

    async def fake_stale(cutoff_minutes: int = 60):
        await mark_stage1_and_wait()
        return []

    async def fake_latest(location_id: str, limit: int = 72):
        nonlocal active_loc_calls, max_loc_calls
        async with lock:
            active_loc_calls += 1
            max_loc_calls = max(max_loc_calls, active_loc_calls)
        try:
            await asyncio.sleep(0.01)
            return {
                "analysis": {"value": 0.5, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
                "forecasts": [
                    {"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.1}
                    for h in range(1, 25)
                ],
            }
        finally:
            async with lock:
                active_loc_calls -= 1

    mock_sb.get_pilot_locations.side_effect = fake_locations
    mock_sb.get_ingestion_runs.side_effect = fake_runs
    mock_sb.get_stale_started_runs.side_effect = fake_stale
    mock_sb.get_latest_observations_for_location.side_effect = fake_latest

    service = MonitoringService(supabase_client=mock_sb)
    summary = await service.get_monitoring_summary()

    assert len(summary.location_healths) == 8
    assert [lh.location_id for lh in summary.location_healths] == [loc["id"] for loc in locations]
    assert 2 <= max_loc_calls <= MONITORING_MAX_CONCURRENCY
    assert summary.system_health == "healthy"

    # Error propagation check on concurrent monitoring fan-out
    mock_sb_fail = AsyncMock(spec=SupabaseClient)
    mock_sb_fail.get_pilot_locations.return_value = locations[:3]
    mock_sb_fail.get_ingestion_runs.return_value = []
    mock_sb_fail.get_stale_started_runs.return_value = []
    mock_sb_fail.get_latest_observations_for_location.side_effect = SupabaseClientError(
        "Supabase read timeout", status_code=504
    )

    failing_service = MonitoringService(supabase_client=mock_sb_fail)
    with pytest.raises(SupabaseClientError, match="Supabase read timeout"):
        await failing_service.get_monitoring_summary()


@pytest.mark.asyncio
async def test_risk_and_monitoring_endpoints_preserve_contract_and_missing_vs_zero(client):
    """Verify optimized endpoints preserve API schema keys and missing != zero risk semantics."""
    now_utc = datetime.now(timezone.utc)
    fresh_iso = (now_utc - timedelta(minutes=10)).isoformat()
    locations = _sample_locations(2)

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = locations
    mock_sb.get_historical_observations_for_location.return_value = []
    mock_sb.get_ingestion_runs.return_value = []
    mock_sb.get_stale_started_runs.return_value = []
    mock_sb.get_latest_observations_for_location.side_effect = [
        # Risk call: loc 1 missing analysis + 0 forecast -> UNAVAILABLE; loc 2 valid -> LOW
        {
            "analysis": None,
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.0} for h in range(1, 25)],
        },
        {
            "analysis": {"value": 0.0, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.0} for h in range(1, 25)],
        },
        # Monitoring call: loc 1 & loc 2
        {
            "analysis": None,
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.0} for h in range(1, 25)],
        },
        {
            "analysis": {"value": 0.0, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
            "forecasts": [{"valid_at": (now_utc + timedelta(hours=h)).isoformat(), "value": 0.0} for h in range(1, 25)],
        },
    ]

    risk_svc = RiskService(supabase_client=mock_sb)
    mon_svc = MonitoringService(supabase_client=mock_sb)

    from app.api.v1.routes.monitoring import get_monitoring_service
    from app.api.v1.routes.risk import get_risk_service

    app.dependency_overrides[get_risk_service] = lambda: risk_svc
    app.dependency_overrides[get_monitoring_service] = lambda: mon_svc
    try:
        risk_resp = await client.get("/api/v1/risk/rainfall")
        mon_resp = await client.get("/api/v1/monitoring/summary")

        assert risk_resp.status_code == 200
        risk_json = risk_resp.json()
        assert sorted(risk_json.keys()) == [
            "assessment_timestamp",
            "assessments",
            "category_counts",
            "component_readiness",
            "highest_risk_category",
            "prototype_disclaimer",
            "threshold_version",
            "total_locations",
        ]
        assert risk_json["assessments"][0]["risk_category"] == RiskCategory.UNAVAILABLE.value
        assert risk_json["assessments"][1]["risk_category"] == RiskCategory.LOW.value

        assert mon_resp.status_code == 200
        mon_json = mon_resp.json()
        assert sorted(mon_json.keys()) == [
            "freshness_summary",
            "ingestion_summary",
            "location_healths",
            "prototype_notice",
            "scheduler",
            "system_health",
            "timestamp",
        ]
    finally:
        app.dependency_overrides.pop(get_risk_service, None)
        app.dependency_overrides.pop(get_monitoring_service, None)
