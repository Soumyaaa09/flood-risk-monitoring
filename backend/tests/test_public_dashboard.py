"""Automated tests for Phase 2.5 Public Dashboard API contracts and data segregation.

Validates:
- Health endpoint status for public navbar badge.
- Pilot location selection query filtering (/api/v1/risk/rainfall?location_id=...).
- Multi-window historical analytics parameter handling (7d, 14d, 30d).
- Segregation of observed, historical, and forecast data structures.
- Research prototype disclaimers and metadata.
- Data freshness and monitoring KPIs for public banner.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from app.api.v1.routes.monitoring import get_monitoring_service
from app.api.v1.routes.risk import get_risk_service
from app.db.supabase_client import SupabaseClient
from app.main import app
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import (
    FreshnessSummary,
    IngestionHealthSummary,
    MonitoringSummaryResponse,
    QualityStatus,
)
from app.schemas.risk import RiskCategory, RiskConfidence
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


def create_mock_risk_service():
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = MOCK_LOCATIONS
    now_utc = datetime.now(timezone.utc)
    fresh_iso = now_utc.isoformat()

    mock_supabase.get_latest_observations_for_location.return_value = {
        "analysis": {"value": 15.5, "unit": "mm", "valid_at": fresh_iso, "fetched_at": fresh_iso},
        "forecasts": [
            {"valid_at": (now_utc + timedelta(hours=i)).isoformat(), "value": 2.5}
            for i in range(1, 25)
        ],
    }
    mock_supabase.get_historical_observations_for_location.return_value = [
        {
            "valid_at": (now_utc - timedelta(hours=i)).isoformat(),
            "value": 1.2,
            "unit": "mm",
            "observation_type": "analysis",
            "measurement_type": "rainfall",
        }
        for i in range(1, 48)
    ]
    return RiskService(supabase_client=mock_supabase)


@pytest.mark.asyncio
async def test_public_dashboard_health_endpoint(client: AsyncClient):
    """Verify health endpoint provides version and active status for dashboard indicator."""
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = {"status": "connected", "provider": "supabase_postgrest", "table_verified": "districts"}
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


@pytest.mark.asyncio
async def test_public_dashboard_rainfall_risk_all_locations(client: AsyncClient):
    """Verify public dashboard risk endpoint returns all pilot locations with research disclaimer."""
    service = create_mock_risk_service()
    app.dependency_overrides[get_risk_service] = lambda: service
    try:
        response = await client.get("/api/v1/risk/rainfall")
        assert response.status_code == 200
        data = response.json()

        assert "assessments" in data
        assert "prototype_disclaimer" in data
        assert "RESEARCH" in data["prototype_disclaimer"].upper() or "DECISION-SUPPORT" in data["prototype_disclaimer"].upper()

        assessments = data["assessments"]
        assert len(assessments) == 3

        location_names = {a["location_name"] for a in assessments}
        assert "Paradeep Weather Grid Point" in location_names
        assert "Cuttack Weather Grid Point" in location_names
        assert "Kendrapara Weather Grid Point" in location_names

        valid_categories = {"LOW", "MODERATE", "HIGH", "VERY_HIGH", "UNAVAILABLE"}
        for a in assessments:
            assert a["risk_category"] in valid_categories
            assert a["confidence"] in ("HIGH", "MEDIUM", "LOW", "UNAVAILABLE")
            assert "recent_observed_rainfall_mm" in a
            assert "forecast_24h_precipitation_mm" in a
            assert "antecedent_indicator" in a
            assert "historical_context" in a
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_public_dashboard_location_filtering(client: AsyncClient):
    """Verify selecting a single pilot location returns only that location."""
    service = create_mock_risk_service()
    app.dependency_overrides[get_risk_service] = lambda: service
    target_id = MOCK_LOCATIONS[0]["id"]
    try:
        response = await client.get(f"/api/v1/risk/rainfall?location_id={target_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["location_id"] == target_id
        assert data["location_name"] == "Paradeep Weather Grid Point"
        assert data["district_name"] == "Jagatsinghpur"
        assert "historical_context" in data
        assert "antecedent_indicator" in data
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_public_dashboard_historical_multi_window(client: AsyncClient):
    """Verify historical analytics supports 7, 14, and 30-day windows."""
    service = create_mock_risk_service()
    app.dependency_overrides[get_risk_service] = lambda: service
    target_id = MOCK_LOCATIONS[0]["id"]
    try:
        for days in [7, 14, 30]:
            response = await client.get(f"/api/v1/risk/rainfall/history?days={days}&location_id={target_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["location_id"] == target_id
            assert "history" in data
            assert data["history"]["window_days"] == days
            assert "antecedent" in data
            assert "indicator_category" in data["antecedent"]
    finally:
        app.dependency_overrides.pop(get_risk_service, None)


@pytest.mark.asyncio
async def test_public_dashboard_monitoring_summary(client: AsyncClient):
    """Verify monitoring summary endpoint provides network KPIs for public overview banner."""
    mock_service = AsyncMock(spec=MonitoringService)
    now_utc = datetime.now(timezone.utc)
    mock_service.get_monitoring_summary.return_value = MonitoringSummaryResponse(
        system_health="healthy",
        freshness_summary=FreshnessSummary(
            fresh_count=3,
            delayed_count=0,
            stale_count=0,
            unavailable_count=0,
            overall_state=FreshnessState.FRESH,
        ),
        ingestion_summary=IngestionHealthSummary(
            current_run_status="idle",
            recent_runs_count=10,
            successful_runs_count=10,
            failed_runs_count=0,
        ),
        location_healths=[],
        timestamp=now_utc,
    )
    app.dependency_overrides[get_monitoring_service] = lambda: mock_service
    try:
        response = await client.get("/api/v1/monitoring/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["system_health"] == "healthy"
        assert data["freshness_summary"]["fresh_count"] == 3
        assert "prototype_notice" in data
    finally:
        app.dependency_overrides.pop(get_monitoring_service, None)
