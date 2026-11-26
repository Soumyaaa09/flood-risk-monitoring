import asyncio
from datetime import datetime, timezone
import email.utils
import logging
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.http_clients import get_open_meteo_http_client
from app.schemas.weather import DataTypeInfo, HourlyForecastPoint, WeatherPreviewResponse

logger = logging.getLogger(__name__)


class ProviderException(Exception):
    """Base exception for upstream provider communication errors."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProviderTimeoutException(ProviderException):
    """Exception raised when an upstream provider times out."""

    def __init__(self, message: str = "Upstream weather provider timed out"):
        super().__init__(message=message, status_code=504)


class ProviderValidationException(ProviderException):
    """Exception raised when provider returns unparseable or physically invalid data."""

    def __init__(self, message: str = "Invalid response from weather provider"):
        super().__init__(message=message, status_code=502)


class OpenMeteoCurrent(BaseModel):
    time: str
    interval: Optional[int] = None
    precipitation: float = Field(..., ge=0.0, description="Current precipitation accumulation (mm)")

    @field_validator("precipitation")
    @classmethod
    def validate_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"Precipitation cannot be negative: {v}")
        return v


class OpenMeteoHourly(BaseModel):
    time: List[str]
    precipitation: List[Optional[float]]


class OpenMeteoForecastPayload(BaseModel):
    latitude: float
    longitude: float
    elevation: Optional[float] = None
    generationtime_ms: Optional[float] = None
    utc_offset_seconds: int = 0
    timezone: str
    current: Optional[OpenMeteoCurrent] = None
    hourly: Optional[OpenMeteoHourly] = None


class StandardizedPrecipitationRecord(BaseModel):
    valid_at: datetime
    fetched_at: datetime
    forecast_reference_time: Optional[datetime] = None
    observation_type: str  # 'analysis' or 'forecast'
    measurement_type: str = "rainfall"
    value: float
    unit: str = "mm"
    quality_flag: str = "unverified"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OpenMeteoPrecipitationData(BaseModel):
    latitude: float
    longitude: float
    elevation_m: Optional[float] = None
    fetched_at: datetime
    analysis_record: Optional[StandardizedPrecipitationRecord] = None
    forecast_records: List[StandardizedPrecipitationRecord] = Field(default_factory=list)


class OpenMeteoWeatherConnector:
    """Connector for the Open-Meteo Weather Forecast API.
    
    Provides two operational interfaces:
    1. fetch_precipitation: High-reliability pipeline for Phase 2.3.2 real-weather ingestion,
       supporting exponential backoff, bounded retries, and scientific data classification.
    2. fetch_weather_preview: Fast preview interface for API preview endpoints and UI verification.
    """

    PROVIDER_NAME = "Open-Meteo Weather API"

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        backoff_factor: Optional[float] = None,
        max_wait_seconds: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.OPEN_METEO_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.OPEN_METEO_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.OPEN_METEO_MAX_RETRIES
        self.backoff_factor = backoff_factor if backoff_factor is not None else settings.OPEN_METEO_BACKOFF_FACTOR
        self.max_wait_seconds = (
            max_wait_seconds
            if max_wait_seconds is not None
            else getattr(settings, "OPEN_METEO_MAX_WAIT_SECONDS", 60.0)
        )
        self._http_client = http_client

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return get_open_meteo_http_client()

    # =========================================================================
    # Phase 2.3.2: Real Weather Ingestion Methods
    # =========================================================================

    async def fetch_precipitation(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
    ) -> OpenMeteoPrecipitationData:
        """Fetch precipitation from Open-Meteo with retry and exponential backoff.
        
        Args:
            latitude: Latitude between -90 and 90
            longitude: Longitude between -180 and 180
            forecast_days: Days of forecast (default 3)
            
        Returns:
            OpenMeteoPrecipitationData containing analysis and future forecast records.
        """
        if not (-90.0 <= latitude <= 90.0):
            raise ProviderValidationException(f"Latitude out of bounds [-90, 90]: {latitude}")
        if not (-180.0 <= longitude <= 180.0):
            raise ProviderValidationException(f"Longitude out of bounds [-180, 180]: {longitude}")

        endpoint = f"{self.base_url}/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "precipitation",
            "hourly": "precipitation",
            "timezone": "UTC",
            "forecast_days": min(max(forecast_days, 1), 7),
            "past_days": 0,
        }

        raw_payload = await self._execute_request_with_retry(endpoint, params)
        return self._transform_precipitation_payload(
            raw_payload, latitude, longitude, forecast_days=forecast_days
        )

    def _parse_retry_after(self, header_value: Optional[str]) -> Optional[float]:
        """Parse HTTP Retry-After header into seconds.
        
        Supports both integer/float seconds and HTTP-date formats (RFC 7231 / RFC 2822).
        Returns None if header is missing, malformed, or unparseable.
        """
        if not header_value:
            return None
        cleaned = header_value.strip()

        # 1. Try numeric seconds (e.g. "5", "10.5")
        try:
            val = float(cleaned)
            return max(0.0, val)
        except ValueError:
            pass

        # 2. Try HTTP-date format (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
        try:
            target_dt = email.utils.parsedate_to_datetime(cleaned)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            delay = (target_dt - now_utc).total_seconds()
            return max(0.0, delay)
        except Exception:
            return None

    async def _execute_request_with_retry(
        self, endpoint: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute HTTP GET with bounded exponential backoff and rate-limit handling on transient errors."""
        attempt = 0
        last_exception: Optional[Exception] = None

        client = self._get_http_client()
        while attempt <= self.max_retries:
            try:
                response = await client.get(endpoint, params=params, timeout=self.timeout_seconds)

                # 1. Dedicated handling for HTTP 429 Too Many Requests
                if response.status_code == 429:
                    attempt += 1
                    retry_after_raw = response.headers.get("Retry-After")
                    parsed_delay = self._parse_retry_after(retry_after_raw)

                    if parsed_delay is not None and parsed_delay > self.max_wait_seconds:
                        raise ProviderException(
                            f"Open-Meteo rate limit (HTTP 429): requested Retry-After delay "
                            f"({parsed_delay:.1f}s) exceeds maximum allowed wait ({self.max_wait_seconds}s)",
                            status_code=429,
                        )

                    if parsed_delay is not None:
                        sleep_duration = parsed_delay
                        delay_source = f"Retry-After header ({parsed_delay:.1f}s)"
                    else:
                        sleep_duration = self.backoff_factor * (2 ** (attempt - 1))
                        delay_source = f"exponential backoff ({sleep_duration:.2f}s)"

                    if attempt <= self.max_retries:
                        logger.warning(
                            f"Open-Meteo rate limit (HTTP 429) on attempt {attempt}. "
                            f"Retrying in {delay_source}..."
                        )
                        await asyncio.sleep(sleep_duration)
                        continue
                    else:
                        raise ProviderException(
                            f"Open-Meteo rate limit (HTTP 429) retries exhausted after {self.max_retries} attempts",
                            status_code=429,
                        )

                # 2. Fail fast on other 4xx client errors (do not retry permanent errors)
                if 400 <= response.status_code < 500:
                    raise ProviderException(
                        f"Open-Meteo client error {response.status_code}: {response.text}",
                        status_code=response.status_code,
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as exc:
                last_exception = ProviderTimeoutException(
                    f"Open-Meteo request timed out after {self.timeout_seconds}s: {str(exc)}"
                )
            except httpx.HTTPStatusError as exc:
                last_exception = ProviderException(
                    f"Open-Meteo upstream error {exc.response.status_code}: {exc.response.text}",
                    status_code=502,
                )
            except httpx.RequestError as exc:
                last_exception = ProviderException(
                    f"Open-Meteo network connection error: {str(exc)}",
                    status_code=502,
                )

            attempt += 1
            if attempt <= self.max_retries:
                sleep_duration = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    f"Open-Meteo attempt {attempt} failed ({last_exception.message}). Retrying in {sleep_duration:.2f}s..."
                )
                await asyncio.sleep(sleep_duration)

        raise last_exception or ProviderException("Failed to fetch data from Open-Meteo after retries")

    def _transform_precipitation_payload(
        self,
        raw_payload: Dict[str, Any],
        requested_lat: float,
        requested_lon: float,
        forecast_days: int = 3,
    ) -> OpenMeteoPrecipitationData:
        """Validate raw provider response and classify into analysis and forecast records."""
        try:
            validated = OpenMeteoForecastPayload.model_validate(raw_payload)
        except Exception as exc:
            raise ProviderValidationException(
                f"Open-Meteo payload schema validation failed: {str(exc)}"
            ) from exc

        fetched_at = datetime.now(timezone.utc)
        analysis_record: Optional[StandardizedPrecipitationRecord] = None
        forecast_records: List[StandardizedPrecipitationRecord] = []

        # 1. Current analysis (Model-derived nowcast)
        analysis_dt: Optional[datetime] = None
        if validated.current is not None:
            try:
                time_str = validated.current.time
                if not time_str.endswith("Z") and "+" not in time_str:
                    time_str += "+00:00"
                analysis_dt = datetime.fromisoformat(time_str).astimezone(timezone.utc)
            except Exception as exc:
                raise ProviderValidationException(
                    f"Invalid current timestamp format '{validated.current.time}': {exc}"
                ) from exc

            analysis_record = StandardizedPrecipitationRecord(
                valid_at=analysis_dt,
                fetched_at=fetched_at,
                forecast_reference_time=None,
                observation_type="analysis",
                measurement_type="rainfall",
                value=round(validated.current.precipitation, 4),
                unit="mm",
                quality_flag="unverified",
                metadata={
                    "provider": self.PROVIDER_NAME,
                    "accumulation_interval_seconds": validated.current.interval or 900,
                    "accumulation_interval_desc": "15-minute model nowcast estimate",
                    "scientific_classification": "model_derived_analysis",
                    "disclaimer": "Model-derived estimate; NOT a physical ground gauge observation.",
                },
            )

        # 2. Hourly future forecasts (Filter out past hourly points and validate data gaps)
        if validated.hourly is not None:
            times = validated.hourly.time
            precips = validated.hourly.precipitation

            if len(times) != len(precips):
                raise ProviderValidationException(
                    f"Open-Meteo hourly timestamps count ({len(times)}) does not match precipitation count ({len(precips)})"
                )

            cutoff_dt = analysis_dt if analysis_dt is not None else fetched_at

            # Identify future forecast horizon indices
            future_indices: List[int] = []
            parsed_dts: Dict[int, datetime] = {}

            for i, time_str in enumerate(times):
                try:
                    iso_str = time_str if time_str.endswith("Z") or "+" in time_str else time_str + "+00:00"
                    valid_at_dt = datetime.fromisoformat(iso_str).astimezone(timezone.utc)
                    parsed_dts[i] = valid_at_dt
                except Exception as exc:
                    raise ProviderValidationException(
                        f"Invalid hourly timestamp format '{time_str}': {exc}"
                    ) from exc

                if valid_at_dt >= cutoff_dt:
                    future_indices.append(i)

            total_horizon_points = len(future_indices)
            if total_horizon_points == 0:
                raise ProviderValidationException(
                    "Open-Meteo forecast data gap: response contains no future forecast horizon points."
                )

            missing_count = 0
            valid_points: List[StandardizedPrecipitationRecord] = []

            for i in future_indices:
                p_val = precips[i]
                time_str = times[i]
                valid_at_dt = parsed_dts[i]

                # Distinguish missing (None) from genuine zero rainfall (0.0)
                if p_val is None:
                    missing_count += 1
                    continue

                if p_val < 0:
                    raise ProviderValidationException(
                        f"Hourly precipitation value cannot be negative: {p_val} at {time_str}"
                    )

                valid_points.append(
                    StandardizedPrecipitationRecord(
                        valid_at=valid_at_dt,
                        fetched_at=fetched_at,
                        forecast_reference_time=None,
                        observation_type="forecast",
                        measurement_type="rainfall",
                        value=round(float(p_val), 4),
                        unit="mm",
                        quality_flag="unverified",
                        metadata={
                            "provider": self.PROVIDER_NAME,
                            "accumulation_interval": "1_hour",
                            "scientific_classification": "numerical_weather_forecast",
                            "model_cycle": "unspecified_by_provider",
                        },
                    )
                )

            valid_count = len(valid_points)

            # Evaluate completeness across the actual forecast horizon
            if valid_count == 0:
                raise ProviderValidationException(
                    f"Open-Meteo forecast data gap: all {total_horizon_points} hourly precipitation "
                    f"values are null in the forecast horizon."
                )

            missing_ratio = missing_count / total_horizon_points
            if missing_ratio > 0.50:
                missing_pct = missing_ratio * 100.0
                raise ProviderValidationException(
                    f"Open-Meteo forecast data gap: {missing_count}/{total_horizon_points} "
                    f"({missing_pct:.1f}%) hourly precipitation values are missing in the forecast "
                    f"horizon (exceeds 50% threshold)."
                )

            if missing_count > 0:
                logger.warning(
                    "Open-Meteo partial forecast accepted: %d/%d (%.1f%%) missing points "
                    "(within 50%% tolerance) for (%.4f, %.4f).",
                    missing_count,
                    total_horizon_points,
                    missing_ratio * 100.0,
                    requested_lat,
                    requested_lon,
                )

            forecast_records.extend(valid_points)

        return OpenMeteoPrecipitationData(
            latitude=validated.latitude,
            longitude=validated.longitude,
            elevation_m=validated.elevation,
            fetched_at=fetched_at,
            analysis_record=analysis_record,
            forecast_records=forecast_records,
        )

    # =========================================================================
    # Phase 1: Preview Compatibility Interface
    # =========================================================================

    async def fetch_weather_preview(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
    ) -> WeatherPreviewResponse:
        """Fetch current conditions and hourly rainfall forecast for preview endpoints."""
        endpoint = f"{self.base_url}/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,precipitation",
            "hourly": "precipitation,rain,precipitation_probability",
            "forecast_days": min(max(forecast_days, 1), 7),
            "timezone": "UTC",
        }

        client = self._get_http_client()
        try:
            response = await client.get(endpoint, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutException(
                f"Open-Meteo request timed out after {self.timeout_seconds}s: {str(exc)}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = 502 if exc.response.status_code >= 500 else exc.response.status_code
            raise ProviderException(
                f"Open-Meteo returned status {exc.response.status_code}: {exc.response.text}",
                status_code=status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderException(
                f"Network error while connecting to Open-Meteo: {str(exc)}",
                status_code=502,
            ) from exc

        return self._transform_payload(payload, latitude, longitude)

    def _transform_payload(
        self, payload: Dict[str, Any], requested_lat: float, requested_lon: float
    ) -> WeatherPreviewResponse:
        """Transform Open-Meteo raw payload into standardized WeatherPreviewResponse."""
        fetch_ts = datetime.now(timezone.utc)
        current = payload.get("current", {})
        hourly = payload.get("hourly", {})

        times = hourly.get("time", [])
        precipitations = hourly.get("precipitation", [])
        rains = hourly.get("rain", [])
        probabilities = hourly.get("precipitation_probability", [])

        hourly_points = []
        for i, time_str in enumerate(times):
            p_val = float(precipitations[i]) if i < len(precipitations) and precipitations[i] is not None else 0.0
            r_val = float(rains[i]) if i < len(rains) and rains[i] is not None else None
            prob_val = (
                float(probabilities[i])
                if i < len(probabilities) and probabilities[i] is not None
                else None
            )
            hourly_points.append(
                HourlyForecastPoint(
                    time=time_str,
                    precipitation_mm=p_val,
                    rain_mm=r_val,
                    precipitation_probability_pct=prob_val,
                )
            )

        return WeatherPreviewResponse(
            provider=self.PROVIDER_NAME,
            fetch_timestamp=fetch_ts,
            latitude=payload.get("latitude", requested_lat),
            longitude=payload.get("longitude", requested_lon),
            elevation_m=payload.get("elevation"),
            current_temperature_c=current.get("temperature_2m"),
            current_precipitation_mm=current.get("precipitation"),
            data_type_info=DataTypeInfo(
                temperature_unit="°C",
                precipitation_unit="mm",
                measurement_type="rainfall",
                observation_nature="current_and_forecast",
            ),
            hourly_rainfall_forecast=hourly_points,
            prototype_notice=(
                "Sample coordinates are used solely for API connectivity testing. "
                "They do NOT represent official monitoring stations or flood gauge readings."
            ),
        )
