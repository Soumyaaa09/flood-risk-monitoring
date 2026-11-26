from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class HourlyForecastPoint(BaseModel):
    """Hourly forecast observation point."""

    time: str = Field(..., description="Timestamp in ISO 8601 string format (corresponds to valid_at in database observations)")
    precipitation_mm: float = Field(..., description="Precipitation depth in millimeters (mm)")
    rain_mm: Optional[float] = Field(None, description="Rainfall depth in millimeters (mm)")
    precipitation_probability_pct: Optional[float] = Field(
        None, description="Probability of precipitation in percentage (%)"
    )


class DataTypeInfo(BaseModel):
    """Metadata describing the data types and physical units."""

    temperature_unit: str = Field(default="°C", description="Unit of measurement for temperature")
    precipitation_unit: str = Field(default="mm", description="Unit of measurement for precipitation")
    measurement_type: str = Field(
        default="rainfall",
        description="Physical measurement category (distinguished from river water level and discharge)",
    )
    observation_nature: str = Field(
        default="current_and_forecast",
        description="Distinction between observed real-time and predicted values",
    )


class WeatherPreviewResponse(BaseModel):
    """Response schema for weather preview endpoint."""

    provider: str = Field(
        default="Open-Meteo Weather API",
        description="External upstream data provider name",
    )
    fetch_timestamp: datetime = Field(
        ...,
        description="UTC timestamp when data was fetched from provider (corresponds to fetched_at in database observations)",
    )
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    elevation_m: Optional[float] = Field(None, description="Elevation above sea level in meters")
    current_temperature_c: Optional[float] = Field(
        None, description="Current ambient temperature in degrees Celsius"
    )
    current_precipitation_mm: Optional[float] = Field(
        None, description="Current precipitation rate/accumulation in millimeters (mm)"
    )
    data_type_info: DataTypeInfo = Field(
        default_factory=DataTypeInfo,
        description="Detailed metadata about units and measurement types",
    )
    hourly_rainfall_forecast: List[HourlyForecastPoint] = Field(
        default_factory=list,
        description="Hourly precipitation forecast for up to 48-72 hours",
    )
    prototype_notice: str = Field(
        default="Sample coordinates are used solely for API connectivity testing. They do NOT represent official monitoring stations or flood gauge readings.",
        description="Prototype notice clarifying sample nature of coordinates",
    )
