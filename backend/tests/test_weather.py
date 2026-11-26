from unittest.mock import AsyncMock
import httpx
import pytest
from httpx import AsyncClient

from app.api.v1.routes.weather import get_weather_service
from app.ingestion.open_meteo import (
    OpenMeteoWeatherConnector,
    ProviderException,
    ProviderTimeoutException,
)
from app.main import app
from app.schemas.weather import WeatherPreviewResponse, DataTypeInfo, HourlyForecastPoint
from app.services.weather_service import SAMPLE_PILOT_COORDINATES, WeatherService


MOCK_PAYLOAD = {
    "latitude": 20.316,
    "longitude": 86.611,
    "generationtime_ms": 0.12,
    "utc_offset_seconds": 0,
    "timezone": "UTC",
    "timezone_abbreviation": "UTC",
    "elevation": 5.0,
    "current": {
        "time": "2026-11-17T12:00",
        "interval": 900,
        "temperature_2m": 29.2,
        "precipitation": 0.0,
    },
    "hourly": {
        "time": [
            "2026-11-17T00:00",
            "2026-11-17T01:00",
            "2026-11-17T02:00",
        ],
        "precipitation": [0.0, 1.2, 3.5],
        "rain": [0.0, 1.0, 3.2],
        "precipitation_probability": [5, 20, 60],
    },
}


@pytest.mark.asyncio
async def test_weather_input_validation(client: AsyncClient):
    """Test validation errors for missing and out-of-range latitude/longitude coordinates."""
    # Missing coordinates
    resp = await client.get("/api/v1/weather/preview")
    assert resp.status_code == 422

    # Latitude out of bounds (> 90)
    resp = await client.get("/api/v1/weather/preview?latitude=95.0&longitude=86.611")
    assert resp.status_code == 422

    # Longitude out of bounds (< -180)
    resp = await client.get("/api/v1/weather/preview?latitude=20.316&longitude=-185.0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_connector_unit_transformation():
    """Unit test OpenMeteoWeatherConnector payload transformation without hitting network."""
    connector = OpenMeteoWeatherConnector()
    result = connector._transform_payload(MOCK_PAYLOAD, 20.316, 86.611)

    assert result.provider == "Open-Meteo Weather API"
    assert result.latitude == 20.316
    assert result.longitude == 86.611
    assert result.elevation_m == 5.0
    assert result.current_temperature_c == 29.2
    assert result.current_precipitation_mm == 0.0

    assert result.data_type_info.measurement_type == "rainfall"
    assert result.data_type_info.precipitation_unit == "mm"
    assert result.data_type_info.temperature_unit == "°C"

    assert len(result.hourly_rainfall_forecast) == 3
    assert result.hourly_rainfall_forecast[0].time == "2026-11-17T00:00"
    assert result.hourly_rainfall_forecast[0].precipitation_mm == 0.0
    assert result.hourly_rainfall_forecast[0].precipitation_probability_pct == 5.0
    assert result.hourly_rainfall_forecast[2].precipitation_mm == 3.5
    assert "solely for API connectivity testing" in result.prototype_notice


@pytest.mark.asyncio
async def test_weather_preview_endpoint_success(client: AsyncClient):
    """Test /api/v1/weather/preview route with mocked weather service."""
    connector = OpenMeteoWeatherConnector()
    mocked_preview = connector._transform_payload(MOCK_PAYLOAD, 20.316, 86.611)

    mock_service = AsyncMock(spec=WeatherService)
    mock_service.get_preview.return_value = mocked_preview

    app.dependency_overrides[get_weather_service] = lambda: mock_service

    try:
        paradeep = SAMPLE_PILOT_COORDINATES["paradeep"]
        resp = await client.get(
            f"/api/v1/weather/preview?latitude={paradeep['latitude']}&longitude={paradeep['longitude']}"
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["provider"] == "Open-Meteo Weather API"
        assert data["latitude"] == 20.316
        assert data["longitude"] == 86.611
        assert data["elevation_m"] == 5.0
        assert data["current_temperature_c"] == 29.2
        assert data["current_precipitation_mm"] == 0.0
        assert len(data["hourly_rainfall_forecast"]) == 3
        assert data["data_type_info"]["measurement_type"] == "rainfall"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_weather_connector_timeout(client: AsyncClient):
    """Test handling of upstream provider timeout through FastAPI route."""
    mock_service = AsyncMock(spec=WeatherService)
    mock_service.get_preview.side_effect = ProviderTimeoutException(
        "Open-Meteo request timed out after 10.0s"
    )

    app.dependency_overrides[get_weather_service] = lambda: mock_service

    try:
        resp = await client.get("/api/v1/weather/preview?latitude=20.316&longitude=86.611")
        assert resp.status_code == 504
        detail = resp.json().get("detail", {})
        assert detail.get("error") == "provider_timeout"
        assert "timed out" in detail.get("message")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_weather_connector_upstream_500(client: AsyncClient):
    """Test handling of upstream provider 500 error through FastAPI route."""
    mock_service = AsyncMock(spec=WeatherService)
    mock_service.get_preview.side_effect = ProviderException(
        "Open-Meteo returned status 500: Internal Server Error", status_code=502
    )

    app.dependency_overrides[get_weather_service] = lambda: mock_service

    try:
        resp = await client.get("/api/v1/weather/preview?latitude=20.316&longitude=86.611")
        assert resp.status_code == 502
        detail = resp.json().get("detail", {})
        assert detail.get("error") == "provider_error"
        assert "returned status 500" in detail.get("message")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_live_open_meteo_integration():
    """Live integration test against Open-Meteo API using sample pilot coordinates.
    
    Tests real network connectivity when online. If offline or connection fails,
    skips gracefully without failing local test runs.
    """
    paradeep = SAMPLE_PILOT_COORDINATES["paradeep"]
    connector = OpenMeteoWeatherConnector(timeout_seconds=8.0)

    try:
        result = await connector.fetch_weather_preview(
            latitude=paradeep["latitude"],
            longitude=paradeep["longitude"],
            forecast_days=2,
        )
        assert result.provider == "Open-Meteo Weather API"
        assert result.latitude == pytest.approx(paradeep["latitude"], rel=1e-1)
        assert result.longitude == pytest.approx(paradeep["longitude"], rel=1e-1)
        assert len(result.hourly_rainfall_forecast) > 0
        assert result.data_type_info.measurement_type == "rainfall"
        assert result.data_type_info.precipitation_unit == "mm"
    except (ProviderException, ProviderTimeoutException) as exc:
        pytest.skip(f"Live network unavailable or provider error: {exc}")
