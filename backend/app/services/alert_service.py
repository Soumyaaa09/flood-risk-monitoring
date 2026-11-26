"""Alert Evaluation and In-App Notification Service for FloodWatch (Phase 2.7).

Coordinates:
- Detection of meaningful rainfall risk escalations and reductions across pilot locations.
- Data freshness / staleness notifications.
- Stable fingerprinting and duplicate alert prevention.
- Alert lifecycle state transitions: NEW -> ACTIVE -> ACKNOWLEDGED -> RESOLVED.
- Concurrency protection using an in-process asyncio.Lock.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import Settings, get_settings
from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClient, SupabaseClientError
from app.risk_engine.config import DEFAULT_RISK_CONFIG
from app.schemas.alert import (
    DEFAULT_ALERT_DISCLAIMER,
    AlertEvidence,
    AlertResponse,
    AlertSeverity,
    AlertStatus,
    AlertSummaryResponse,
    AlertType,
    AlertsListResponse,
)
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualityStatus
from app.schemas.risk import RainfallRiskAssessmentResponse, RiskCategory

logger = logging.getLogger(__name__)


class AlertService:
    """Evaluates risk assessment outcomes and orchestrates in-app alert lifecycle."""

    ALERT_SCAN_PAGE_SIZE = 100
    MAX_ALERT_SCAN_PAGES = 100

    def __init__(
        self,
        supabase_client: Optional[SupabaseClient] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.supabase = supabase_client or SupabaseClient(
            supabase_url=self.settings.SUPABASE_URL,
            service_role_key=self.settings.SUPABASE_SERVICE_ROLE_KEY,
            anon_key=self.settings.SUPABASE_ANON_KEY,
        )
        self._lock = asyncio.Lock()
        # In-memory store for fallback / testing when live DB is unconfigured in non-production
        self._memory_alerts: Dict[str, Dict[str, Any]] = {}

    @property
    def _allow_memory_fallback(self) -> bool:
        """Allow ephemeral in-memory alert storage ONLY in non-production environments."""
        return not self.settings.is_production

    @staticmethod
    def generate_fingerprint(location_id: str, alert_type: AlertType) -> str:
        """Generate stable deduplication fingerprint for an open alert."""
        return f"{location_id}:{alert_type.value}"

    async def _fetch_all_matching_alerts(
        self,
        location_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve complete alert set via bounded deduplicating pagination (no >100 truncation)."""
        if self._allow_memory_fallback and not self.supabase.base_url:
            return list(self._memory_alerts.values())

        collected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        page_offset = 0

        for _page in range(self.MAX_ALERT_SCAN_PAGES):
            try:
                batch = await self.supabase.get_alerts(
                    location_id=location_id,
                    status=status,
                    severity=severity,
                    alert_type=alert_type,
                    limit=self.ALERT_SCAN_PAGE_SIZE,
                    offset=page_offset,
                )
            except SupabaseClientError:
                raise
            except Exception as exc:
                logger.warning("DB query failed in _fetch_all_matching_alerts: %s", exc.__class__.__name__)
                raise SupabaseClientError(
                    f"Failed to retrieve alert records: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc

            if not batch:
                break

            new_in_page = 0
            for rec in batch:
                if not isinstance(rec, dict):
                    continue
                rec_id = rec.get("id")
                dedup_key = (
                    f"id:{rec_id}"
                    if rec_id is not None
                    else f"anon:{len(collected)}:{rec.get('created_at')}:{rec.get('fingerprint')}"
                )
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)
                collected.append(rec)
                new_in_page += 1

            # Stop when final partial page reached or when static test mock repeats identical items
            if len(batch) < self.ALERT_SCAN_PAGE_SIZE or new_in_page == 0:
                break

            page_offset += self.ALERT_SCAN_PAGE_SIZE
        else:
            logger.warning(
                "Alert pagination reached bounded safety cap (%d pages, %d alerts)",
                self.MAX_ALERT_SCAN_PAGES,
                len(collected),
            )

        return collected

    async def get_alerts(
        self,
        location_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AlertsListResponse:
        """Retrieve alerts matching filter criteria with pagination and complete lifecycle counts."""
        records = await self._fetch_all_matching_alerts(
            location_id=location_id,
            status=status,
            severity=severity,
            alert_type=alert_type,
        )

        filtered: List[Dict[str, Any]] = []
        for r in records:
            if location_id and str(r.get("location_id")) != location_id:
                continue
            if status and str(r.get("status")).upper() != status.upper():
                continue
            if severity and str(r.get("severity")).upper() != severity.upper():
                continue
            if alert_type and str(r.get("alert_type")).upper() != alert_type.upper():
                continue
            filtered.append(r)

        # Sort newest first
        filtered.sort(
            key=lambda x: str(x.get("created_at") or ""),
            reverse=True,
        )

        total = len(filtered)
        paginated = filtered[offset : offset + limit]

        # Complete lifecycle counts across the full matching result set (not limited to page slice)
        active_count = sum(1 for r in filtered if r.get("status") in ("NEW", "ACTIVE"))
        ack_count = sum(1 for r in filtered if r.get("status") == "ACKNOWLEDGED")
        res_count = sum(1 for r in filtered if r.get("status") == "RESOLVED")

        loc_map = (
            await self._get_location_metadata_map()
            if any(not r.get("location_name") for r in paginated)
            else {}
        )
        alert_responses = [
            self._to_alert_response(r, location_meta=loc_map.get(str(r.get("location_id"))))
            for r in paginated
        ]

        return AlertsListResponse(
            total=total,
            alerts=alert_responses,
            active_count=active_count,
            acknowledged_count=ack_count,
            resolved_count=res_count,
        )

    async def get_alert_by_id(self, alert_id: str) -> Optional[AlertResponse]:
        """Retrieve a specific alert by ID."""
        try:
            record = await self.supabase.get_alert_by_id(alert_id)
        except SupabaseClientError:
            raise
        except Exception as exc:
            logger.warning("DB query failed in get_alert_by_id: %s", exc.__class__.__name__)
            if not self._allow_memory_fallback or bool(self.supabase.base_url):
                raise SupabaseClientError(
                    f"Failed to retrieve alert record: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc
            record = None

        if record:
            loc_meta = await self._resolve_single_location_meta(record)
            return self._to_alert_response(record, location_meta=loc_meta)

        if not self._allow_memory_fallback or bool(self.supabase.base_url):
            return None

        mem_record = self._memory_alerts.get(alert_id)
        if not mem_record:
            return None
        loc_meta = await self._resolve_single_location_meta(mem_record)
        return self._to_alert_response(mem_record, location_meta=loc_meta)

    async def acknowledge_alert(
        self, alert_id: str, acknowledged_by: str = "operator"
    ) -> Optional[AlertResponse]:
        """Acknowledge an active alert without changing underlying risk calculations."""
        now_utc = datetime.now(timezone.utc)
        clean_user = sanitize_error_message(acknowledged_by) or "operator"

        updates = {
            "status": AlertStatus.ACKNOWLEDGED.value,
            "acknowledged_at": now_utc.isoformat(),
            "acknowledged_by": clean_user,
            "updated_at": now_utc.isoformat(),
        }

        updated_record = None
        try:
            updated_record = await self.supabase.update_alert(alert_id, updates)
        except Exception as exc:
            logger.warning("DB update failed in acknowledge_alert: %s", exc.__class__.__name__)
            if not self._allow_memory_fallback or bool(self.supabase.base_url):
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to persist alert acknowledgement: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc

        if self._allow_memory_fallback and not self.supabase.base_url:
            if not updated_record and alert_id in self._memory_alerts:
                self._memory_alerts[alert_id].update(updates)
                updated_record = self._memory_alerts[alert_id]
            elif updated_record and alert_id in self._memory_alerts:
                self._memory_alerts[alert_id].update(updates)

        if not updated_record:
            return None
        loc_meta = await self._resolve_single_location_meta(updated_record)
        return self._to_alert_response(updated_record, location_meta=loc_meta)

    async def resolve_alert(
        self,
        alert_id: str,
        resolved_reason: str,
        resolved_by: Optional[str] = None,
    ) -> Optional[AlertResponse]:
        """Manually resolve an open alert with operator justification and timestamp.

        Requires an alert in an open status (NEW, ACTIVE, ACKNOWLEDGED).
        Updates status, resolved_at, resolved_reason, updated_at.
        """
        now_utc = datetime.now(timezone.utc)
        clean_reason = sanitize_error_message(resolved_reason.strip()) or "Resolved by operator"
        clean_user = (
            sanitize_error_message(resolved_by.strip())
            if resolved_by and resolved_by.strip()
            else "operator"
        )

        updates = {
            "status": AlertStatus.RESOLVED.value,
            "resolved_at": now_utc.isoformat(),
            "resolved_reason": clean_reason,
            "updated_at": now_utc.isoformat(),
        }

        updated_record = None
        try:
            updated_record = await self.supabase.update_alert(alert_id, updates)
        except Exception as exc:
            logger.warning("DB update failed in resolve_alert: %s", exc.__class__.__name__)
            if not self._allow_memory_fallback or bool(self.supabase.base_url):
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to persist alert resolution: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc

        if self._allow_memory_fallback and not self.supabase.base_url:
            if not updated_record and alert_id in self._memory_alerts:
                self._memory_alerts[alert_id].update(updates)
                updated_record = self._memory_alerts[alert_id]
            elif updated_record and alert_id in self._memory_alerts:
                self._memory_alerts[alert_id].update(updates)

        if not updated_record:
            return None

        logger.info(
            "Alert '%s' manually resolved with reason '%s' (operator label: %s)",
            alert_id,
            clean_reason,
            clean_user,
        )
        loc_meta = await self._resolve_single_location_meta(updated_record)
        return self._to_alert_response(updated_record, location_meta=loc_meta)

    async def get_summary(self) -> AlertSummaryResponse:
        """Compute top-level active alert metrics across the complete alert dataset."""
        raw_alerts = await self._fetch_all_matching_alerts()
        loc_map = (
            await self._get_location_metadata_map()
            if any(not r.get("location_name") for r in raw_alerts)
            else {}
        )
        alerts = [
            self._to_alert_response(r, location_meta=loc_map.get(str(r.get("location_id"))))
            for r in raw_alerts
        ]

        open_alerts = [a for a in alerts if a.status in (AlertStatus.NEW, AlertStatus.ACTIVE)]
        ack_alerts = [a for a in alerts if a.status == AlertStatus.ACKNOWLEDGED]

        severity_counts: Dict[str, int] = {
            "INFO": 0,
            "DATA_WARNING": 0,
            "WARNING": 0,
            "HIGH": 0,
            "CRITICAL": 0,
        }
        location_counts: Dict[str, int] = {}

        for a in open_alerts:
            s_val = a.severity.value
            severity_counts[s_val] = severity_counts.get(s_val, 0) + 1
            loc_key = a.location_name or a.location_id
            location_counts[loc_key] = location_counts.get(loc_key, 0) + 1

        # Highest active severity
        highest_sev = None
        if severity_counts.get("CRITICAL", 0) > 0:
            highest_sev = AlertSeverity.CRITICAL
        elif severity_counts.get("HIGH", 0) > 0:
            highest_sev = AlertSeverity.HIGH
        elif severity_counts.get("WARNING", 0) > 0:
            highest_sev = AlertSeverity.WARNING
        elif severity_counts.get("DATA_WARNING", 0) > 0:
            highest_sev = AlertSeverity.DATA_WARNING
        elif severity_counts.get("INFO", 0) > 0:
            highest_sev = AlertSeverity.INFO

        recent = sorted(alerts, key=lambda a: a.created_at, reverse=True)[:5]

        return AlertSummaryResponse(
            total_active_alerts=len(open_alerts),
            total_acknowledged_alerts=len(ack_alerts),
            highest_active_severity=highest_sev,
            severity_counts=severity_counts,
            location_active_counts=location_counts,
            recent_alerts=recent,
        )

    async def evaluate_location_assessment(
        self, assessment: RainfallRiskAssessmentResponse
    ) -> Optional[AlertResponse]:
        """Evaluate a single location risk assessment and trigger appropriate alert lifecycle events."""
        async with self._lock:
            return await self._process_assessment_internal(assessment)

    async def evaluate_all_locations(
        self, assessments: List[RainfallRiskAssessmentResponse]
    ) -> List[AlertResponse]:
        """Batch evaluate risk assessments across pilot monitoring points."""
        async with self._lock:
            outcomes: List[AlertResponse] = []
            for a in assessments:
                res = await self._process_assessment_internal(a)
                if res:
                    outcomes.append(res)
            return outcomes

    async def evaluate_post_ingestion(self, ingestion_results: List[Any]) -> List[AlertResponse]:
        """Evaluate alerts for completed locations from ingestion outcomes with strict fault isolation.

        Evaluates only successfully ingested locations with valid assessment data.
        Follows the scheduler's independent try/except fault-isolation pattern.
        Alert evaluation failures never impact weather ingestion.
        """
        from app.schemas.ingestion import IngestionStatus
        from app.services.risk_service import get_risk_service

        risk_svc = get_risk_service()
        outcomes: List[AlertResponse] = []

        for loc in ingestion_results:
            loc_status = getattr(loc, "status", None) or (
                loc.get("status") if isinstance(loc, dict) else None
            )
            # Only evaluate completed ingestion locations
            if loc_status == IngestionStatus.COMPLETED or loc_status == IngestionStatus.COMPLETED.value:
                loc_id = getattr(loc, "location_id", None) or (
                    loc.get("location_id") if isinstance(loc, dict) else None
                )
                loc_name = getattr(loc, "location_name", None) or (
                    loc.get("location_name") if isinstance(loc, dict) else str(loc_id)
                )
                if not loc_id:
                    continue

                try:
                    loc_risk = await risk_svc.get_location_rainfall_risk(str(loc_id))
                    if loc_risk:
                        alert_outcome = await self.evaluate_location_assessment(loc_risk)
                        if alert_outcome:
                            outcomes.append(alert_outcome)
                except Exception as loc_eval_exc:
                    logger.warning(
                        "Alert evaluation failed for location '%s': %s",
                        loc_name,
                        loc_eval_exc.__class__.__name__,
                    )

        return outcomes

    async def _process_assessment_internal(
        self, assessment: RainfallRiskAssessmentResponse
    ) -> Optional[AlertResponse]:
        """Internal alert evaluator guarded by concurrency lock."""
        loc_id = str(assessment.location_id)
        loc_name = str(assessment.location_name or "Unknown Pilot Station")
        district = str(assessment.district_name or "Mahanadi Delta")
        now_utc = datetime.now(timezone.utc)

        cat = assessment.risk_category
        recent_mm = assessment.recent_observed_rainfall_mm
        f6_mm = assessment.forecast_6h_precipitation_mm
        f24_mm = assessment.forecast_24h_precipitation_mm
        recent_mm_text = self._format_rainfall_mm(recent_mm)
        f24_mm_text = self._format_rainfall_mm(f24_mm)
        freshness = assessment.observation_freshness

        # Fetch currently open alerts for this location
        open_alerts = await self._get_open_alerts_for_location(loc_id)

        # ---------------------------------------------------------------------
        # Case A: High, Very High, or Moderate Rainfall Risk
        # ---------------------------------------------------------------------
        if cat in (RiskCategory.VERY_HIGH, RiskCategory.HIGH, RiskCategory.MODERATE):
            if cat == RiskCategory.VERY_HIGH:
                a_type = AlertType.VERY_HIGH_RAINFALL_RISK
                sev = AlertSeverity.CRITICAL
                title = f"Very High Rainfall Runoff Potential: {loc_name}"
                desc = (
                    f"Numerical forecasts and recent telemetry at {loc_name} ({district}) indicate "
                    f"very high surface water accumulation potential ({recent_mm_text} mm observed nowcast, "
                    f"{f24_mm_text} mm 24h forecast). Research prototype: NOT an official flood warning."
                )
            elif cat == RiskCategory.HIGH:
                a_type = AlertType.HIGH_RAINFALL_RISK
                sev = AlertSeverity.HIGH
                title = f"High Rainfall Runoff Potential: {loc_name}"
                desc = (
                    f"Substantial rainfall detected at {loc_name} ({district}) with high surface runoff "
                    f"potential ({recent_mm_text} mm observed, {f24_mm_text} mm 24h forecast)."
                )
            else:
                a_type = AlertType.MODERATE_RAINFALL_RISK
                sev = AlertSeverity.WARNING
                title = f"Moderate Rainfall Surface Water Accumulation: {loc_name}"
                desc = (
                    f"Moderate rainfall conditions detected at {loc_name} ({district}) "
                    f"({recent_mm_text} mm observed, {f24_mm_text} mm 24h forecast)."
                )

            fp = self.generate_fingerprint(loc_id, a_type)

            # Check if matching open alert already exists (Deduplication)
            existing_same = next((a for a in open_alerts if a.get("fingerprint") == fp), None)
            if existing_same:
                # Update existing alert timestamp, description, and evidence without duplicating
                updates = await self._touch_alert(
                    existing_same["id"],
                    assessment,
                    description=desc,
                )
                return self._to_alert_response(
                    {**existing_same, **updates},
                    location_meta={"location_name": loc_name, "district_name": district},
                )

            # Check for Escalation: resolve any lower-tier active risk alerts
            for old in open_alerts:
                old_type = old.get("alert_type")
                if old_type in (
                    AlertType.MODERATE_RAINFALL_RISK.value,
                    AlertType.HIGH_RAINFALL_RISK.value,
                ) and old_type != a_type.value:
                    await self._resolve_alert_record(
                        old["id"], reason=f"Risk escalated to {a_type.value}"
                    )

            # Create new risk alert
            return await self._create_alert_record(
                location_id=loc_id,
                location_name=loc_name,
                district_name=district,
                alert_type=a_type,
                severity=sev,
                title=title,
                description=desc,
                fingerprint=fp,
                assessment=assessment,
            )

        # ---------------------------------------------------------------------
        # Case B: Data Unavailable or Stale (Data-Quality Notifications)
        # ---------------------------------------------------------------------
        elif cat == RiskCategory.UNAVAILABLE:
            if freshness == FreshnessState.STALE:
                a_type = AlertType.DATA_STALE
                sev = AlertSeverity.DATA_WARNING
                title = f"Telemetry Data Stale: {loc_name}"
                desc = (
                    f"Weather observations for {loc_name} are stale ({assessment.observation_age_minutes or '>360'}m old). "
                    "Atmospheric telemetry exceeds acceptable freshness threshold."
                )
            else:
                a_type = AlertType.DATA_UNAVAILABLE
                sev = AlertSeverity.DATA_WARNING
                title = f"Telemetry Data Unavailable: {loc_name}"
                desc = (
                    f"Rainfall observations or forecast horizons for {loc_name} are incomplete or unavailable. "
                    "FloodWatch refuses flood risk evaluation on missing telemetry."
                )

            fp = self.generate_fingerprint(loc_id, a_type)
            existing_same = next((a for a in open_alerts if a.get("fingerprint") == fp), None)
            if existing_same:
                updates = await self._touch_alert(
                    existing_same["id"],
                    assessment,
                    description=desc,
                )
                return self._to_alert_response(
                    {**existing_same, **updates},
                    location_meta={"location_name": loc_name, "district_name": district},
                )

            # CRITICAL SAFETY RULE: Missing or stale data MUST NOT automatically resolve
            # an existing high-risk alert! Leave existing high-risk alerts intact.
            return await self._create_alert_record(
                location_id=loc_id,
                location_name=loc_name,
                district_name=district,
                alert_type=a_type,
                severity=sev,
                title=title,
                description=desc,
                fingerprint=fp,
                assessment=assessment,
            )

        # ---------------------------------------------------------------------
        # Case C: Low Rainfall Risk (Normal / De-escalated Conditions)
        # ---------------------------------------------------------------------
        elif cat == RiskCategory.LOW:
            # Recovery requires verified fresh, valid, and complete telemetry;
            # missing, delayed, stale, or failed telemetry never triggers recovery resolution.
            has_valid_recovery_telemetry = (
                freshness == FreshnessState.FRESH
                and assessment.quality_status != QualityStatus.FAIL
                and recent_mm is not None
                and assessment.forecast_completeness_pct >= DEFAULT_RISK_CONFIG.min_forecast_completeness_pct
                and assessment.active_forecast_hours >= DEFAULT_RISK_CONFIG.min_forecast_hours
            )
            if not has_valid_recovery_telemetry:
                logger.warning(
                    "Skipping LOW-risk recovery alert resolution for '%s': telemetry not fresh/valid/complete "
                    "(freshness=%s, quality=%s, completeness=%.1f%%, horizon=%dh).",
                    loc_name,
                    freshness.value,
                    assessment.quality_status.value,
                    assessment.forecast_completeness_pct,
                    assessment.active_forecast_hours,
                )
                return None

            # 1. Resolve obsolete data-quality alerts BEFORE emitting/returning RISK_REDUCED
            data_alerts = [
                a for a in open_alerts
                if a.get("alert_type") in (AlertType.DATA_UNAVAILABLE.value, AlertType.DATA_STALE.value)
            ]
            for da in data_alerts:
                await self._resolve_alert_record(da["id"], reason="Fresh weather data successfully restored.")

            # 2. Check if there were open rainfall risk alerts to resolve (Risk Reduction)
            prior_risk_alerts = [
                a for a in open_alerts
                if a.get("alert_type") in (
                    AlertType.VERY_HIGH_RAINFALL_RISK.value,
                    AlertType.HIGH_RAINFALL_RISK.value,
                    AlertType.MODERATE_RAINFALL_RISK.value,
                )
            ]

            if prior_risk_alerts:
                for old in prior_risk_alerts:
                    await self._resolve_alert_record(
                        old["id"], reason="Rainfall runoff potential de-escalated to LOW."
                    )

                # Emit or update RISK_REDUCED notification without violating uq_risk_alerts_active_fingerprint
                a_type = AlertType.RISK_REDUCED
                fp = self.generate_fingerprint(loc_id, a_type)
                title = f"Rainfall Risk Reduced: {loc_name}"
                desc = (
                    f"Rainfall and forecast conditions at {loc_name} ({district}) have subsided "
                    f"to LOW runoff potential ({recent_mm_text} mm observed, {f24_mm_text} mm 24h forecast)."
                )

                existing_reduced = next(
                    (a for a in open_alerts if a.get("fingerprint") == fp or a.get("alert_type") == a_type.value),
                    None,
                )
                if existing_reduced:
                    updates = await self._touch_alert(
                        existing_reduced["id"],
                        assessment,
                        description=desc,
                    )
                    return self._to_alert_response(
                        {**existing_reduced, **updates},
                        location_meta={"location_name": loc_name, "district_name": district},
                    )

                try:
                    return await self._create_alert_record(
                        location_id=loc_id,
                        location_name=loc_name,
                        district_name=district,
                        alert_type=a_type,
                        severity=AlertSeverity.INFO,
                        title=title,
                        description=desc,
                        fingerprint=fp,
                        assessment=assessment,
                    )
                except SupabaseClientError as exc:
                    # Handle race on uq_risk_alerts_active_fingerprint if another worker created RISK_REDUCED concurrently
                    if exc.status_code != 409:
                        raise
                    refreshed_open = await self._get_open_alerts_for_location(loc_id)
                    concurrent_reduced = next(
                        (a for a in refreshed_open if a.get("fingerprint") == fp or a.get("alert_type") == a_type.value),
                        None,
                    )
                    if not concurrent_reduced:
                        raise
                    updates = await self._touch_alert(
                        concurrent_reduced["id"],
                        assessment,
                        description=desc,
                    )
                    return self._to_alert_response(
                        {**concurrent_reduced, **updates},
                        location_meta={"location_name": loc_name, "district_name": district},
                    )

            return None

        return None

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    async def _get_open_alerts_for_location(self, location_id: str) -> List[Dict[str, Any]]:
        try:
            db_open = await self.supabase.get_open_alerts_for_location(location_id)
            return db_open
        except Exception as exc:
            logger.warning(
                "DB query failed in _get_open_alerts_for_location for '%s': %s",
                location_id,
                exc.__class__.__name__,
            )
            if not self._allow_memory_fallback:
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to query open alerts: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc

        return [
            a for a in self._memory_alerts.values()
            if a.get("location_id") == location_id
            and a.get("status") in (AlertStatus.NEW.value, AlertStatus.ACTIVE.value, AlertStatus.ACKNOWLEDGED.value)
        ]

    async def _create_alert_record(
        self,
        location_id: str,
        location_name: str,
        district_name: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        description: str,
        fingerprint: str,
        assessment: RainfallRiskAssessmentResponse,
    ) -> AlertResponse:
        now_utc = datetime.now(timezone.utc)
        alert_id = str(uuid.uuid4())

        evidence = self._build_evidence_payload(assessment)

        # Build strict 15-column DB payload matching public.risk_alerts schema (Migration 005)
        db_record = {
            "id": alert_id,
            "location_id": location_id,
            "alert_type": alert_type.value,
            "severity": severity.value,
            "status": AlertStatus.NEW.value,
            "title": title,
            "description": description,
            "evidence": evidence,
            "fingerprint": fingerprint,
            "created_at": now_utc.isoformat(),
            "updated_at": now_utc.isoformat(),
            "acknowledged_at": None,
            "acknowledged_by": None,
            "resolved_at": None,
            "resolved_reason": None,
        }

        persisted_record = db_record
        # Attempt DB insertion
        try:
            created = await self.supabase.create_alert(db_record)
            if created and isinstance(created, dict):
                persisted_record = created
        except Exception as exc:
            if not self._allow_memory_fallback:
                logger.error("Production DB alert insertion failed: %s", exc.__class__.__name__)
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to persist new alert record: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc
            logger.warning("DB alert insertion fallback to memory: %s", exc.__class__.__name__)

        if self._allow_memory_fallback:
            self._memory_alerts[alert_id] = {
                **persisted_record,
                "location_name": location_name,
                "district_name": district_name,
            }

        logger.info("Generated %s alert for '%s' (severity: %s)", alert_type.value, location_name, severity.value)
        return self._to_alert_response(
            persisted_record,
            location_meta={"location_name": location_name, "district_name": district_name},
        )

    async def _resolve_alert_record(self, alert_id: str, reason: str = "") -> None:
        now_utc = datetime.now(timezone.utc)
        updates = {
            "status": AlertStatus.RESOLVED.value,
            "resolved_at": now_utc.isoformat(),
            "resolved_reason": reason,
            "updated_at": now_utc.isoformat(),
        }

        try:
            await self.supabase.update_alert(alert_id, updates)
        except Exception as exc:
            if not self._allow_memory_fallback:
                logger.error("Production DB alert resolution update failed for '%s': %s", alert_id, exc.__class__.__name__)
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to persist alert resolution: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc
            logger.warning("DB update failed in _resolve_alert_record: %s", exc.__class__.__name__)

        if self._allow_memory_fallback and alert_id in self._memory_alerts:
            self._memory_alerts[alert_id].update(updates)

    @staticmethod
    def _format_rainfall_mm(value: Optional[float]) -> str:
        """Format rainfall values cleanly to stay consistent with frontend evidence cards."""
        if value is None:
            return "0"
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
        return f"{round(numeric, 2):g}"

    @staticmethod
    def _build_evidence_payload(
        assessment: RainfallRiskAssessmentResponse,
        last_evaluated_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Build complete structured evidence dictionary from an assessment."""
        evidence = AlertEvidence(
            risk_category=assessment.risk_category.value,
            confidence=assessment.confidence.value,
            recent_observed_mm=assessment.recent_observed_rainfall_mm,
            forecast_6h_mm=assessment.forecast_6h_precipitation_mm,
            forecast_24h_mm=assessment.forecast_24h_precipitation_mm,
            observation_freshness=assessment.observation_freshness.value,
            observation_age_minutes=assessment.observation_age_minutes,
            forecast_completeness_pct=assessment.forecast_completeness_pct,
            antecedent_category=(
                assessment.antecedent_indicator.indicator_category.value
                if assessment.antecedent_indicator
                else None
            ),
            trigger_reason=f"Evaluated from {assessment.risk_category.value} risk category.",
        ).model_dump()
        if last_evaluated_at is not None:
            evidence["last_evaluated_at"] = last_evaluated_at.isoformat()
        return evidence

    async def _touch_alert(
        self,
        alert_id: str,
        assessment: RainfallRiskAssessmentResponse,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        updates: Dict[str, Any] = {
            "updated_at": now_utc.isoformat(),
            "evidence": self._build_evidence_payload(assessment, last_evaluated_at=now_utc),
        }
        if description is not None:
            updates["description"] = description
        try:
            await self.supabase.update_alert(alert_id, updates)
        except Exception as exc:
            if not self._allow_memory_fallback:
                logger.error("Production DB alert touch update failed for '%s': %s", alert_id, exc.__class__.__name__)
                if isinstance(exc, SupabaseClientError):
                    raise
                raise SupabaseClientError(
                    f"Failed to persist alert touch update: {exc.__class__.__name__}",
                    status_code=503,
                ) from exc
            logger.warning("DB update failed in _touch_alert: %s", exc.__class__.__name__)

        if self._allow_memory_fallback and alert_id in self._memory_alerts:
            self._memory_alerts[alert_id].update(updates)

        return updates

    async def _get_location_metadata_map(self) -> Dict[str, Dict[str, Optional[str]]]:
        """Lookup location_name and district_name via supported monitoring_locations relationship."""
        try:
            locs = await self.supabase.get_pilot_locations()
        except Exception:
            return {}

        if not isinstance(locs, list):
            return {}

        mapping: Dict[str, Dict[str, Optional[str]]] = {}
        for loc in locs:
            if not isinstance(loc, dict) or not loc.get("id"):
                continue
            loc_id = str(loc["id"])
            districts_obj = loc.get("districts")
            district_name = (
                districts_obj.get("name")
                if isinstance(districts_obj, dict)
                else loc.get("district_name")
            )
            mapping[loc_id] = {
                "location_name": loc.get("name"),
                "district_name": district_name,
            }
        return mapping

    async def _resolve_single_location_meta(
        self, record: Dict[str, Any]
    ) -> Optional[Dict[str, Optional[str]]]:
        """Enrich a single alert DB record with location/district display names if absent."""
        if record.get("location_name") and record.get("district_name"):
            return None
        loc_id = str(record.get("location_id") or "")
        if not loc_id:
            return None
        loc_map = await self._get_location_metadata_map()
        return loc_map.get(loc_id)

    def _to_alert_response(
        self,
        r: Dict[str, Any],
        location_meta: Optional[Dict[str, Optional[str]]] = None,
    ) -> AlertResponse:
        created = r.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))

        updated = r.get("updated_at")
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))

        ack_at = r.get("acknowledged_at")
        if isinstance(ack_at, str):
            ack_at = datetime.fromisoformat(ack_at.replace("Z", "+00:00"))

        res_at = r.get("resolved_at")
        if isinstance(res_at, str):
            res_at = datetime.fromisoformat(res_at.replace("Z", "+00:00"))

        # Sanitize evidence and narrative fields before response serialization
        raw_evidence = r.get("evidence") or {}
        sanitized_evidence = {
            k: sanitize_error_message(v) if isinstance(v, str) else v
            for k, v in raw_evidence.items()
        }

        raw_desc = r.get("description", "")
        clean_desc = sanitize_error_message(raw_desc) or raw_desc

        raw_reason = r.get("resolved_reason")
        clean_reason = sanitize_error_message(raw_reason) if raw_reason else None

        # Do NOT reconstruct internal fingerprint from public view records
        fp = r.get("fingerprint")

        meta = location_meta or {}
        loc_name = r.get("location_name") or meta.get("location_name")
        dist_name = r.get("district_name") or meta.get("district_name")

        return AlertResponse(
            id=str(r["id"]),
            location_id=str(r["location_id"]),
            location_name=loc_name,
            district_name=dist_name,
            alert_type=AlertType(r["alert_type"]),
            severity=AlertSeverity(r["severity"]),
            status=AlertStatus(r["status"]),
            title=sanitize_error_message(r.get("title", "")) or r.get("title", ""),
            description=clean_desc,
            evidence=sanitized_evidence,
            fingerprint=fp,
            created_at=created or datetime.now(timezone.utc),
            updated_at=updated or datetime.now(timezone.utc),
            acknowledged_at=ack_at,
            acknowledged_by=r.get("acknowledged_by"),
            resolved_at=res_at,
            resolved_reason=clean_reason,
            disclaimer=DEFAULT_ALERT_DISCLAIMER,
        )


_alert_service_instance: Optional[AlertService] = None


def get_alert_service() -> AlertService:
    """Return application-wide singleton instance of AlertService."""
    global _alert_service_instance
    if _alert_service_instance is None:
        _alert_service_instance = AlertService()
    return _alert_service_instance


def reset_alert_service() -> None:
    """Reset alert service singleton (used in test suites)."""
    global _alert_service_instance
    _alert_service_instance = None
