from typing import Optional
from app.ingestion.open_meteo import OpenMeteoWeatherConnector
from app.schemas.weather import WeatherPreviewResponse


# Sample reference coordinates for testing API connectivity in pilot regions.
# NOTE: These are approximate geometric centroid coordinates used purely for API connectivity testing.
# They are NOT official gauge locations or CWC stations.
SAMPLE_PILOT_COORDINATES = {
    "paradeep": {"latitude": 20.316, "longitude": 86.611, "district": "Jagatsinghpur"},
    "cuttack": {"latitude": 20.462, "longitude": 85.882, "district": "Cuttack"},
    "kendrapara": {"latitude": 20.501, "longitude": 86.422, "district": "Kendrapara"},
}


class WeatherService:
    """Service layer coordinating weather data retrieval and validation."""

    def __init__(self, connector: Optional[OpenMeteoWeatherConnector] = None):
        self.connector = connector or OpenMeteoWeatherConnector()

    async def get_preview(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
    ) -> WeatherPreviewResponse:
        """Retrieve weather preview for given coordinates."""
        return await self.connector.fetch_weather_preview(
            latitude=latitude,
            longitude=longitude,
            forecast_days=forecast_days,
        )
