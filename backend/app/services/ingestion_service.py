from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import get_settings
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.ingestion.open_meteo import OpenMeteoWeatherConnector
from app.schemas.ingestion import (
    FreshnessState,
    IngestionLocationResult,
    IngestionRunResponse,
    IngestionStatus,
    IngestionTriggerResponse,
    LocationLatestWeather,
    LocationSummary,
    ObservationRecord,
    SourceHealthResponse,
)
from app.services.data_quality import DataQualityValidator, FreshnessEvaluator

logger = logging.getLogger(__name__)


class IngestionService:
    """Service orchestrating real-weather ingestion, transformation, and query operations."""

    def __init__(
        self,
        supabase_client: Optional[SupabaseClient] = None,
        weather_connector: Optional[OpenMeteoWeatherConnector] = None,
    ):
        self.supabase = supabase_client or SupabaseClient()
        self.weather_connector = weather_connector or OpenMeteoWeatherConnector()
        self.settings = get_settings()

    @staticmethod
    def generate_attempt_key(
        location_id: str, cycle_key: str, attempt_id: Optional[str] = None
    ) -> str:
        """Generate a unique attempt key for an ingestion run attempt.

        Format: openmeteo_{location_id}_{cycle_key}_run_{attempt_id}
        Preserves the stable logical cycle_key while ensuring every distinct
        attempt receives a globally unique attempt identifier.
        """
        token = attempt_id or uuid.uuid4().hex[:12]
        return f"openmeteo_{location_id}_{cycle_key}_run_{token}"

    async def trigger_ingestion(
        self, location_id: Optional[str] = None
    ) -> IngestionTriggerResponse:
        """Trigger ingestion for pilot locations.
        
        Fetches precipitation from Open-Meteo, classifies data into analysis and forecast records,
        persists records atomically to Supabase, and updates operational audit counters.
        Continues processing subsequent locations if any individual location fails.
        """
        trigger_time = datetime.now(timezone.utc)
        results: List[IngestionLocationResult] = []

        # 1. Fetch pilot locations
        all_locations = await self.supabase.get_pilot_locations()
        if not all_locations:
            logger.warning("No pilot monitoring locations registered in Supabase.")

        target_locations = all_locations
        if location_id:
            target_locations = [loc for loc in all_locations if loc["id"] == location_id]
            if not target_locations:
                raise ValueError(f"Specified location_id '{location_id}' not found in pilot monitoring locations.")

        # 2. Fetch Open-Meteo data source record
        # CRITICAL FIX: The data source must genuinely exist in Supabase. Never fabricate a random UUID!
        try:
            data_source = await self.supabase.get_data_source_by_name("open_meteo_weather")
        except Exception as exc:
            logger.error("Failed to query data source 'open_meteo_weather': %s", exc.__class__.__name__)
            raise SupabaseClientError(
                "Data source lookup failed. Unable to verify weather data source configuration.",
                status_code=500,
            ) from exc

        if not data_source or not data_source.get("id"):
            logger.error("Required data source 'open_meteo_weather' not found in database.")
            raise ValueError(
                "Configured weather data source 'open_meteo_weather' is missing from the database."
            )

        data_source_id = str(data_source["id"])

        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        success_count = 0
        failed_count = 0

        # Deterministic cycle key for idempotency (hourly granularity)
        cycle_key = trigger_time.strftime("%Y%m%d%H")

        for loc in target_locations:
            loc_id = loc["id"]
            loc_name = loc["name"]
            district_name = (
                loc.get("districts", {}).get("name")
                if isinstance(loc.get("districts"), dict)
                else None
            )
            lat = float(loc["latitude"])
            lon = float(loc["longitude"])
            attempt_key = self.generate_attempt_key(loc_id, cycle_key)

            created_run_id: Optional[str] = None
            rec_fetched = 0
            rec_inserted = 0
            rec_updated = 0
            freshness_dt: Optional[datetime] = None

            try:
                # 3. Create ingestion run in 'started' state
                run_rec = await self.supabase.create_ingestion_run(
                    data_source_id=data_source_id,
                    location_id=loc_id,
                    idempotency_key=attempt_key,
                    metadata={
                        "location_name": loc_name,
                        "latitude": lat,
                        "longitude": lon,
                        "cycle_key": cycle_key,
                    },
                )
                if not run_rec or "id" not in run_rec:
                    raise SupabaseClientError(
                        f"Failed to create ingestion run record for location '{loc_name}'."
                    )
                created_run_id = str(run_rec["id"])

                # 4. Fetch precipitation data from Open-Meteo
                data = await self.weather_connector.fetch_precipitation(
                    latitude=lat,
                    longitude=lon,
                    forecast_days=3,
                )

                # Retrieval freshness timestamp is data.fetched_at (NOT valid_at)
                freshness_dt = data.fetched_at

                # 5. Transform standardized records into database payloads
                observation_payloads: List[Dict[str, Any]] = []

                # Add analysis record (current model nowcast)
                if data.analysis_record:
                    observation_payloads.append({
                        "location_id": loc_id,
                        "data_source_id": data_source_id,
                        "observation_type": data.analysis_record.observation_type,
                        "measurement_type": data.analysis_record.measurement_type,
                        "valid_at": data.analysis_record.valid_at.isoformat(),
                        "fetched_at": data.analysis_record.fetched_at.isoformat(),
                        "forecast_reference_time": None,
                        "value": data.analysis_record.value,
                        "unit": data.analysis_record.unit,
                        "quality_flag": data.analysis_record.quality_flag,
                        "metadata": data.analysis_record.metadata,
                    })

                # Add future hourly forecast records
                for fc in data.forecast_records:
                    observation_payloads.append({
                        "location_id": loc_id,
                        "data_source_id": data_source_id,
                        "observation_type": fc.observation_type,
                        "measurement_type": fc.measurement_type,
                        "valid_at": fc.valid_at.isoformat(),
                        "fetched_at": fc.fetched_at.isoformat(),
                        "forecast_reference_time": None,
                        "value": fc.value,
                        "unit": fc.unit,
                        "quality_flag": fc.quality_flag,
                        "metadata": fc.metadata,
                    })

                rec_fetched = len(observation_payloads)

                # 6. Persist records to Supabase
                counts = await self.supabase.persist_weather_observations(
                    observations=observation_payloads,
                    ingestion_run_id=created_run_id,
                )

                rec_fetched = counts.get("records_fetched", rec_fetched)
                rec_inserted = counts.get("records_inserted", 0)
                rec_updated = counts.get("records_updated", 0)

                # 7. Update ingestion run to completed
                await self.supabase.update_ingestion_run(
                    run_id=created_run_id,
                    status=IngestionStatus.COMPLETED.value,
                    records_fetched=rec_fetched,
                    records_inserted=rec_inserted,
                    records_updated=rec_updated,
                    data_freshness_timestamp=freshness_dt,
                )

                total_fetched += rec_fetched
                total_inserted += rec_inserted
                total_updated += rec_updated
                success_count += 1

                results.append(
                    IngestionLocationResult(
                        location_id=loc_id,
                        location_name=loc_name,
                        district_name=district_name,
                        status=IngestionStatus.COMPLETED,
                        records_fetched=rec_fetched,
                        records_inserted=rec_inserted,
                        records_updated=rec_updated,
                        data_freshness_timestamp=freshness_dt,
                    )
                )

            except Exception as exc:
                failed_count += 1
                raw_error = f"{exc.__class__.__name__}: {str(exc)}"
                clean_error = sanitize_error_message(raw_error) or "Ingestion failed"
                logger.error("Ingestion failed for location '%s': %s", loc_name, clean_error)

                # Only update ingestion run if it was actually created in the database
                if created_run_id:
                    try:
                        await self.supabase.update_ingestion_run(
                            run_id=created_run_id,
                            status=IngestionStatus.FAILED.value,
                            records_fetched=rec_fetched,  # Preserves genuine fetched count before persistence failed
                            records_inserted=0,
                            records_updated=0,
                            data_freshness_timestamp=freshness_dt,
                            error_message=clean_error,
                        )
                    except Exception as inner_exc:
                        logger.error(
                            "Failed to update failed status for run '%s': %s",
                            created_run_id,
                            inner_exc.__class__.__name__,
                        )

                results.append(
                    IngestionLocationResult(
                        location_id=loc_id,
                        location_name=loc_name,
                        district_name=district_name,
                        status=IngestionStatus.FAILED,
                        records_fetched=rec_fetched,
                        records_inserted=0,
                        records_updated=0,
                        error_message=clean_error,
                    )
                )

        if len(target_locations) == 0:
            status_summary = "completed"
        elif failed_count == 0 and success_count > 0:
            status_summary = "completed"
        elif success_count > 0 and failed_count > 0:
            status_summary = "partial"
        else:
            status_summary = "failed"

        # Phase 2.7.2: Trigger alert evaluation for successfully ingested locations
        if success_count > 0:
            try:
                from app.services.alert_service import get_alert_service

                alert_svc = get_alert_service()
                await alert_svc.evaluate_post_ingestion(results)
            except Exception as eval_exc:
                logger.warning(
                    "Pipeline alert evaluation error in manual ingestion trigger: %s",
                    eval_exc.__class__.__name__,
                )

        return IngestionTriggerResponse(
            status=status_summary,
            trigger_timestamp=trigger_time,
            total_locations=len(target_locations),
            successful_locations=success_count,
            failed_locations=failed_count,
            total_records_fetched=total_fetched,
            total_records_inserted=total_inserted,
            total_records_updated=total_updated,
            results=results,
        )

    def calculate_freshness(
        self,
        data_timestamp: Optional[datetime],
        reference_time: Optional[datetime] = None,
    ) -> FreshnessState:
        """Evaluate pipeline ingestion retrieval freshness (fetched_at) via centralized policy."""
        state, _ = FreshnessEvaluator.evaluate_ingestion(
            data_timestamp,
            reference_time=reference_time,
            settings=self.settings,
        )
        return state

    async def get_locations(self) -> List[LocationSummary]:
        """Fetch all pilot monitoring locations."""
        locations_data = await self.supabase.get_pilot_locations()
        summaries: List[LocationSummary] = []
        for loc in locations_data:
            d_name = (
                loc.get("districts", {}).get("name")
                if isinstance(loc.get("districts"), dict)
                else None
            )
            summaries.append(
                LocationSummary(
                    id=loc["id"],
                    district_id=loc["district_id"],
                    district_name=d_name,
                    basin_id=loc.get("basin_id"),
                    name=loc["name"],
                    location_type=loc["location_type"],
                    latitude=float(loc["latitude"]),
                    longitude=float(loc["longitude"]),
                    elevation_m=loc.get("elevation_m"),
                    is_official_gauge=loc.get("is_official_gauge", False),
                    metadata=loc.get("metadata", {}),
                )
            )
        return summaries

    async def get_latest_weather_for_location(self, location_id: str) -> LocationLatestWeather:
        """Fetch current analysis and active forecast summary for a location."""
        locations = await self.supabase.get_pilot_locations()
        matched = [l for l in locations if l["id"] == location_id]
        if not matched:
            raise ValueError(f"Monitoring location with ID '{location_id}' not found.")

        loc = matched[0]
        d_name = (
            loc.get("districts", {}).get("name")
            if isinstance(loc.get("districts"), dict)
            else None
        )

        obs_data = await self.supabase.get_latest_observations_for_location(location_id=location_id)
        raw_analysis = obs_data.get("analysis")
        raw_forecasts = obs_data.get("forecasts", [])

        analysis_record: Optional[ObservationRecord] = None
        freshness_ts: Optional[datetime] = None

        if raw_analysis:
            v_dt, _ = DataQualityValidator.validate_timestamp(raw_analysis.get("valid_at"), field_name="analysis.valid_at")
            f_dt, _ = DataQualityValidator.validate_timestamp(raw_analysis.get("fetched_at"), field_name="analysis.fetched_at")
            # CRITICAL: Ingestion freshness is evaluated from retrieval timestamp (fetched_at)
            freshness_ts = f_dt
            if v_dt and f_dt and raw_analysis.get("value") is not None:
                analysis_record = ObservationRecord(
                    id=raw_analysis.get("id"),
                    location_id=location_id,
                    data_source_id=raw_analysis["data_source_id"],
                    ingestion_run_id=raw_analysis.get("ingestion_run_id"),
                    valid_at=v_dt,
                    fetched_at=f_dt,
                    forecast_reference_time=None,
                    observation_type="analysis",
                    measurement_type="rainfall",
                    value=float(raw_analysis["value"]),
                    unit=raw_analysis.get("unit", "mm"),
                    quality_flag=raw_analysis.get("quality_flag", "unverified"),
                    metadata=raw_analysis.get("metadata", {}),
                )

        # Calculate next 24h accumulated forecast precipitation
        next_24h_precip = 0.0
        now_utc = datetime.now(timezone.utc)
        valid_forecast_count = 0

        for fc in raw_forecasts:
            fc_dt = datetime.fromisoformat(fc["valid_at"].replace("Z", "+00:00"))
            if 0 <= (fc_dt - now_utc).total_seconds() <= 86400:
                next_24h_precip += float(fc["value"])
                valid_forecast_count += 1

        return LocationLatestWeather(
            location_id=location_id,
            location_name=loc["name"],
            district_name=d_name,
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
            freshness=self.calculate_freshness(freshness_ts),
            data_freshness_timestamp=freshness_ts,
            current_analysis=analysis_record,
            active_forecast_count=len(raw_forecasts),
            forecast_horizon_hours=len(raw_forecasts),
            next_24h_precipitation_mm=round(next_24h_precip, 2) if valid_forecast_count > 0 else None,
        )

    async def get_observations_for_location(
        self, location_id: str, limit: int = 72
    ) -> List[ObservationRecord]:
        """Fetch chronological observations and forecasts for a location."""
        obs_data = await self.supabase.get_latest_observations_for_location(
            location_id=location_id, limit=limit
        )
        records: List[ObservationRecord] = []

        if obs_data.get("analysis"):
            raw = obs_data["analysis"]
            records.append(
                ObservationRecord(
                    id=raw.get("id"),
                    location_id=location_id,
                    data_source_id=raw["data_source_id"],
                    ingestion_run_id=raw.get("ingestion_run_id"),
                    valid_at=datetime.fromisoformat(raw["valid_at"].replace("Z", "+00:00")),
                    fetched_at=datetime.fromisoformat(raw["fetched_at"].replace("Z", "+00:00")),
                    forecast_reference_time=None,
                    observation_type="analysis",
                    measurement_type="rainfall",
                    value=float(raw["value"]),
                    unit=raw.get("unit", "mm"),
                    quality_flag=raw.get("quality_flag", "unverified"),
                    metadata=raw.get("metadata", {}),
                )
            )

        for fc in obs_data.get("forecasts", []):
            records.append(
                ObservationRecord(
                    id=fc.get("id"),
                    location_id=location_id,
                    data_source_id=fc["data_source_id"],
                    ingestion_run_id=fc.get("ingestion_run_id"),
                    valid_at=datetime.fromisoformat(fc["valid_at"].replace("Z", "+00:00")),
                    fetched_at=datetime.fromisoformat(fc["fetched_at"].replace("Z", "+00:00")),
                    forecast_reference_time=None,
                    observation_type="forecast",
                    measurement_type="rainfall",
                    value=float(fc["value"]),
                    unit=fc.get("unit", "mm"),
                    quality_flag=fc.get("quality_flag", "unverified"),
                    metadata=fc.get("metadata", {}),
                )
            )

        return records

    async def get_ingestion_runs(self, limit: int = 20) -> List[IngestionRunResponse]:
        """Fetch recent ingestion runs with sanitized operational details."""
        runs = await self.supabase.get_ingestion_runs(limit=limit)
        return [
            IngestionRunResponse(
                id=r["id"],
                data_source_id=r["data_source_id"],
                location_id=r.get("location_id"),
                status=r["status"],
                started_at=datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")),
                completed_at=(
                    datetime.fromisoformat(r["completed_at"].replace("Z", "+00:00"))
                    if r.get("completed_at")
                    else None
                ),
                records_fetched=r.get("records_fetched", 0),
                records_inserted=r.get("records_inserted", 0),
                records_updated=r.get("records_updated", 0),
                data_freshness_timestamp=(
                    datetime.fromisoformat(r["data_freshness_timestamp"].replace("Z", "+00:00"))
                    if r.get("data_freshness_timestamp")
                    else None
                ),
                idempotency_key=r.get("idempotency_key"),
                error_message=sanitize_error_message(r.get("error_message")),
                metadata=r.get("metadata", {}),
                created_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
            )
            for r in runs
        ]

    async def get_ingestion_run_by_id(self, run_id: str) -> Optional[IngestionRunResponse]:
        """Fetch specific ingestion run with sanitized operational details."""
        r = await self.supabase.get_ingestion_run_by_id(run_id)
        if not r:
            return None
        return IngestionRunResponse(
            id=r["id"],
            data_source_id=r["data_source_id"],
            location_id=r.get("location_id"),
            status=r["status"],
            started_at=datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")),
            completed_at=(
                datetime.fromisoformat(r["completed_at"].replace("Z", "+00:00"))
                if r.get("completed_at")
                else None
            ),
            records_fetched=r.get("records_fetched", 0),
            records_inserted=r.get("records_inserted", 0),
            records_updated=r.get("records_updated", 0),
            data_freshness_timestamp=(
                datetime.fromisoformat(r["data_freshness_timestamp"].replace("Z", "+00:00"))
                if r.get("data_freshness_timestamp")
                else None
            ),
            idempotency_key=r.get("idempotency_key"),
            error_message=sanitize_error_message(r.get("error_message")),
            metadata=r.get("metadata", {}),
            created_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
        )

    async def get_sources_health(self) -> SourceHealthResponse:
        """Health check for external weather provider and database connection."""
        from app.db.session import check_database_connectivity

        service_role_set = bool(self.settings.SUPABASE_SERVICE_ROLE_KEY.strip())

        # Perform genuine read-only database connectivity verification
        db_check = await check_database_connectivity(timeout_seconds=5.0)
        supabase_status = db_check.get("status", "disconnected")

        # Check Open-Meteo connectivity with sample Paradeep coordinate
        open_meteo_status = "operational"
        try:
            await self.weather_connector.fetch_precipitation(
                latitude=20.3160, longitude=86.6110, forecast_days=1
            )
        except Exception as exc:
            open_meteo_status = f"degraded: {exc.__class__.__name__}"

        db_healthy = supabase_status == "connected"
        weather_healthy = open_meteo_status == "operational"
        overall_status = "healthy" if (db_healthy and weather_healthy) else "degraded"

        return SourceHealthResponse(
            status=overall_status,
            provider="Open-Meteo & Supabase PostgREST",
            connectivity=f"Database: {supabase_status}; WeatherAPI: {open_meteo_status}",
            timestamp=datetime.now(timezone.utc),
            service_role_configured=service_role_set,
            details={
                "open_meteo_base_url": self.weather_connector.base_url,
                "supabase_url_configured": bool(self.supabase.base_url),
                "database_status": supabase_status,
                "open_meteo_status": open_meteo_status,
            },
        )
