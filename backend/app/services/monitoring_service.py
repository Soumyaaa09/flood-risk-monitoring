"""Ingestion and Data-Quality Monitoring Service for FloodWatch.

Coordinates operational telemetry, data freshness calculations, and
quality validation across all pilot monitoring locations (Paradeep, Cuttack, Kendrapara).
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.risk_engine.config import get_risk_config
from app.schemas.ingestion import FreshnessState, IngestionRunResponse
from app.schemas.monitoring import (
    DataQualityIssue,
    FreshnessSummary,
    IngestionHealthSummary,
    LocationQualityHealth,
    MonitoringIngestionHealthResponse,
    MonitoringLocationsResponse,
    MonitoringSummaryResponse,
    QualitySeverity,
    QualityStatus,
)
from app.services.data_quality import (
    DataQualityValidator,
    FreshnessEvaluator,
    normalize_to_utc,
    parse_utc_timestamp,
)

logger = logging.getLogger(__name__)

# Bounded per-request concurrency when querying pilot locations from Supabase
MAX_CONCURRENT_LOCATION_QUERIES = 5


class MonitoringService:
    """Read-only monitoring service inspecting observation quality, freshness, and ingestion runs."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self.supabase = supabase_client or SupabaseClient()
        self.settings = get_settings()

    async def get_location_quality_health(self, location: Dict[str, Any]) -> LocationQualityHealth:
        """Evaluate data quality and freshness for an individual pilot monitoring location."""
        loc_id = str(location["id"])
        loc_name = str(location["name"])
        district_name = (
            location.get("districts", {}).get("name")
            if isinstance(location.get("districts"), dict)
            else location.get("district_name")
        )
        lat = float(location["latitude"])
        lon = float(location["longitude"])

        # 1. Location metadata validation
        loc_issues = DataQualityValidator.validate_location_metadata(location)

        # 2. Query latest observations (service-role query)
        obs_data = await self.supabase.get_latest_observations_for_location(location_id=loc_id)
        raw_analysis = obs_data.get("analysis")
        raw_forecasts = obs_data.get("forecasts", [])

        # 3. Evaluate Current Analysis Record (Dual Freshness: Observation vs Ingestion)
        obs_freshness_state = FreshnessState.UNAVAILABLE
        obs_freshness_ts: Optional[datetime] = None
        obs_age_minutes: Optional[float] = None

        ing_freshness_state = FreshnessState.UNAVAILABLE
        ing_freshness_ts: Optional[datetime] = None
        ing_age_minutes: Optional[float] = None

        current_rainfall: Optional[float] = None

        if raw_analysis:
            # Physical value validation
            raw_val = raw_analysis.get("value")
            val_issues = DataQualityValidator.validate_observation_value(
                raw_val, measurement_type="rainfall", unit=raw_analysis.get("unit", "mm"), field_name="analysis.value"
            )
            loc_issues.extend(val_issues)
            if raw_val is not None:
                current_rainfall = round(float(raw_val), 4)

            # Observation timestamp: valid_at (physical phenomenon time)
            raw_valid = raw_analysis.get("valid_at")
            v_dt, v_issues = DataQualityValidator.validate_timestamp(raw_valid, field_name="analysis.valid_at")
            loc_issues.extend(v_issues)
            if v_dt:
                obs_freshness_ts = v_dt
                obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate_observation(
                    obs_freshness_ts,
                    config=get_risk_config(),
                )
            else:
                obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate_observation(None)

            # Ingestion timestamp: fetched_at (pipeline retrieval time)
            raw_fetched = raw_analysis.get("fetched_at")
            f_dt, f_issues = DataQualityValidator.validate_timestamp(raw_fetched, field_name="analysis.fetched_at")
            loc_issues.extend(f_issues)
            if f_dt:
                ing_freshness_ts = f_dt
                ing_freshness_state, ing_age_minutes = FreshnessEvaluator.evaluate_ingestion(
                    ing_freshness_ts,
                    settings=self.settings,
                )
            else:
                ing_freshness_state, ing_age_minutes = FreshnessEvaluator.evaluate_ingestion(
                    None,
                    settings=self.settings,
                )

            # Detect discrepancy: recently fetched but stale/delayed physical observation
            if ing_freshness_state == FreshnessState.FRESH and obs_freshness_state in [
                FreshnessState.DELAYED,
                FreshnessState.STALE,
                FreshnessState.UNAVAILABLE,
            ]:
                loc_issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.WARNING,
                        code="STALE_OBSERVATION_WITH_RECENT_INGESTION",
                        message=(
                            f"Observation timestamp ({obs_freshness_ts.isoformat() if obs_freshness_ts else 'missing'}) "
                            f"is {obs_freshness_state.value.upper()}, despite recent ingestion retrieval at "
                            f"{ing_freshness_ts.isoformat() if ing_freshness_ts else 'unknown'}."
                        ),
                        field="analysis.valid_at",
                        value=raw_valid,
                        timestamp=obs_freshness_ts,
                    )
                )
        else:
            obs_freshness_state, obs_age_minutes = FreshnessEvaluator.evaluate(None)
            ing_freshness_state, ing_age_minutes = FreshnessEvaluator.evaluate(None)

        # 4. Evaluate Active Forecast Horizon
        forecast_status, fc_issues, fc_meta = DataQualityValidator.validate_forecast_horizon(
            raw_forecasts, min_hours=24
        )
        loc_issues.extend(fc_issues)

        # 5. Calculate Next 24h Accumulated Forecast Precipitation (MED-05: fault-tolerant parsing)
        next_24h_precip = 0.0
        now_utc = datetime.now(timezone.utc)
        valid_fc_points = 0
        invalid_fc_points = 0
        for fc in raw_forecasts:
            if not isinstance(fc, dict):
                invalid_fc_points += 1
                continue
            v_raw = fc.get("valid_at")
            v_dt = parse_utc_timestamp(v_raw)
            if v_dt is None:
                invalid_fc_points += 1
                continue
            val = fc.get("value")
            if val is None:
                continue
            try:
                num_val = float(val)
            except (ValueError, TypeError):
                invalid_fc_points += 1
                continue
            if 0 <= (v_dt - now_utc).total_seconds() <= 86400:
                next_24h_precip += num_val
                valid_fc_points += 1

        if invalid_fc_points > 0:
            logger.warning(
                "Skipped %d forecast record(s) with invalid timestamps or values for location '%s'",
                invalid_fc_points,
                sanitize_error_message(loc_id) or "unknown",
            )

        next_24h_total = round(next_24h_precip, 2) if valid_fc_points > 0 else None

        # 6. Overall Location Quality Status
        # Physical observation freshness is authoritative for data usability
        has_errors = any(i.severity in [QualitySeverity.ERROR, QualitySeverity.CRITICAL] for i in loc_issues)
        has_warnings = any(i.severity == QualitySeverity.WARNING for i in loc_issues)

        if has_errors or obs_freshness_state == FreshnessState.UNAVAILABLE:
            quality_status = QualityStatus.FAIL
        elif has_warnings or obs_freshness_state in [FreshnessState.DELAYED, FreshnessState.STALE]:
            quality_status = QualityStatus.WARNING
        else:
            quality_status = QualityStatus.PASS

        return LocationQualityHealth(
            location_id=loc_id,
            location_name=loc_name,
            district_name=district_name,
            latitude=lat,
            longitude=lon,
            # Dual Freshness
            observation_freshness=obs_freshness_state,
            observation_timestamp=obs_freshness_ts,
            observation_age_minutes=obs_age_minutes,
            ingestion_freshness=ing_freshness_state,
            ingestion_timestamp=ing_freshness_ts,
            ingestion_age_minutes=ing_age_minutes,
            # Backward compatibility (mirrors physical observation freshness)
            freshness=obs_freshness_state,
            data_freshness_timestamp=obs_freshness_ts or ing_freshness_ts,
            age_minutes=obs_age_minutes,
            current_rainfall_mm=current_rainfall,
            active_forecast_hours=fc_meta.get("horizon_hours", len(raw_forecasts)),
            next_24h_precipitation_mm=next_24h_total,
            quality_status=quality_status,
            quality_issues=loc_issues,
            completeness_percentage=fc_meta.get("completeness_pct", 100.0),
        )

    async def _get_locations_quality_health_bounded(
        self, pilot_locations: List[Dict[str, Any]]
    ) -> List[LocationQualityHealth]:
        """Evaluate location quality health across pilot locations with bounded concurrency."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOCATION_QUERIES)

        async def _evaluate(location: Dict[str, Any]) -> LocationQualityHealth:
            async with semaphore:
                return await self.get_location_quality_health(location)

        return list(await asyncio.gather(*(_evaluate(loc) for loc in pilot_locations)))

    async def get_monitoring_summary(self) -> MonitoringSummaryResponse:
        """Produce high-level operational health and freshness telemetry across all pilot locations."""
        pilot_locations, recent_runs_raw, stale_runs_raw = await asyncio.gather(
            self.supabase.get_pilot_locations(),
            self.supabase.get_ingestion_runs(limit=20),
            self.supabase.get_stale_started_runs(cutoff_minutes=60),
        )
        location_healths = await self._get_locations_quality_health_bounded(pilot_locations)

        obs_fresh_count = 0
        obs_delayed_count = 0
        obs_stale_count = 0
        obs_unavailable_count = 0

        ing_fresh_count = 0
        ing_delayed_count = 0
        ing_stale_count = 0
        ing_unavailable_count = 0

        for lh in location_healths:
            # Observation freshness
            if lh.observation_freshness == FreshnessState.FRESH:
                obs_fresh_count += 1
            elif lh.observation_freshness == FreshnessState.DELAYED:
                obs_delayed_count += 1
            elif lh.observation_freshness == FreshnessState.STALE:
                obs_stale_count += 1
            else:
                obs_unavailable_count += 1

            # Ingestion freshness
            if lh.ingestion_freshness == FreshnessState.FRESH:
                ing_fresh_count += 1
            elif lh.ingestion_freshness == FreshnessState.DELAYED:
                ing_delayed_count += 1
            elif lh.ingestion_freshness == FreshnessState.STALE:
                ing_stale_count += 1
            else:
                ing_unavailable_count += 1

        # Derive overall observation freshness state across locations
        if obs_unavailable_count > 0:
            overall_obs_freshness = FreshnessState.UNAVAILABLE
        elif obs_stale_count > 0:
            overall_obs_freshness = FreshnessState.STALE
        elif obs_delayed_count > 0:
            overall_obs_freshness = FreshnessState.DELAYED
        elif obs_fresh_count > 0:
            overall_obs_freshness = FreshnessState.FRESH
        else:
            overall_obs_freshness = FreshnessState.UNAVAILABLE

        # Derive overall ingestion freshness state across locations
        if ing_unavailable_count > 0:
            overall_ing_freshness = FreshnessState.UNAVAILABLE
        elif ing_stale_count > 0:
            overall_ing_freshness = FreshnessState.STALE
        elif ing_delayed_count > 0:
            overall_ing_freshness = FreshnessState.DELAYED
        elif ing_fresh_count > 0:
            overall_ing_freshness = FreshnessState.FRESH
        else:
            overall_ing_freshness = FreshnessState.UNAVAILABLE

        freshness_summary = FreshnessSummary(
            fresh_count=obs_fresh_count,
            delayed_count=obs_delayed_count,
            stale_count=obs_stale_count,
            unavailable_count=obs_unavailable_count,
            overall_state=overall_obs_freshness,
            ingestion_fresh_count=ing_fresh_count,
            ingestion_delayed_count=ing_delayed_count,
            ingestion_stale_count=ing_stale_count,
            ingestion_unavailable_count=ing_unavailable_count,
            ingestion_overall_state=overall_ing_freshness,
        )

        # Ingestion pipeline health metrics (computed from concurrent Stage-1 fetch)

        latest_attempt_at: Optional[datetime] = None
        latest_attempt_status: Optional[str] = None
        latest_success_at: Optional[datetime] = None
        current_run_status = "idle"
        succ_runs = 0
        fail_runs = 0
        durations: List[float] = []
        total_records_ingested = 0

        if recent_runs_raw:
            first_run = recent_runs_raw[0]
            latest_attempt_at = parse_utc_timestamp(first_run.get("started_at"))
            latest_attempt_status = first_run.get("status")
            current_run_status = latest_attempt_status or "unknown"

            for r in recent_runs_raw:
                st = r.get("status")
                if st == "completed":
                    succ_runs += 1
                    if latest_success_at is None and r.get("completed_at"):
                        latest_success_at = parse_utc_timestamp(r.get("completed_at"))
                    elif latest_success_at is None and r.get("started_at"):
                        latest_success_at = parse_utc_timestamp(r.get("started_at"))
                elif st == "failed":
                    fail_runs += 1

                total_records_ingested += r.get("records_inserted", 0) + r.get("records_updated", 0)

                # Duration computation for completed runs
                s_dt = parse_utc_timestamp(r.get("started_at"))
                c_dt = parse_utc_timestamp(r.get("completed_at"))
                if s_dt is not None and c_dt is not None:
                    durations.append(max(0.0, (c_dt - s_dt).total_seconds()))

        avg_dur = round(sum(durations) / len(durations), 2) if durations else None

        ingestion_summary = IngestionHealthSummary(
            latest_attempt_at=latest_attempt_at,
            latest_attempt_status=latest_attempt_status,
            latest_success_at=latest_success_at,
            current_run_status=current_run_status,
            recent_runs_count=len(recent_runs_raw),
            successful_runs_count=succ_runs,
            failed_runs_count=fail_runs,
            stale_started_runs_count=len(stale_runs_raw),
            average_duration_seconds=avg_dur,
            total_records_ingested_recent=total_records_ingested,
        )

        # Derive system overall health label
        if obs_unavailable_count > 0 or len(stale_runs_raw) > 0 or (fail_runs > succ_runs and fail_runs > 0):
            system_health = "degraded" if succ_runs > 0 else "critical"
        elif obs_delayed_count > 0 or obs_stale_count > 0:
            system_health = "degraded"
        else:
            system_health = "healthy"

        scheduler_status = None
        try:
            from app.services.ingestion_scheduler import get_ingestion_scheduler
            scheduler_status = get_ingestion_scheduler().get_status()
        except Exception as exc:
            logger.debug("Scheduler status unavailable for summary: %s", exc)

        return MonitoringSummaryResponse(
            system_health=system_health,
            freshness_summary=freshness_summary,
            ingestion_summary=ingestion_summary,
            location_healths=location_healths,
            scheduler=scheduler_status,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_monitoring_locations(self) -> MonitoringLocationsResponse:
        """Detailed location-wise freshness and observation quality audit."""
        pilot_locations = await self.supabase.get_pilot_locations()
        location_healths = await self._get_locations_quality_health_bounded(pilot_locations)
        return MonitoringLocationsResponse(
            total_locations=len(location_healths),
            locations=location_healths,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ingestion_health(self) -> MonitoringIngestionHealthResponse:
        """Detailed ingestion telemetry, data source connectivity, and recent runs."""
        service_role_set = bool(self.settings.SUPABASE_SERVICE_ROLE_KEY.strip())
        supabase_status = "connected" if self.supabase.base_url else "not_configured"

        recent_runs_raw = await self.supabase.get_ingestion_runs(limit=20)
        stale_runs_raw = await self.supabase.get_stale_started_runs(cutoff_minutes=60)

        sanitized_runs = [
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
            for r in recent_runs_raw
        ]

        overall_status = "healthy" if len(stale_runs_raw) == 0 else "degraded"

        return MonitoringIngestionHealthResponse(
            status=overall_status,
            provider="Open-Meteo Weather API & Supabase PostgREST",
            connectivity=f"Database: {supabase_status}; ServiceRoleConfigured: {service_role_set}",
            service_role_configured=service_role_set,
            recent_runs=sanitized_runs,
            stale_runs=stale_runs_raw,
            timestamp=datetime.now(timezone.utc),
            details={
                "stale_runs_count": len(stale_runs_raw),
                "recent_runs_count": len(recent_runs_raw),
            },
        )
