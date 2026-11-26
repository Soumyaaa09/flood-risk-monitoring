from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from httpx import AsyncClient, Response

from app.core.config import get_settings
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.ingestion.open_meteo import (
    OpenMeteoWeatherConnector,
    ProviderException,
    ProviderTimeoutException,
    ProviderValidationException,
)
from app.main import app
from app.schemas.ingestion import FreshnessState, IngestionStatus
from app.services.ingestion_service import IngestionService
from app.api.v1.routes.ingestion import get_ingestion_service

SAMPLE_OPEN_METEO_PAYLOAD = {
    "latitude": 20.316,
    "longitude": 86.611,
    "elevation": 5.0,
    "generationtime_ms": 0.12,
    "utc_offset_seconds": 0,
    "timezone": "UTC",
    "current": {
        "time": "2026-11-17T12:00",
        "interval": 900,
        "precipitation": 2.4,
    },
    "hourly": {
        "time": [
            "2026-11-17T11:00",  # Past hourly (should be filtered out)
            "2026-11-17T12:00",  # Current hour
            "2026-11-17T13:00",  # Future hour 1
            "2026-11-17T14:00",  # Future hour 2
        ],
        "precipitation": [0.5, 2.4, 4.2, 0.0],
    },
}

SAMPLE_PILOT_LOCATIONS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "district_id": "d1",
        "basin_id": "b1",
        "name": "Paradeep Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.3160,
        "longitude": 86.6110,
        "elevation_m": None,
        "is_official_gauge": False,
        "metadata": {"pilot_area": "Paradeep"},
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
        "elevation_m": None,
        "is_official_gauge": False,
        "metadata": {"pilot_area": "Cuttack"},
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
        "elevation_m": None,
        "is_official_gauge": False,
        "metadata": {"pilot_area": "Kendrapara"},
        "districts": {"name": "Kendrapara"},
    },
]


# =============================================================================
# 1. Open-Meteo Successful Response & Scientific Classification
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_success_and_classification():
    """Verify Open-Meteo successful response, analysis vs forecast classification,
    NULL forecast reference time, and interval metadata preservation.
    """
    connector = OpenMeteoWeatherConnector()
    data = connector._transform_precipitation_payload(SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611)

    # 1. Successful response
    assert data.latitude == 20.316
    assert data.longitude == 86.611
    assert data.elevation_m == 5.0
    assert data.analysis_record is not None

    # 8. Current analysis classification (model-derived nowcast, NOT physical observation)
    analysis = data.analysis_record
    assert analysis.observation_type == "analysis"
    assert analysis.measurement_type == "rainfall"
    assert analysis.value == 2.4
    assert analysis.unit == "mm"
    assert analysis.quality_flag == "unverified"
    assert analysis.valid_at == datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    # 10. Forecast reference time remains NULL
    assert analysis.forecast_reference_time is None

    # 11. Interval metadata preservation
    assert analysis.metadata["accumulation_interval_seconds"] == 900
    assert "Model-derived" in analysis.metadata["disclaimer"]

    # 9. Future forecast classification (past hourly points filtered out)
    assert len(data.forecast_records) == 3
    # First future point is 12:00
    assert data.forecast_records[0].valid_at == datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    assert data.forecast_records[0].observation_type == "forecast"
    assert data.forecast_records[0].forecast_reference_time is None
    # Subsequent hourly forecast
    assert data.forecast_records[1].valid_at == datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)
    assert data.forecast_records[1].value == 4.2
    assert data.forecast_records[1].unit == "mm"


# =============================================================================
# 2. Invalid Provider Response
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_invalid_provider_response():
    """Verify rejection of unparseable or schema-violating provider response."""
    connector = OpenMeteoWeatherConnector()
    with pytest.raises(ProviderValidationException) as exc_info:
        connector._transform_precipitation_payload({"invalid_key": "junk_data"}, 20.316, 86.611)
    assert "validation failed" in str(exc_info.value)


# =============================================================================
# 3. Missing Precipitation Data
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_missing_precipitation():
    """Verify handling when precipitation fields are absent or null."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": None,
        "hourly": None,
    }
    data = connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert data.analysis_record is None
    assert len(data.forecast_records) == 0


# =============================================================================
# 4. Negative Precipitation Rejection
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_negative_precipitation_rejection():
    """Verify that negative precipitation values are strictly rejected."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {
            "time": "2026-11-17T12:00",
            "interval": 900,
            "precipitation": -5.0,  # Physically invalid!
        },
    }
    with pytest.raises(ProviderValidationException) as exc_info:
        connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert "greater than or equal to 0" in str(exc_info.value) or "cannot be negative" in str(exc_info.value)


# =============================================================================
# 5. HTTP Timeout Handling
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_http_timeout():
    """Verify timeout exception handling when Open-Meteo takes too long."""
    connector = OpenMeteoWeatherConnector(timeout_seconds=0.01, max_retries=0)
    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Read timeout")):
        with pytest.raises(ProviderTimeoutException):
            await connector.fetch_precipitation(latitude=20.316, longitude=86.611)


# =============================================================================
# 6. HTTP 5xx Retry with Backoff
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_5xx_retry_with_backoff():
    """Verify transient HTTP 5xx responses are retried with exponential backoff."""
    connector = OpenMeteoWeatherConnector(max_retries=2, backoff_factor=0.01)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_500 = Response(500, request=mock_req, text="Internal Server Error")
    resp_200 = Response(200, request=mock_req, json=SAMPLE_OPEN_METEO_PAYLOAD)

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.HTTPStatusError("Server Error", request=mock_req, response=resp_500)
        return resp_200

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert result.latitude == 20.316
        assert call_count == 2


# =============================================================================
# 7. Permanent HTTP 4xx Failure (No Indefinite Retries)
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_permanent_4xx_failure():
    """Verify permanent 4xx client errors abort immediately without endless retries."""
    connector = OpenMeteoWeatherConnector(max_retries=3)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_400 = Response(400, request=mock_req, text="Bad Request: Invalid parameters")

    with patch("httpx.AsyncClient.get", return_value=resp_400):
        with pytest.raises(ProviderException) as exc_info:
            await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert exc_info.value.status_code == 400


# =============================================================================
# 8. HTTP 429 Rate-Limit Handling
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_429_retry_success():
    """Verify HTTP 429 rate limit is retried and succeeds on subsequent attempt."""
    connector = OpenMeteoWeatherConnector(max_retries=2, backoff_factor=0.01)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_429 = Response(429, request=mock_req, text="Too Many Requests", headers={"Retry-After": "0.01"})
    resp_200 = Response(200, request=mock_req, json=SAMPLE_OPEN_METEO_PAYLOAD)

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_429
        return resp_200

    with patch("httpx.AsyncClient.get", side_effect=mock_get), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert result.latitude == 20.316
        assert call_count == 2
        mock_sleep.assert_awaited_once_with(0.01)


@pytest.mark.asyncio
async def test_open_meteo_429_retry_after_http_date():
    """Verify HTTP 429 with HTTP-date Retry-After header parses delay and retries."""
    connector = OpenMeteoWeatherConnector(max_retries=2, backoff_factor=0.01)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    future_date = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    resp_429 = Response(429, request=mock_req, text="Too Many Requests", headers={"Retry-After": future_date})
    resp_200 = Response(200, request=mock_req, json=SAMPLE_OPEN_METEO_PAYLOAD)

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_429
        return resp_200

    with patch("httpx.AsyncClient.get", side_effect=mock_get), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert result.latitude == 20.316
        assert call_count == 2
        assert mock_sleep.await_count == 1
        slept_duration = mock_sleep.await_args[0][0]
        assert 3.0 <= slept_duration <= 6.0


@pytest.mark.asyncio
async def test_open_meteo_429_retry_exhaustion():
    """Verify HTTP 429 raises ProviderException after retries are exhausted."""
    connector = OpenMeteoWeatherConnector(max_retries=2, backoff_factor=0.01)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_429 = Response(429, request=mock_req, text="Too Many Requests")

    with patch("httpx.AsyncClient.get", return_value=resp_429), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(ProviderException) as exc_info:
            await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert exc_info.value.status_code == 429
        assert "retries exhausted" in str(exc_info.value).lower()
        assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_open_meteo_429_invalid_or_missing_retry_after():
    """Verify HTTP 429 with missing or malformed Retry-After falls back to exponential backoff."""
    connector = OpenMeteoWeatherConnector(max_retries=1, backoff_factor=0.5)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_429 = Response(429, request=mock_req, text="Too Many Requests", headers={"Retry-After": "invalid-date-string"})
    resp_200 = Response(200, request=mock_req, json=SAMPLE_OPEN_METEO_PAYLOAD)

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_429
        return resp_200

    with patch("httpx.AsyncClient.get", side_effect=mock_get), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert result.latitude == 20.316
        assert call_count == 2
        mock_sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_open_meteo_429_max_wait_exceeded():
    """Verify HTTP 429 with excessive Retry-After delay aborts safely without waiting."""
    connector = OpenMeteoWeatherConnector(max_retries=2, max_wait_seconds=30.0)

    mock_req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp_429 = Response(429, request=mock_req, text="Too Many Requests", headers={"Retry-After": "300"})

    with patch("httpx.AsyncClient.get", return_value=resp_429):
        with pytest.raises(ProviderException) as exc_info:
            await connector.fetch_precipitation(latitude=20.316, longitude=86.611)
        assert exc_info.value.status_code == 429
        assert "exceeds maximum allowed wait" in str(exc_info.value)


# =============================================================================
# 9. Hourly Forecast Data-Gap Validation
# =============================================================================
@pytest.mark.asyncio
async def test_open_meteo_forecast_all_missing_rejected():
    """Verify rejection when all hourly forecast values in horizon are null."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "precipitation": 1.0},
        "hourly": {
            "time": ["2026-11-17T12:00", "2026-11-17T13:00", "2026-11-17T14:00"],
            "precipitation": [None, None, None],
        },
    }
    with pytest.raises(ProviderValidationException) as exc_info:
        connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert "data gap: all 3 hourly precipitation values are null" in str(exc_info.value)


@pytest.mark.asyncio
async def test_open_meteo_forecast_excessive_missing_rejected():
    """Verify rejection when more than 50% of hourly forecast values are null."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "precipitation": 1.0},
        "hourly": {
            "time": [
                "2026-11-17T12:00",
                "2026-11-17T13:00",
                "2026-11-17T14:00",
                "2026-11-17T15:00",
            ],
            "precipitation": [1.2, None, None, None],  # 3/4 = 75% missing
        },
    }
    with pytest.raises(ProviderValidationException) as exc_info:
        connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert "exceeds 50% threshold" in str(exc_info.value)
    assert "3/4 (75.0%)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_open_meteo_forecast_acceptable_partial_preserved():
    """Verify acceptable partial forecasts (<= 50% missing) preserve valid points without converting to 0."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "precipitation": 1.0},
        "hourly": {
            "time": [
                "2026-11-17T12:00",
                "2026-11-17T13:00",
                "2026-11-17T14:00",
                "2026-11-17T15:00",
            ],
            "precipitation": [1.5, None, 2.5, 3.5],  # 1/4 = 25% missing <= 50%
        },
    }
    data = connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert len(data.forecast_records) == 3
    values = [r.value for r in data.forecast_records]
    assert values == [1.5, 2.5, 3.5]


@pytest.mark.asyncio
async def test_open_meteo_forecast_genuine_zero_rainfall_valid():
    """Verify genuine zero rainfall (0.0) is treated as valid measurement, not missing data."""
    connector = OpenMeteoWeatherConnector()
    payload = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "precipitation": 0.0},
        "hourly": {
            "time": [
                "2026-11-17T12:00",
                "2026-11-17T13:00",
                "2026-11-17T14:00",
                "2026-11-17T15:00",
            ],
            "precipitation": [0.0, 0.0, 0.0, 0.0],  # Genuine dry weather forecast
        },
    }
    data = connector._transform_precipitation_payload(payload, 20.316, 86.611)
    assert data.analysis_record is not None
    assert data.analysis_record.value == 0.0
    assert len(data.forecast_records) == 4
    for r in data.forecast_records:
        assert r.value == 0.0


# =============================================================================
# 12 & 15. Ingestion Deduplication, Counters & Status
# =============================================================================
@pytest.mark.asyncio
async def test_ingestion_counters_and_status():
    """Verify ingestion run accurately records fetched, inserted, and updated counters."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-123", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-001", "status": "started"}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 3,
        "records_updated": 1,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    assert result.status == "completed"
    assert result.total_locations == 1
    assert result.successful_locations == 1
    assert result.failed_locations == 0
    assert result.total_records_fetched == 4
    assert result.total_records_inserted == 3
    assert result.total_records_updated == 1

    # Verify update_ingestion_run was called with exact counts
    mock_supabase.update_ingestion_run.assert_called_once()
    call_kwargs = mock_supabase.update_ingestion_run.call_args[1]
    assert call_kwargs["status"] == "completed"
    assert call_kwargs["records_fetched"] == 4
    assert call_kwargs["records_inserted"] == 3
    assert call_kwargs["records_updated"] == 1


# =============================================================================
# 13. Forecast Revision Behavior
# =============================================================================
@pytest.mark.asyncio
async def test_forecast_revision_direct_persistence_behavior():
    """Verify that when an existing forecast record value changes,
    the direct persistence path updates the record and increments records_updated.
    """
    mock_supabase = SupabaseClient(supabase_url="https://test.supabase.co", service_role_key="test-key")

    mock_req = httpx.Request("GET", "https://test.supabase.co")
    # Simulate existing record with value 1.5
    existing_record = [{"id": "obs-existing-1", "value": 1.5, "unit": "mm"}]
    resp_get = Response(200, request=mock_req, json=existing_record)
    resp_patch = Response(200, request=mock_req, json={})

    async def mock_get(url, *args, **kwargs):
        if "select=id" in str(url):
            return resp_get
        return Response(200, request=mock_req, json=[])

    async def mock_patch(url, *args, **kwargs):
        return resp_patch

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        with patch("httpx.AsyncClient.patch", side_effect=mock_patch):
            observations = [{
                "location_id": "loc-1",
                "data_source_id": "ds-1",
                "observation_type": "forecast",
                "measurement_type": "rainfall",
                "valid_at": "2026-11-17T14:00:00+00:00",
                "fetched_at": "2026-11-17T12:00:00+00:00",
                "value": 4.5,  # Changed from 1.5 to 4.5
                "unit": "mm",
            }]
            counts = await mock_supabase._persist_records_direct(observations, "run-1")
            assert counts["records_inserted"] == 0
            assert counts["records_updated"] == 1


# =============================================================================
# 14. Partial Location Failure Resilience
# =============================================================================
@pytest.mark.asyncio
async def test_partial_location_failure_resilience():
    """Verify that if one pilot location fails, the remaining locations continue."""
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = SAMPLE_PILOT_LOCATIONS
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-x"}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    # Connector fails on Paradeep (loc 0), but succeeds on Cuttack (loc 1) and Kendrapara (loc 2)
    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    test_data = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.46, 85.88
    )

    async def side_effect_fetch(latitude, longitude, **kwargs):
        if abs(latitude - 20.3160) < 0.01:
            raise ProviderException("Network timeout for Paradeep station", status_code=504)
        return test_data

    mock_connector.fetch_precipitation.side_effect = side_effect_fetch

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion()

    assert result.status == "partial"
    assert result.total_locations == 3
    assert result.successful_locations == 2
    assert result.failed_locations == 1
    assert result.results[0].status == IngestionStatus.FAILED
    assert result.results[1].status == IngestionStatus.COMPLETED
    assert result.results[2].status == IngestionStatus.COMPLETED


# =============================================================================
# 16. Credential Redaction & Protection
# =============================================================================
@pytest.mark.asyncio
async def test_credential_redaction_in_health_and_errors(client: AsyncClient):
    """Verify that database keys and ingestion tokens are NEVER exposed in responses."""
    resp = await client.get("/api/v1/sources/health")
    assert resp.status_code == 200
    body_text = resp.text

    settings = get_settings()
    # Confirm secrets are not present in response payload
    if settings.SUPABASE_SERVICE_ROLE_KEY:
        assert settings.SUPABASE_SERVICE_ROLE_KEY not in body_text
    assert settings.INGESTION_TRIGGER_TOKEN not in body_text


# =============================================================================
# 17. Ingestion Trigger Authorization Security
# =============================================================================
@pytest.mark.asyncio
async def test_ingestion_trigger_authorization(client: AsyncClient):
    """Verify that POST /api/v1/ingestion/trigger requires a valid authorization token."""
    # 1. Missing token -> 401 Unauthorized
    resp_unauth = await client.post("/api/v1/ingestion/trigger")
    assert resp_unauth.status_code == 401

    # 2. Invalid token -> 403 Forbidden
    resp_forbidden = await client.post(
        "/api/v1/ingestion/trigger", headers={"X-Ingestion-Token": "wrong-token"}
    )
    assert resp_forbidden.status_code == 403

    # 3. Query parameter token rejection -> Must be rejected with 401
    settings = get_settings()
    resp_query_rejected = await client.post(
        f"/api/v1/ingestion/trigger?token={settings.INGESTION_TRIGGER_TOKEN}"
    )
    assert resp_query_rejected.status_code == 401
    assert "Missing required 'X-Ingestion-Token' header" in resp_query_rejected.text

    # 4. Valid header token -> Authorized and executed
    mock_service = AsyncMock(spec=IngestionService)
    from app.schemas.ingestion import IngestionTriggerResponse
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=12,
        total_records_inserted=12,
        total_records_updated=0,
    )

    app.dependency_overrides[get_ingestion_service] = lambda: mock_service
    try:
        resp_auth = await client.post(
            "/api/v1/ingestion/trigger",
            headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        )
        assert resp_auth.status_code == 200
        assert resp_auth.json()["status"] == "completed"
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)


# =============================================================================
# Pre-Live Safety Audit Tests
# =============================================================================
@pytest.mark.asyncio
async def test_concurrent_conflict_handling_in_direct_fallback():
    """Verify that if another process inserts a record concurrently between GET and POST (HTTP 409 Conflict),
    the fallback persistence handles the conflict by re-querying and patching without losing data.
    """
    mock_supabase = SupabaseClient(supabase_url="https://test.supabase.co", service_role_key="test-key")

    mock_req = httpx.Request("POST", "https://test.supabase.co")
    resp_409 = Response(409, request=mock_req, text="duplicate key value violates unique constraint")
    existing_after_race = [{"id": "obs-race-winner", "value": 1.0, "unit": "mm"}]
    resp_get_retry = Response(200, request=mock_req, json=existing_after_race)
    resp_patch = Response(200, request=mock_req, json={})

    get_call_count = 0

    async def mock_get(url, *args, **kwargs):
        nonlocal get_call_count
        get_call_count += 1
        if get_call_count == 1:
            return Response(200, request=mock_req, json=[])  # Initially empty
        return resp_get_retry  # Re-query after conflict finds the record

    async def mock_post(url, *args, **kwargs):
        return resp_409  # Collision on POST

    async def mock_patch(url, *args, **kwargs):
        return resp_patch

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            with patch("httpx.AsyncClient.patch", side_effect=mock_patch):
                observations = [{
                    "location_id": "loc-1",
                    "data_source_id": "ds-1",
                    "observation_type": "forecast",
                    "measurement_type": "rainfall",
                    "valid_at": "2026-11-18T12:00:00+00:00",
                    "fetched_at": "2026-11-18T10:00:00+00:00",
                    "value": 3.0,
                    "unit": "mm",
                }]
                counts = await mock_supabase._persist_records_direct(observations, "run-conflict-test")
                assert counts["records_inserted"] == 0
                assert counts["records_updated"] == 1


@pytest.mark.asyncio
async def test_production_environment_requires_rpc():
    """Verify that in production environment, non-atomic fallback is strictly disabled."""
    mock_supabase = SupabaseClient(supabase_url="https://test.supabase.co", service_role_key="test-key")

    mock_req = httpx.Request("POST", "https://test.supabase.co")
    resp_404 = Response(404, request=mock_req, text="Function upsert_weather_observations not found")

    with patch("httpx.AsyncClient.post", return_value=resp_404):
        with patch.object(get_settings(), "ENVIRONMENT", "production"):
            from app.db.supabase_client import SupabaseClientError
            with pytest.raises(SupabaseClientError) as exc_info:
                await mock_supabase.persist_weather_observations(
                    [{"location_id": "l1", "valid_at": "2026-11-18T12:00:00Z"}],
                    "run-prod-check"
                )
            assert "Production ingestion requires Migration 003 RPC" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rpc_permission_denial_handling():
    """Verify that when Supabase RPC returns 403 Forbidden, an explicit error is reported."""
    mock_supabase = SupabaseClient(supabase_url="https://test.supabase.co", service_role_key="test-key")

    mock_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/rpc/upsert_weather_observations")
    resp_403 = Response(403, request=mock_req, text="permission denied for function upsert_weather_observations")

    with patch("httpx.AsyncClient.post", return_value=resp_403):
        with patch.object(get_settings(), "ENVIRONMENT", "production"):
            from app.db.supabase_client import SupabaseClientError
            with pytest.raises(SupabaseClientError) as exc_info:
                await mock_supabase.persist_weather_observations(
                    [{"location_id": "l1", "valid_at": "2026-11-18T12:00:00Z"}],
                    "run-403-check"
                )
            assert "403" in str(exc_info.value)


@pytest.mark.asyncio
async def test_failed_location_persistence_handling():
    """Verify that when persistence fails for a location, the ingestion run is marked failed
    with a sanitized error message, without stopping processing of other locations.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-fail"}
    from app.db.supabase_client import SupabaseClientError
    mock_supabase.persist_weather_observations.side_effect = SupabaseClientError("Database connection dropped")
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    assert result.status == "failed"
    assert result.failed_locations == 1
    assert result.results[0].status == IngestionStatus.FAILED
    assert "Database connection dropped" in result.results[0].error_message


# =============================================================================
# Freshness Calculation Unit Tests
# =============================================================================
def test_freshness_calculation_states():
    """Verify data freshness transitions between fresh, delayed, stale, and unavailable."""
    service = IngestionService()
    now = datetime.now(timezone.utc)

    # None -> unavailable
    assert service.calculate_freshness(None) == FreshnessState.UNAVAILABLE

    # 15 minutes ago -> fresh (<= 60 min)
    assert service.calculate_freshness(now - timedelta(minutes=15)) == FreshnessState.FRESH

    # 90 minutes ago -> delayed (60 < age <= 180 min)
    assert service.calculate_freshness(now - timedelta(minutes=90)) == FreshnessState.DELAYED

    # 300 minutes ago -> stale (> 180 min)
    assert service.calculate_freshness(now - timedelta(minutes=300)) == FreshnessState.STALE

    # Future timestamp (> 60s ahead) -> unavailable
    assert service.calculate_freshness(now + timedelta(minutes=10)) == FreshnessState.UNAVAILABLE

    # Exact boundaries: 60m -> FRESH, 60.1m -> DELAYED, 180m -> DELAYED, 180.1m -> STALE
    assert service.calculate_freshness(now - timedelta(minutes=60), reference_time=now) == FreshnessState.FRESH
    assert service.calculate_freshness(now - timedelta(minutes=60, seconds=6), reference_time=now) == FreshnessState.DELAYED
    assert service.calculate_freshness(now - timedelta(minutes=180), reference_time=now) == FreshnessState.DELAYED
    assert service.calculate_freshness(now - timedelta(minutes=180, seconds=6), reference_time=now) == FreshnessState.STALE


@pytest.mark.asyncio
async def test_latest_weather_handles_malformed_timestamps_without_crashing():
    """MED-02: Malformed valid_at / fetched_at in DB produces UNAVAILABLE rather than raising ValueError."""
    loc_id = "11111111-1111-1111-1111-111111111111"
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_pilot_locations.return_value = [
        {
            "id": loc_id,
            "district_id": "d1",
            "name": "Paradeep Weather Grid Point",
            "latitude": 20.3160,
            "longitude": 86.6110,
            "districts": {"name": "Jagatsinghpur"},
        }
    ]
    mock_sb.get_latest_observations_for_location.return_value = {
        "analysis": {
            "value": 0.0,
            "unit": "mm",
            "valid_at": "bad-timestamp",
            "fetched_at": "bad-timestamp",
            "data_source_id": "ds-1",
        },
        "forecasts": [],
    }

    service = IngestionService(supabase_client=mock_sb)
    latest = await service.get_latest_weather_for_location(loc_id)
    assert latest.freshness == FreshnessState.UNAVAILABLE
    assert latest.current_analysis is None


# =============================================================================
# Phase 2.3.2.1 Hardening Regression Tests
# =============================================================================

# 18. Credential Separation & Write Denial Tests
@pytest.mark.asyncio
async def test_credential_separation_and_write_blocking():
    """Verify that:
    1. An anon key CANNOT authorize write operations.
    2. Missing service role key raises a sanitized SupabaseClientError.
    3. Read operations use the anon key.
    4. Service role key is used strictly for writes when configured.
    """
    from app.db.supabase_client import SupabaseClientError

    # Client with ONLY anon key (no service role key)
    anon_only_client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="",
        anon_key="public-anon-key",
    )

    # Read header uses anon key
    read_headers = anon_only_client._get_headers(use_service_role=False)
    assert read_headers["apikey"] == "public-anon-key"
    assert "Bearer public-anon-key" in read_headers["Authorization"]

    # Write header raises SupabaseClientError
    with pytest.raises(SupabaseClientError) as exc_info:
        anon_only_client._get_headers(use_service_role=True)
    assert "service role credentials not configured" in str(exc_info.value)

    # All write methods block execution when service role key is absent
    with pytest.raises(SupabaseClientError):
        await anon_only_client.create_ingestion_run("ds-1", "loc-1", "key-1")

    with pytest.raises(SupabaseClientError):
        await anon_only_client.update_ingestion_run("run-1", "completed", 10, 10, 0)

    with pytest.raises(SupabaseClientError):
        await anon_only_client.persist_weather_observations([], "run-1")

    with pytest.raises(SupabaseClientError):
        await anon_only_client._persist_records_direct([], "run-1")

    # Client with separate service role key
    proper_client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="secret-service-role-key",
        anon_key="public-anon-key",
    )
    write_headers = proper_client._get_headers(use_service_role=True)
    assert write_headers["apikey"] == "secret-service-role-key"
    assert "Bearer secret-service-role-key" in write_headers["Authorization"]


# 19. Data Source Validation & Missing Handling Tests
@pytest.mark.asyncio
async def test_data_source_missing_stops_ingestion_safely():
    """Verify that missing or inaccessible data_source stops ingestion safely,
    raises a sanitized configuration error, and NEVER generates a fabricated UUID.
    """
    from app.db.supabase_client import SupabaseClientError

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    # Data source not found in database (returns None)
    mock_supabase.get_data_source_by_name.return_value = None

    service = IngestionService(supabase_client=mock_supabase)
    with pytest.raises(ValueError) as exc_info:
        await service.trigger_ingestion()
    assert "missing from the database" in str(exc_info.value)
    # Ensure no ingestion run was created with a fabricated UUID
    mock_supabase.create_ingestion_run.assert_not_called()

    # Data source lookup raises an error
    mock_supabase.get_data_source_by_name.side_effect = Exception("Connection lost")
    with pytest.raises(SupabaseClientError) as exc_info:
        await service.trigger_ingestion()
    assert "Data source lookup failed" in str(exc_info.value)
    mock_supabase.create_ingestion_run.assert_not_called()


# 20. Endpoint Security: Token Authentication for All Three Endpoints
@pytest.mark.asyncio
async def test_all_ingestion_endpoints_require_token_header(client: AsyncClient):
    """Verify that POST /trigger, GET /runs, and GET /runs/{run_id} all strictly require
    the X-Ingestion-Token header, rejecting missing tokens (401), wrong tokens (403),
    and query parameters (401).
    """
    settings = get_settings()
    endpoints = [
        ("POST", "/api/v1/ingestion/trigger"),
        ("GET", "/api/v1/ingestion/runs"),
        ("GET", "/api/v1/ingestion/runs/test-run-id"),
    ]

    for method, path in endpoints:
        # 1. Missing header -> 401
        if method == "POST":
            resp_no_token = await client.post(path)
        else:
            resp_no_token = await client.get(path)
        assert resp_no_token.status_code == 401, f"{method} {path} should return 401 without token"

        # 2. Invalid header -> 403
        headers_invalid = {"X-Ingestion-Token": "invalid-token-123"}
        if method == "POST":
            resp_bad_token = await client.post(path, headers=headers_invalid)
        else:
            resp_bad_token = await client.get(path, headers=headers_invalid)
        assert resp_bad_token.status_code == 403, f"{method} {path} should return 403 with wrong token"

        # 3. Query parameter rejection -> 401
        query_path = f"{path}?token={settings.INGESTION_TRIGGER_TOKEN}"
        if method == "POST":
            resp_query = await client.post(query_path)
        else:
            resp_query = await client.get(query_path)
        assert resp_query.status_code == 401, f"{method} {query_path} should return 401"

    # 4. Valid token succeeds for runs endpoints
    mock_service = AsyncMock(spec=IngestionService)
    from app.schemas.ingestion import IngestionRunResponse
    sample_run = IngestionRunResponse(
        id="run-1",
        data_source_id="ds-1",
        status="completed",
        started_at=datetime.now(timezone.utc),
        records_fetched=10,
        records_inserted=10,
        records_updated=0,
        created_at=datetime.now(timezone.utc),
    )
    mock_service.get_ingestion_runs.return_value = [sample_run]
    mock_service.get_ingestion_run_by_id.return_value = sample_run

    app.dependency_overrides[get_ingestion_service] = lambda: mock_service
    try:
        auth_header = {"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN}
        resp_runs = await client.get("/api/v1/ingestion/runs", headers=auth_header)
        assert resp_runs.status_code == 200
        assert len(resp_runs.json()) == 1

        resp_run_id = await client.get("/api/v1/ingestion/runs/run-1", headers=auth_header)
        assert resp_run_id.status_code == 200
        assert resp_run_id.json()["id"] == "run-1"
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)


# 21. Sanitization and Credential Redaction Tests
def test_sanitizer_removes_sensitive_information():
    """Verify that credentials, database connection strings, passwords, and SQL details
    are reliably redacted and truncated.
    """
    from app.core.sanitizer import sanitize_error_message, redact_headers

    raw_err = "Failed to connect to postgresql://admin:secretPass123@db.supabase.co:5432/postgres. Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"
    sanitized = sanitize_error_message(raw_err)
    assert "secretPass123" not in sanitized
    assert "admin" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "eyJhbGciOiJIUzI1NiJ9" not in sanitized

    schema_err = "duplicate key value violates unique constraint 'uq_observations_forecast_active'"
    sanitized_schema = sanitize_error_message(schema_err)
    assert "uq_observations_forecast_active" not in sanitized_schema
    assert "database constraint or schema violation" in sanitized_schema

    # Header redaction
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer super-secret",
        "apikey": "secret-apikey",
        "X-Ingestion-Token": "admin-token",
    }
    redacted = redact_headers(headers)
    assert redacted["Content-Type"] == "application/json"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["apikey"] == "[REDACTED]"
    assert redacted["X-Ingestion-Token"] == "[REDACTED]"


# 22. Ingestion Run Lifecycle Failure Stages
@pytest.mark.asyncio
async def test_ingestion_lifecycle_failure_stages():
    """Verify run lifecycle handling:
    1. If run creation fails, no update_ingestion_run is attempted.
    2. If persistence fails, records_fetched is preserved in the failed run record.
    3. Run update failure is logged and does not crash the location iteration.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    
    # 1. Run creation fails
    from app.db.supabase_client import SupabaseClientError
    mock_supabase.create_ingestion_run.side_effect = SupabaseClientError("DB down")

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion()

    assert result.status == "failed"
    assert result.failed_locations == 1
    # update_ingestion_run was NEVER called because run creation failed
    mock_supabase.update_ingestion_run.assert_not_called()

    # 2. Persistence fails after records fetched: records_fetched must be preserved!
    mock_supabase.create_ingestion_run.side_effect = None
    mock_supabase.create_ingestion_run.return_value = {"id": "run-created-123"}
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )
    mock_supabase.persist_weather_observations.side_effect = SupabaseClientError("RPC connection timeout")

    result2 = await service.trigger_ingestion()
    assert result2.status == "failed"
    assert result2.results[0].records_fetched == 4  # 1 analysis + 3 forecasts in sample payload
    assert result2.results[0].records_inserted == 0
    assert result2.results[0].records_updated == 0

    # Verify update_ingestion_run was called with records_fetched=4 preserved
    mock_supabase.update_ingestion_run.assert_called_once()
    call_kwargs = mock_supabase.update_ingestion_run.call_args.kwargs
    assert call_kwargs["run_id"] == "run-created-123"
    assert call_kwargs["status"] == "failed"
    assert call_kwargs["records_fetched"] == 4
    assert call_kwargs["records_inserted"] == 0


# 23. Direct Fallback Partial Index Matching and Error Handling
@pytest.mark.asyncio
async def test_direct_fallback_query_matching_and_error_handling():
    """Verify that _persist_records_direct:
    1. Distinguishes active forecast (forecast_reference_time IS NULL) from cycle forecast.
    2. Raises SupabaseClientError on GET, POST, or PATCH failure.
    3. Handles 409 conflict and raises if recovery fails.
    """
    from app.db.supabase_client import SupabaseClientError
    mock_client = SupabaseClient(supabase_url="https://test.supabase.co", service_role_key="test-key")
    mock_req = httpx.Request("GET", "https://test.supabase.co")

    # 1. GET failure raises SupabaseClientError
    with patch("httpx.AsyncClient.get", return_value=Response(500, request=mock_req)):
        with pytest.raises(SupabaseClientError) as exc_info:
            await mock_client._persist_records_direct(
                [{"location_id": "l1", "data_source_id": "ds1", "observation_type": "analysis", "measurement_type": "rainfall", "valid_at": "2026-11-18T12:00:00Z", "value": 1.0, "unit": "mm", "fetched_at": "2026-11-18T10:00:00Z"}],
                "run-1"
            )
        assert "Direct observation query failed" in str(exc_info.value)

    # 2. PATCH failure raises SupabaseClientError
    with patch("httpx.AsyncClient.get", return_value=Response(200, request=mock_req, json=[{"id": "obs-1", "value": 0.0, "unit": "mm"}])):
        with patch("httpx.AsyncClient.patch", return_value=Response(500, request=mock_req)):
            with pytest.raises(SupabaseClientError) as exc_info:
                await mock_client._persist_records_direct(
                    [{"location_id": "l1", "data_source_id": "ds1", "observation_type": "analysis", "measurement_type": "rainfall", "valid_at": "2026-11-18T12:00:00Z", "value": 1.0, "unit": "mm", "fetched_at": "2026-11-18T10:00:00Z"}],
                    "run-1"
                )
            assert "Direct observation patch failed" in str(exc_info.value)

    # 3. POST failure (non-409) raises SupabaseClientError
    with patch("httpx.AsyncClient.get", return_value=Response(200, request=mock_req, json=[])):
        with patch("httpx.AsyncClient.post", return_value=Response(500, request=mock_req)):
            with pytest.raises(SupabaseClientError) as exc_info:
                await mock_client._persist_records_direct(
                    [{"location_id": "l1", "data_source_id": "ds1", "observation_type": "analysis", "measurement_type": "rainfall", "valid_at": "2026-11-18T12:00:00Z", "value": 1.0, "unit": "mm", "fetched_at": "2026-11-18T10:00:00Z"}],
                    "run-1"
                )
            assert "Direct observation insert failed" in str(exc_info.value)

    # 4. Partial index query targeting verification
    recorded_urls = []
    async def capture_get(url, *args, **kwargs):
        recorded_urls.append(url)
        return Response(200, request=mock_req, json=[])

    with patch("httpx.AsyncClient.get", side_effect=capture_get):
        with patch("httpx.AsyncClient.post", return_value=Response(201, request=mock_req, json={})):
            # Active forecast (forecast_reference_time is None)
            await mock_client._persist_records_direct(
                [{"location_id": "l1", "data_source_id": "ds1", "observation_type": "forecast", "measurement_type": "rainfall", "valid_at": "2026-11-18T12:00:00Z", "forecast_reference_time": None, "value": 1.0, "unit": "mm", "fetched_at": "2026-11-18T10:00:00Z"}],
                "run-1"
            )
            assert "forecast_reference_time=is.null" in recorded_urls[0]

            # Cycle forecast (forecast_reference_time supplied)
            await mock_client._persist_records_direct(
                [{"location_id": "l1", "data_source_id": "ds1", "observation_type": "forecast", "measurement_type": "rainfall", "valid_at": "2026-11-18T12:00:00Z", "forecast_reference_time": "2026-11-18T00:00:00Z", "value": 1.0, "unit": "mm", "fetched_at": "2026-11-18T10:00:00Z"}],
                "run-1"
            )
            assert "forecast_reference_time=eq.2026-11-18T00:00:00Z" in recorded_urls[1]


# 24. Migration 003 SQL Structural & Validation Checks
def test_migration_003_sql_structure():
    """Verify that Migration 003 contains all required transactional and validation elements."""
    with open("database/migrations/003_weather_ingestion_rpc.sql", "r", encoding="utf-8") as f:
        sql = f.read()

    # Transaction wrapping
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "SET LOCAL search_path = public, extensions;" in sql
    assert "SECURITY INVOKER" in sql
    assert "SET search_path = public, extensions, pg_temp" in sql

    # Access control
    assert "REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM PUBLIC;" in sql
    assert "REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM anon;" in sql
    assert "REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM authenticated;" in sql
    assert "GRANT EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) TO service_role;" in sql

    # Defensive validation points
    assert "p_ingestion_run_id is required" in sql
    assert "observations payload cannot be null" in sql
    assert "must be a JSON array" in sql
    assert "observations array cannot be empty" in sql
    assert "location_id is required" in sql
    assert "data_source_id is required" in sql
    assert "unauthorized observation_type" in sql
    assert "unsupported measurement_type" in sql
    assert "valid_at timestamp is required" in sql
    assert "value cannot be NaN or non-finite" in sql
    assert "rainfall value cannot be negative" in sql
    assert "unsupported unit" in sql
    assert "metadata must be a JSON object" in sql

    # Partial index conflict predicates
    assert "WHERE observation_type = 'analysis'" in sql
    assert "WHERE observation_type = 'forecast' AND forecast_reference_time IS NULL" in sql
    assert "WHERE observation_type = 'forecast' AND forecast_reference_time IS NOT NULL" in sql


# 25. Migration 004 Least-Privilege Permissions Structural Checks
def test_migration_004_sql_structure():
    """Verify that Migration 004 correctly enforces least-privilege security model."""
    with open("database/migrations/004_least_privilege_permissions.sql", "r", encoding="utf-8") as f:
        sql = f.read()

    # Transaction wrapping & search_path
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "SET LOCAL search_path = public, extensions;" in sql

    # Fail-closed preflight check for global default ACLs
    assert "d.defaclnamespace = 0" in sql
    assert "aclexplode(d.defaclacl)" in sql
    assert "Preflight check failed: Insecure global default table ACL detected!" in sql

    # Row Level Security enabled on all seven tables
    for tbl in [
        "basins", "districts", "data_sources", "monitoring_locations",
        "ingestion_runs", "observations", "forecast_observation_revisions"
    ]:
        assert f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY;" in sql

    # Revoke ALL from PUBLIC
    for tbl in [
        "basins", "districts", "data_sources", "monitoring_locations",
        "ingestion_runs", "observations", "forecast_observation_revisions"
    ]:
        assert f"REVOKE ALL ON TABLE public.{tbl} FROM PUBLIC;" in sql

    # Public reference tables: SELECT only for anon and authenticated
    for ref_tbl in ["basins", "districts", "data_sources"]:
        assert f"GRANT SELECT ON TABLE public.{ref_tbl} TO anon;" in sql
        assert f"GRANT SELECT ON TABLE public.{ref_tbl} TO authenticated;" in sql

    # Service-role operational privileges
    assert "GRANT SELECT ON TABLE public.monitoring_locations TO service_role;" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE public.ingestion_runs TO service_role;" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE public.observations TO service_role;" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.forecast_observation_revisions TO service_role;" in sql

    # Function permissions: Trigger function revoked from service_role, PUBLIC, anon, authenticated
    assert "REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM PUBLIC;" in sql
    assert "REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM service_role;" in sql
    assert "GRANT EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) TO service_role;" in sql
    assert "REVOKE ALL ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM PUBLIC;" in sql

    # Column-level privilege hardening for PUBLIC, anon, authenticated, and service_role
    assert "grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role')" in sql

    # Default privileges hardening for schema public with authorization guard
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;" in sql
    assert "pg_has_role(CURRENT_USER, v_role, 'MEMBER')" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC" in sql
    assert "Skipping default privileges configuration for role %" in sql


# 26. Private Table Reads Use Service-Role Permissions
@pytest.mark.asyncio
async def test_private_table_reads_use_service_role():
    """Verify that backend queries for private operational tables (monitoring_locations,
    observations, ingestion_runs) explicitly use the service-role credential.
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
        anon_key="anon-public-key",
    )
    mock_req = httpx.Request("GET", "https://test.supabase.co")
    captured_auth_headers = []

    async def capture_get(url, headers=None, *args, **kwargs):
        captured_auth_headers.append(headers.get("Authorization", ""))
        return Response(200, request=mock_req, json=[])

    with patch("httpx.AsyncClient.get", side_effect=capture_get):
        # 1. Pilot locations (private table: monitoring_locations)
        await client.get_pilot_locations()
        assert "Bearer service-role-secret" in captured_auth_headers[-1]

        # 2. Latest observations (private table: observations)
        await client.get_latest_observations_for_location("loc-1")
        assert "Bearer service-role-secret" in captured_auth_headers[-1]

        # 3. Ingestion runs (private table: ingestion_runs)
        await client.get_ingestion_runs()
        assert "Bearer service-role-secret" in captured_auth_headers[-1]

        # 4. Ingestion run by ID (private table: ingestion_runs)
        await client.get_ingestion_run_by_id("run-1")
        assert "Bearer service-role-secret" in captured_auth_headers[-1]


# =============================================================================
# 27. Phase 2.3.3.3: Same-Hour Retry Recovery & Ingestion Reliability Tests
# Note: These are unit tests mocking PostgREST HTTP responses and asserting
# application-level orchestration, idempotency key generation, retry behavior,
# and conflict handling. They test application reliability and contract fidelity,
# NOT live PostgreSQL engine state.
# =============================================================================

@pytest.mark.asyncio
async def test_first_ingestion_attempt_creates_run():
    """Requirement 1: Verify first ingestion attempt creates a run with a distinct
    attempt key and preserves cycle_key in metadata.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-initial-001", "status": "started"}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    assert result.status == "completed"
    assert result.successful_locations == 1
    assert result.failed_locations == 0

    # Verify create_ingestion_run call signature
    mock_supabase.create_ingestion_run.assert_called_once()
    create_kwargs = mock_supabase.create_ingestion_run.call_args.kwargs
    attempt_key = create_kwargs["idempotency_key"]
    metadata = create_kwargs["metadata"]

    # Verify attempt_key format: openmeteo_{loc_id}_{cycle_key}_run_{uuid}
    import re
    loc_id = SAMPLE_PILOT_LOCATIONS[0]["id"]
    pattern = rf"^openmeteo_{re.escape(loc_id)}_\d{{10}}_run_[a-f0-9]{{12}}$"
    assert re.match(pattern, attempt_key), f"Attempt key '{attempt_key}' does not match pattern '{pattern}'"

    # Verify cycle_key is stored in metadata
    assert "cycle_key" in metadata
    assert len(metadata["cycle_key"]) == 10  # YYYYMMDDHH format

    # Verify update_ingestion_run marked completed
    mock_supabase.update_ingestion_run.assert_called_once()
    update_kwargs = mock_supabase.update_ingestion_run.call_args.kwargs
    assert update_kwargs["run_id"] == "run-initial-001"
    assert update_kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_first_attempt_followed_by_same_hour_retry_succeeds_with_distinct_runs():
    """Requirements 2 & 3: Verify failed first attempt followed by a second attempt
    in the same hour creates a new run with a distinct attempt key, succeeds independently,
    and preserves both runs historically without overwriting.
    """
    mock_db: Dict[str, Dict[str, Any]] = {}
    created_keys: List[str] = []

    async def mock_create_run(data_source_id, location_id, idempotency_key, metadata=None, **kwargs):
        run_id = f"run-mock-{len(mock_db) + 1}"
        created_keys.append(idempotency_key)
        rec = {
            "id": run_id,
            "data_source_id": data_source_id,
            "location_id": location_id,
            "status": "started",
            "idempotency_key": idempotency_key,
            "metadata": metadata or {},
        }
        mock_db[run_id] = rec
        return rec

    async def mock_update_run(run_id, status, records_fetched, records_inserted, records_updated, **kwargs):
        if run_id in mock_db:
            mock_db[run_id]["status"] = status
            mock_db[run_id]["records_fetched"] = records_fetched
            mock_db[run_id]["records_inserted"] = records_inserted
            mock_db[run_id]["records_updated"] = records_updated
            if "error_message" in kwargs:
                mock_db[run_id]["error_message"] = kwargs["error_message"]
        return mock_db.get(run_id, {})

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.side_effect = mock_create_run
    mock_supabase.update_ingestion_run.side_effect = mock_update_run

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)

    # Attempt 1: Upstream provider failure (e.g. timeout / 504)
    mock_connector.fetch_precipitation.side_effect = ProviderException("Upstream gateway timeout", status_code=504)

    result_1 = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])
    assert result_1.status == "failed"
    assert result_1.failed_locations == 1
    assert len(mock_db) == 1

    run_1_id = "run-mock-1"
    assert mock_db[run_1_id]["status"] == "failed"
    assert "ProviderException" in mock_db[run_1_id]["error_message"]

    # Attempt 2: Same hour retry (recovering from failure)
    mock_connector.fetch_precipitation.side_effect = None
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }

    result_2 = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])
    assert result_2.status == "completed"
    assert result_2.successful_locations == 1
    assert result_2.failed_locations == 0
    assert len(mock_db) == 2

    run_2_id = "run-mock-2"
    assert mock_db[run_2_id]["status"] == "completed"
    assert mock_db[run_2_id]["records_inserted"] == 4

    # Requirement 3 Assertions: Distinct keys, same cycle_key, historical record preserved
    key_1 = created_keys[0]
    key_2 = created_keys[1]
    assert key_1 != key_2, f"Attempt keys must be distinct! key_1={key_1}, key_2={key_2}"

    # Both share identical logical cycle_key
    cycle_1 = mock_db[run_1_id]["metadata"]["cycle_key"]
    cycle_2 = mock_db[run_2_id]["metadata"]["cycle_key"]
    assert cycle_1 == cycle_2, f"Cycle keys must match! cycle_1={cycle_1}, cycle_2={cycle_2}"

    # Historical failed run was NOT overwritten or corrupted
    assert mock_db[run_1_id]["status"] == "failed"
    assert mock_db[run_2_id]["status"] == "completed"


@pytest.mark.asyncio
async def test_repeated_attempts_across_different_locations_do_not_collide():
    """Requirement 4: Verify repeated attempts across different pilot locations
    generate distinct attempt keys without cross-location collisions.
    """
    created_keys: List[str] = []

    async def mock_create(data_source_id, location_id, idempotency_key, metadata=None, **kwargs):
        created_keys.append(idempotency_key)
        return {"id": f"run-{location_id}", "status": "started"}

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = SAMPLE_PILOT_LOCATIONS  # All 3 pilot locations
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.side_effect = mock_create
    mock_supabase.update_ingestion_run.return_value = {}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion()

    assert result.status == "completed"
    assert result.total_locations == 3
    assert result.successful_locations == 3
    assert len(created_keys) == 3

    # Ensure all 3 keys are unique
    assert len(set(created_keys)) == 3

    # Ensure each key embeds its specific location_id
    for loc, key in zip(SAMPLE_PILOT_LOCATIONS, created_keys):
        assert loc["id"] in key


@pytest.mark.asyncio
async def test_ambiguous_post_failure_retries_with_identical_attempt_key():
    """Requirement 5: Verify that when an ambiguous network timeout or connection error
    occurs during run creation, the retry reuses the exact same attempt key.
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/ingestion_runs")
    posted_payloads: List[Dict[str, Any]] = []

    call_count = 0

    async def mock_post(url, json=None, headers=None, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        posted_payloads.append(dict(json))
        if call_count == 1:
            # Ambiguous failure: network timeout waiting for response
            raise httpx.ConnectTimeout("Connection timed out waiting for PostgREST response")
        # Retry succeeds
        return Response(
            201,
            request=mock_req,
            json=[{"id": "run-recovered-post", "status": "started", "idempotency_key": json["idempotency_key"]}],
        )

    test_key = "openmeteo_loc-test_2026111914_run_ambiguous01"
    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        result = await client.create_ingestion_run(
            data_source_id="ds-1",
            location_id="loc-test",
            idempotency_key=test_key,
            max_retries=1,
        )

    assert result["id"] == "run-recovered-post"
    assert call_count == 2
    assert len(posted_payloads) == 2

    # CRITICAL: Both initial attempt and retry must use the IDENTICAL attempt key
    assert posted_payloads[0]["idempotency_key"] == test_key
    assert posted_payloads[1]["idempotency_key"] == test_key


@pytest.mark.asyncio
async def test_create_ingestion_run_http_409_handled_safely_without_false_success():
    """Requirement 6: Verify HTTP 409 Conflict is handled safely and diagnostically,
    never falsely reporting success, never blind-merging, and failing safely at service level.
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/ingestion_runs")

    async def mock_post_409(url, json=None, headers=None, *args, **kwargs):
        return Response(
            409,
            request=mock_req,
            json={"message": "duplicate key value violates unique constraint uq_ingestion_runs_idempotency_key"},
        )

    # When lookup after 409 finds no matching record: raises controlled 409 error
    async def mock_get_empty(url, headers=None, *args, **kwargs):
        return Response(200, request=mock_req, json=[])

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), \
         patch("httpx.AsyncClient.get", side_effect=mock_get_empty):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run(
                data_source_id="ds-1",
                location_id="loc-test",
                idempotency_key="openmeteo_loc-test_2026111914_run_conflict01",
            )
        assert exc_info.value.status_code == 409
        assert "Conflict resolution failed" in exc_info.value.message

    # 2. Service level handles 409 safely without falsely reporting success
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.side_effect = SupabaseClientError(
        "Duplicate ingestion run conflict: idempotency key already exists (HTTP 409).",
        status_code=409,
    )

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)

    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])
    assert result.status == "failed"
    assert result.failed_locations == 1
    assert result.successful_locations == 0
    assert result.results[0].status == IngestionStatus.FAILED
    assert "Duplicate ingestion run conflict" in (result.results[0].error_message or "")

    # update_ingestion_run must NOT be called since run creation was rejected
    mock_supabase.update_ingestion_run.assert_not_called()


# =============================================================================
# 28. Phase 2.3.3.3: Ambiguous POST Recovery (Test Group B) - Mandatory
# =============================================================================

@pytest.mark.asyncio
async def test_ambiguous_post_outcome_successfully_recovers_original_run_id():
    """Test Group B (Core): Verify the exact Issue B recovery workflow:
    1. First POST /rest/v1/ingestion_runs is committed by server, but client times out.
    2. Second POST retries with identical attempt key and payload.
    3. Server returns HTTP 409.
    4. Client queries existing run using exact attempt key with service-role permissions.
    5. Existing 'started' run record is verified and original run ID is recovered.
    6. Ingestion continues and completes using original run without duplicate creation.
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_post_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/ingestion_runs")
    mock_get_req = httpx.Request("GET", "https://test.supabase.co/rest/v1/ingestion_runs")

    test_attempt_key = "openmeteo_loc-1_2026111914_run_recover123"
    expected_run_id = "run-recovered-original-uuid-999"

    # Simulated server database state
    server_committed_record = {
        "id": expected_run_id,
        "data_source_id": "ds-1",
        "location_id": "loc-1",
        "status": "started",
        "idempotency_key": test_attempt_key,
        "records_fetched": 0,
        "records_inserted": 0,
        "records_updated": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"cycle_key": "2026111914"},
    }

    post_call_count = 0
    get_call_count = 0

    async def mock_post(url, json=None, headers=None, *args, **kwargs):
        nonlocal post_call_count
        post_call_count += 1
        if post_call_count == 1:
            # 1. Server commits record, but network drops before response is delivered
            raise httpx.ReadTimeout("Read timeout waiting for PostgREST response")
        # 2. Retry POST receives HTTP 409 duplicate key conflict from PostgreSQL
        return Response(
            409,
            request=mock_post_req,
            json={"message": f"duplicate key value violates unique constraint on {test_attempt_key}"},
        )

    async def mock_get(url, headers=None, *args, **kwargs):
        nonlocal get_call_count
        get_call_count += 1
        # 3. Client queries existing run by attempt key using service role
        assert f"idempotency_key=eq.{test_attempt_key}" in url
        assert headers.get("Authorization") == "Bearer service-role-secret"
        return Response(200, request=mock_get_req, json=[server_committed_record])

    with patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("httpx.AsyncClient.get", side_effect=mock_get):
        recovered_rec = await client.create_ingestion_run(
            data_source_id="ds-1",
            location_id="loc-1",
            idempotency_key=test_attempt_key,
            max_retries=1,
        )

    # 4. Assertions: Exact recovery of original run ID
    assert post_call_count == 2
    assert get_call_count == 1
    assert recovered_rec["id"] == expected_run_id
    assert recovered_rec["status"] == "started"
    assert recovered_rec["idempotency_key"] == test_attempt_key

    # 5. Full IngestionService integration with ambiguous POST recovery
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    # create_ingestion_run returns recovered record
    mock_supabase.create_ingestion_run.return_value = recovered_rec
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    assert result.status == "completed"
    assert result.successful_locations == 1
    assert result.results[0].status == IngestionStatus.COMPLETED

    # Verify observations persisted with the recovered run ID
    mock_supabase.persist_weather_observations.assert_called_once()
    assert mock_supabase.persist_weather_observations.call_args.kwargs["ingestion_run_id"] == expected_run_id

    # Verify status update targeted the recovered run ID
    mock_supabase.update_ingestion_run.assert_called_once()
    assert mock_supabase.update_ingestion_run.call_args.kwargs["run_id"] == expected_run_id
    assert mock_supabase.update_ingestion_run.call_args.kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_ambiguous_post_conflict_classification_edge_cases():
    """Test Group B (Edge Cases): Verify all conflict classification branches:
    - Wrong location ID
    - Wrong data source ID
    - Lookup timeout
    - Multiple matching records (data integrity error)
    - Existing completed run
    - Existing failed run
    - Connection failure before commit
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/ingestion_runs")

    async def mock_post_409(url, json=None, headers=None, *args, **kwargs):
        return Response(409, request=mock_req, json={"message": "conflict"})

    test_key = "openmeteo_loc-1_2026111914_run_edge001"

    # Edge Case 1: Wrong location ID
    async def get_wrong_loc(url, headers=None, *args, **kwargs):
        return Response(
            200,
            request=mock_req,
            json=[{"id": "run-1", "data_source_id": "ds-1", "location_id": "loc-WRONG", "status": "started", "idempotency_key": test_key}],
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_wrong_loc):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 409
        assert "different location" in exc_info.value.message

    # Edge Case 2: Wrong data source ID
    async def get_wrong_ds(url, headers=None, *args, **kwargs):
        return Response(
            200,
            request=mock_req,
            json=[{"id": "run-1", "data_source_id": "ds-WRONG", "location_id": "loc-1", "status": "started", "idempotency_key": test_key}],
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_wrong_ds):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 409
        assert "different data source" in exc_info.value.message

    # Edge Case 3: Lookup timeout during conflict verification
    async def get_timeout(url, headers=None, *args, **kwargs):
        raise httpx.ReadTimeout("Timeout querying existing run")

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_timeout):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 504
        assert "Database lookup failed during conflict verification" in exc_info.value.message

    # Edge Case 4: Multiple matching records (data integrity violation)
    async def get_multiple(url, headers=None, *args, **kwargs):
        return Response(
            200,
            request=mock_req,
            json=[
                {"id": "run-1", "data_source_id": "ds-1", "location_id": "loc-1", "status": "started", "idempotency_key": test_key},
                {"id": "run-2", "data_source_id": "ds-1", "location_id": "loc-1", "status": "started", "idempotency_key": test_key},
            ],
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_multiple):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 500
        assert "Data integrity error" in exc_info.value.message

    # Edge Case 5: Existing record with completed status (rejects silent rerun)
    async def get_completed(url, headers=None, *args, **kwargs):
        return Response(
            200,
            request=mock_req,
            json=[{"id": "run-1", "data_source_id": "ds-1", "location_id": "loc-1", "status": "completed", "idempotency_key": test_key}],
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_completed):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 409
        assert "already completed" in exc_info.value.message

    # Edge Case 6: Existing record with failed status (requires fresh attempt key)
    async def get_failed(url, headers=None, *args, **kwargs):
        return Response(
            200,
            request=mock_req,
            json=[{"id": "run-1", "data_source_id": "ds-1", "location_id": "loc-1", "status": "failed", "idempotency_key": test_key}],
        )

    with patch("httpx.AsyncClient.post", side_effect=mock_post_409), patch("httpx.AsyncClient.get", side_effect=get_failed):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key)
        assert exc_info.value.status_code == 409
        assert "already failed" in exc_info.value.message

    # Edge Case 7: Network failure before any database commit (both initial and retry fail)
    async def post_all_fail(url, json=None, headers=None, *args, **kwargs):
        raise httpx.ConnectError("Connection refused by database proxy")

    with patch("httpx.AsyncClient.post", side_effect=post_all_fail):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", test_key, max_retries=1)
        assert exc_info.value.status_code == 503
        assert "Network error or timeout" in exc_info.value.message


# =============================================================================
# 29. Phase 2.3.3.3: Run Lifecycle & State Management (Test Group C)
# =============================================================================

@pytest.mark.asyncio
async def test_run_lifecycle_status_update_failure_does_not_report_success():
    """Test Group C: Verify that when observation persistence succeeds, but the final
    status update to 'completed' fails, the service never falsely reports success.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-lifecycle-001", "status": "started"}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 4,
        "records_updated": 0,
    }
    # Final status update to completed fails (e.g. database network disruption)
    mock_supabase.update_ingestion_run.side_effect = SupabaseClientError(
        "Connection reset while updating status to completed", status_code=503
    )

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    # CRITICAL: Must be reported as failed, never completed!
    assert result.status == "failed"
    assert result.successful_locations == 0
    assert result.failed_locations == 1
    assert result.results[0].status == IngestionStatus.FAILED
    assert "Connection reset" in (result.results[0].error_message or "")


# =============================================================================
# 30. Phase 2.3.3.3: Observation Idempotency Application-Level Tests (Test Group D)
# Note: These application-level tests verify payload construction and RPC response
# handling. Database trigger behavior (e.g. trg_archive_forecast_revision) is defined
# in PostgreSQL migrations 002/003 and validated in integration environments.
# =============================================================================

@pytest.mark.asyncio
async def test_observation_idempotency_analysis_and_forecast_upsert_counters():
    """Test Group D: Verify application-level observation persistence handles
    re-ingestion counters accurately for both analysis and forecast records.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-idemp-001", "status": "started"}

    # Repeated identical observation persistence: 0 inserted, 4 updated
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 0,
        "records_updated": 4,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion(location_id=SAMPLE_PILOT_LOCATIONS[0]["id"])

    assert result.status == "completed"
    assert result.total_records_inserted == 0
    assert result.total_records_updated == 4


@pytest.mark.asyncio
async def test_observation_missing_forecast_values_omitted_not_zeroed():
    """Test Group D: Verify missing forecast timestamps (None values within acceptable
    missing ratio <= 50%) are omitted from database payloads and NEVER converted to 0.0 mm.
    """
    payload_with_none = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "interval": 900, "precipitation": 1.5},
        "hourly": {
            "time": ["2026-11-17T12:00", "2026-11-17T13:00", "2026-11-17T14:00"],
            "precipitation": [0.5, None, 2.0],  # 1 missing out of 3 (33.3% <= 50%)
        },
    }
    connector = OpenMeteoWeatherConnector()
    data = connector._transform_precipitation_payload(payload_with_none, 20.316, 86.611)

    # 1 analysis + 2 valid forecasts (the None timestamp at 13:00 must be omitted)
    assert len(data.forecast_records) == 2
    for fc in data.forecast_records:
        assert fc.value is not None
        assert fc.value in [0.5, 2.0]
        # None value was NOT coerced to 0.0
        assert fc.valid_at.isoformat() != "2026-11-17T13:00:00+00:00"


@pytest.mark.asyncio
async def test_observation_genuine_zero_rainfall_preserved():
    """Test Group D: Verify genuine zero rainfall readings (0.0 mm) are preserved
    as 0.0 and distinguished from missing data.
    """
    payload_with_zero = {
        "latitude": 20.316,
        "longitude": 86.611,
        "timezone": "UTC",
        "current": {"time": "2026-11-17T12:00", "interval": 900, "precipitation": 0.0},
        "hourly": {
            "time": ["2026-11-17T12:00", "2026-11-17T13:00"],
            "precipitation": [0.0, 0.0],
        },
    }
    connector = OpenMeteoWeatherConnector()
    data = connector._transform_precipitation_payload(payload_with_zero, 20.316, 86.611)

    assert data.analysis_record.value == 0.0
    assert len(data.forecast_records) == 2
    for fc in data.forecast_records:
        assert fc.value == 0.0


# =============================================================================
# 31. Phase 2.3.3.3: Retry Limits, Security & Inspection (Test Group E)
# =============================================================================

@pytest.mark.asyncio
async def test_create_ingestion_run_retry_limit_exhausted():
    """Test Group E: Verify bounded retry attempts in create_ingestion_run.
    max_retries=1 means exactly 2 POST attempts, preventing infinite loops.
    """
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    post_count = 0

    async def mock_post_timeout(url, json=None, headers=None, *args, **kwargs):
        nonlocal post_count
        post_count += 1
        raise httpx.ReadTimeout("Persistent network timeout")

    with patch("httpx.AsyncClient.post", side_effect=mock_post_timeout):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", "test-key-exhaust", max_retries=1)
        assert exc_info.value.status_code == 504
        assert post_count == 2  # Attempt 0 + 1 retry


@pytest.mark.asyncio
async def test_create_ingestion_run_fail_fast_on_client_errors():
    """Test Group E: Verify HTTP 400, 401, 403 fail immediately without retrying."""
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("POST", "https://test.supabase.co/rest/v1/ingestion_runs")
    post_count = 0

    async def mock_post_401(url, json=None, headers=None, *args, **kwargs):
        nonlocal post_count
        post_count += 1
        return Response(401, request=mock_req, json={"message": "invalid JWT"})

    with patch("httpx.AsyncClient.post", side_effect=mock_post_401):
        with pytest.raises(SupabaseClientError) as exc_info:
            await client.create_ingestion_run("ds-1", "loc-1", "test-key-401", max_retries=2)
        assert exc_info.value.status_code == 401
        assert post_count == 1  # Fails fast on attempt 0


@pytest.mark.asyncio
async def test_get_ingestion_run_by_idempotency_key_and_stale_detection():
    """Test Group E: Verify get_ingestion_run_by_idempotency_key and get_stale_started_runs."""
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("GET", "https://test.supabase.co/rest/v1/ingestion_runs")

    # 1. get_ingestion_run_by_idempotency_key found
    async def mock_get_single(url, headers=None, *args, **kwargs):
        return Response(200, request=mock_req, json=[{"id": "run-found-1", "status": "started"}])

    with patch("httpx.AsyncClient.get", side_effect=mock_get_single):
        rec = await client.get_ingestion_run_by_idempotency_key("openmeteo_loc-1_2026111914_run_abc")
        assert rec is not None
        assert rec["id"] == "run-found-1"

    # 2. get_ingestion_run_by_idempotency_key not found
    async def mock_get_empty(url, headers=None, *args, **kwargs):
        return Response(200, request=mock_req, json=[])

    with patch("httpx.AsyncClient.get", side_effect=mock_get_empty):
        rec2 = await client.get_ingestion_run_by_idempotency_key("openmeteo_loc-1_2026111914_run_nonexistent")
        assert rec2 is None

    # 3. get_stale_started_runs queries started runs older than cutoff
    captured_urls = []
    async def mock_get_stale(url, headers=None, *args, **kwargs):
        captured_urls.append(url)
        return Response(200, request=mock_req, json=[{"id": "run-stale-1", "status": "started"}])

    with patch("httpx.AsyncClient.get", side_effect=mock_get_stale):
        stale_runs = await client.get_stale_started_runs(cutoff_minutes=60)
        assert len(stale_runs) == 1
        assert "status=eq.started" in captured_urls[0]
        assert "started_at=lt." in captured_urls[0]



@pytest.mark.asyncio
async def test_mixed_location_success_and_failure_preserves_accurate_counters():
    """Requirement 7: Verify mixed location success and failure scenario preserves
    accurate run counters (fetched, inserted, updated) and produces 'partial' overall status.
    """
    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = SAMPLE_PILOT_LOCATIONS  # 3 locations
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-1", "name": "open_meteo_weather"}

    # Mock run creation for all 3
    mock_supabase.create_ingestion_run.side_effect = [
        {"id": "run-loc-1", "status": "started"},
        {"id": "run-loc-2", "status": "started"},
        {"id": "run-loc-3", "status": "started"},
    ]

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    sample_data = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.316, 86.611
    )

    # Loc 1: Fetch succeeds
    # Loc 2: Fetch fails (timeout)
    # Loc 3: Fetch succeeds
    mock_connector.fetch_precipitation.side_effect = [
        sample_data,
        ProviderTimeoutException("Open-Meteo request timed out after 10.0s"),
        sample_data,
    ]

    # Loc 1: Persist succeeds (4 fetched, 3 inserted, 1 updated)
    # Loc 3: Persist fails (database write error, 4 fetched, 0 inserted, 0 updated)
    mock_supabase.persist_weather_observations.side_effect = [
        {"records_fetched": 4, "records_inserted": 3, "records_updated": 1},
        SupabaseClientError("RPC connection pool exhausted", status_code=503),
    ]
    mock_supabase.update_ingestion_run.return_value = {}

    service = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    result = await service.trigger_ingestion()

    # Overall summary verification
    assert result.status == "partial"
    assert result.total_locations == 3
    assert result.successful_locations == 1
    assert result.failed_locations == 2

    # Aggregated counters verification:
    # Loc 1 fetched 4, inserted 3, updated 1.
    # Loc 2 failed at fetch. Loc 3 failed at persist.
    # total_records_fetched aggregates successfully persisted records (4)
    assert result.total_records_fetched == 4
    assert result.total_records_inserted == 3
    assert result.total_records_updated == 1

    # Individual location results verification
    assert result.results[0].status == IngestionStatus.COMPLETED
    assert result.results[0].records_fetched == 4
    assert result.results[0].records_inserted == 3
    assert result.results[0].records_updated == 1

    assert result.results[1].status == IngestionStatus.FAILED
    assert result.results[1].records_fetched == 0
    assert result.results[1].records_inserted == 0
    assert result.results[1].records_updated == 0

    assert result.results[2].status == IngestionStatus.FAILED
    assert result.results[2].records_fetched == 4  # Preserved genuine fetch count
    assert result.results[2].records_inserted == 0
    assert result.results[2].records_updated == 0



