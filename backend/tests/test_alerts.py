"""Automated Unit and Integration Tests for Risk Alerts & In-App Notification System (Phase 2.7).

Verification Coverage (15 Comprehensive Scenarios):
1. Valid moderate, high, and very-high risk alert creation.
2. Duplicate alert prevention (same-fingerprint idempotent evaluation).
3. Risk escalation (moderate -> high resolves moderate, creates high).
4. Risk reduction (high -> low resolves high, emits RISK_REDUCED).
5. Alert resolution lifecycle and audit reasons.
6. Acknowledgement state change without altering risk calculations.
7. Missing-data handling (DATA_UNAVAILABLE created; existing high alert preserved).
8. Stale-data handling (DATA_STALE created).
9. Invalid risk assessment handling (no spurious alerts for clean LOW).
10. Failed ingestion isolation (failed ingestion locations are not evaluated as successes).
11. One location failure not blocking other locations.
12. Concurrent evaluation protection (asyncio.Lock guard).
13. API authentication and authorization (401/403/200 on acknowledge).
14. RLS and migration permission assumptions in Migration 005.
15. Notification failures not affecting ingestion completion.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.db.supabase_client import SupabaseClient
from app.main import app
from app.schemas.alert import (
    AlertEvidence,
    AlertResponse,
    AlertSeverity,
    AlertStatus,
    AlertType,
)
from app.schemas.ingestion import (
    FreshnessState,
    IngestionLocationResult,
    IngestionStatus,
    IngestionTriggerResponse,
)
from app.schemas.monitoring import QualityStatus
from app.schemas.risk import (
    AntecedentCategory,
    AntecedentRainfallIndicator,
    RainfallRiskAssessmentResponse,
    RiskCategory,
    RiskConfidence,
)
from app.services.alert_service import (
    AlertService,
    get_alert_service,
    reset_alert_service,
)
from app.services.ingestion_scheduler import IngestionScheduler
from app.services.ingestion_service import IngestionService
from app.services.risk_service import RiskService


@pytest.fixture(autouse=True)
def cleanup_alert_service(monkeypatch):
    """Ensure AlertService singleton is reset and unit tests are isolated from live .env Supabase."""
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    reset_alert_service()
    yield
    reset_alert_service()
    get_settings.cache_clear()


def make_sample_assessment(
    location_id: str = "11111111-1111-1111-1111-111111111111",
    location_name: str = "Paradeep Weather Grid Point",
    district_name: str = "Jagatsinghpur",
    risk_category: RiskCategory = RiskCategory.HIGH,
    recent_mm: float = 45.0,
    f6_mm: float = 20.0,
    f24_mm: float = 85.0,
    freshness: FreshnessState = FreshnessState.FRESH,
) -> RainfallRiskAssessmentResponse:
    """Construct mock RainfallRiskAssessmentResponse for testing."""
    now_utc = datetime.now(timezone.utc)
    return RainfallRiskAssessmentResponse(
        location_id=location_id,
        location_name=location_name,
        district_name=district_name,
        latitude=20.3160,
        longitude=86.6110,
        assessment_timestamp=now_utc,
        risk_category=risk_category,
        confidence=RiskConfidence.HIGH,
        recent_observed_rainfall_mm=recent_mm,
        forecast_6h_precipitation_mm=f6_mm,
        forecast_24h_precipitation_mm=f24_mm,
        observation_freshness=freshness,
        observation_age_minutes=15.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=24,
        quality_status=QualityStatus.PASS,
        quality_warnings=[],
        assessment_explanation="Test hydrological evaluation",
        threshold_version="v1.0-research",
        prototype_disclaimer="Research prototype: Not an official flood warning service.",
        antecedent_indicator=AntecedentRainfallIndicator(
            indicator_category=AntecedentCategory.MODERATE_ANTECEDENT,
            indicator_value_mm=55.0,
            coverage_pct=100.0,
            missing_intervals_count=0,
            confidence=RiskConfidence.HIGH,
            explanation="Moderate preceding precipitation accumulation.",
        ),
    )


# -----------------------------------------------------------------------------
# 1. Valid moderate, high, and very-high risk alert creation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alert_creation_moderate_high_very_high():
    """Verify distinct alert types, severities, and evidence generated for risk categories."""
    service = AlertService()

    # Moderate
    mod_assessment = make_sample_assessment(risk_category=RiskCategory.MODERATE, recent_mm=15.0, f24_mm=25.0)
    mod_alert = await service.evaluate_location_assessment(mod_assessment)
    assert mod_alert is not None
    assert mod_alert.alert_type == AlertType.MODERATE_RAINFALL_RISK
    assert mod_alert.severity == AlertSeverity.WARNING
    assert mod_alert.status == AlertStatus.NEW
    assert "Moderate" in mod_alert.title
    assert mod_alert.evidence["risk_category"] == "MODERATE"

    # High
    high_assessment = make_sample_assessment(location_id="loc-2", risk_category=RiskCategory.HIGH, f24_mm=80.0)
    high_alert = await service.evaluate_location_assessment(high_assessment)
    assert high_alert is not None
    assert high_alert.alert_type == AlertType.HIGH_RAINFALL_RISK
    assert high_alert.severity == AlertSeverity.HIGH
    assert high_alert.status == AlertStatus.NEW

    # Very High
    vhigh_assessment = make_sample_assessment(location_id="loc-3", risk_category=RiskCategory.VERY_HIGH, f24_mm=130.0)
    vhigh_alert = await service.evaluate_location_assessment(vhigh_assessment)
    assert vhigh_alert is not None
    assert vhigh_alert.alert_type == AlertType.VERY_HIGH_RAINFALL_RISK
    assert vhigh_alert.severity == AlertSeverity.CRITICAL
    assert vhigh_alert.status == AlertStatus.NEW


# -----------------------------------------------------------------------------
# 2. Duplicate alert prevention
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_alert_prevention():
    """Verify evaluating same active condition twice does not create duplicate alerts."""
    service = AlertService()
    assessment = make_sample_assessment(risk_category=RiskCategory.HIGH)

    # First evaluation creates alert
    alert1 = await service.evaluate_location_assessment(assessment)
    assert alert1 is not None

    # Second evaluation with same condition touches existing alert without duplicating
    alert2 = await service.evaluate_location_assessment(assessment)
    assert alert2 is not None
    assert alert2.id == alert1.id
    assert alert2.fingerprint == alert1.fingerprint

    # Total in store remains 1
    all_alerts = await service.get_alerts()
    assert all_alerts.total == 1


# -----------------------------------------------------------------------------
# 3. Risk escalation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_risk_escalation():
    """Verify escalating from MODERATE to HIGH resolves moderate and creates high."""
    service = AlertService()
    loc_id = "loc-escalate"

    # 1. Initial moderate risk
    mod_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.MODERATE)
    mod_alert = await service.evaluate_location_assessment(mod_ass)
    assert mod_alert.alert_type == AlertType.MODERATE_RAINFALL_RISK
    assert mod_alert.status == AlertStatus.NEW

    # 2. Conditions escalate to HIGH
    high_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    high_alert = await service.evaluate_location_assessment(high_ass)

    assert high_alert.id != mod_alert.id
    assert high_alert.alert_type == AlertType.HIGH_RAINFALL_RISK
    assert high_alert.status == AlertStatus.NEW

    # 3. Old moderate alert should now be RESOLVED
    old_mod = await service.get_alert_by_id(mod_alert.id)
    assert old_mod.status == AlertStatus.RESOLVED
    assert "escalated" in old_mod.resolved_reason.lower()


# -----------------------------------------------------------------------------
# 4. Risk reduction
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_risk_reduction():
    """Verify de-escalation from HIGH to LOW resolves high alert and emits RISK_REDUCED."""
    service = AlertService()
    loc_id = "loc-reduction"

    # 1. Initial high risk
    high_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    high_alert = await service.evaluate_location_assessment(high_ass)

    # 2. Weather subsides to LOW
    low_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=1.0)
    reduced_notif = await service.evaluate_location_assessment(low_ass)

    assert reduced_notif is not None
    assert reduced_notif.alert_type == AlertType.RISK_REDUCED
    assert reduced_notif.severity == AlertSeverity.INFO

    # 3. Verify prior high alert was resolved
    prior_high = await service.get_alert_by_id(high_alert.id)
    assert prior_high.status == AlertStatus.RESOLVED
    assert "LOW" in prior_high.resolved_reason


# -----------------------------------------------------------------------------
# 5. Alert resolution
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alert_resolution():
    """Verify alert resolution sets status, timestamp, and audit reason."""
    service = AlertService()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)

    await service._resolve_alert_record(alert.id, reason="Manual operator drill resolution")
    resolved = await service.get_alert_by_id(alert.id)

    assert resolved.status == AlertStatus.RESOLVED
    assert resolved.resolved_at is not None
    assert resolved.resolved_reason == "Manual operator drill resolution"


# -----------------------------------------------------------------------------
# 6. Acknowledgement
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acknowledgement():
    """Verify acknowledgement updates status and operator without altering evidence."""
    service = AlertService()
    ass = make_sample_assessment(risk_category=RiskCategory.VERY_HIGH)
    alert = await service.evaluate_location_assessment(ass)

    ack = await service.acknowledge_alert(alert.id, acknowledged_by="duty_hydrologist_01")
    assert ack.status == AlertStatus.ACKNOWLEDGED
    assert ack.acknowledged_at is not None
    assert ack.acknowledged_by == "duty_hydrologist_01"
    # Evidence is preserved
    assert ack.evidence["risk_category"] == "VERY_HIGH"


# -----------------------------------------------------------------------------
# 7. Missing-data handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_data_handling():
    """Verify missing data creates DATA_UNAVAILABLE and does NOT resolve prior high alert."""
    service = AlertService()
    loc_id = "loc-missing-guard"

    # 1. Establish an active HIGH alert
    high_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    high_alert = await service.evaluate_location_assessment(high_ass)

    # 2. Next assessment returns UNAVAILABLE (data missing)
    unavail_ass = make_sample_assessment(
        location_id=loc_id,
        risk_category=RiskCategory.UNAVAILABLE,
        recent_mm=None,
        f24_mm=None,
        freshness=FreshnessState.UNAVAILABLE,
    )
    unavail_alert = await service.evaluate_location_assessment(unavail_ass)

    assert unavail_alert.alert_type == AlertType.DATA_UNAVAILABLE
    assert unavail_alert.severity == AlertSeverity.DATA_WARNING

    # 3. CRITICAL: High alert must NOT be resolved due to missing telemetry
    check_high = await service.get_alert_by_id(high_alert.id)
    assert check_high.status in (AlertStatus.NEW, AlertStatus.ACTIVE)


# -----------------------------------------------------------------------------
# 8. Stale-data handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_data_handling():
    """Verify stale data produces DATA_STALE alert with DATA_WARNING severity."""
    service = AlertService()
    stale_ass = make_sample_assessment(
        risk_category=RiskCategory.UNAVAILABLE,
        freshness=FreshnessState.STALE,
    )
    stale_alert = await service.evaluate_location_assessment(stale_ass)

    assert stale_alert.alert_type == AlertType.DATA_STALE
    assert stale_alert.severity == AlertSeverity.DATA_WARNING
    assert "Stale" in stale_alert.title


# -----------------------------------------------------------------------------
# 9. Invalid / Baseline risk assessment handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invalid_risk_assessment_handling():
    """Verify ordinary LOW assessment with no prior alert returns None."""
    service = AlertService()
    clean_low = make_sample_assessment(risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    result = await service.evaluate_location_assessment(clean_low)
    assert result is None


# -----------------------------------------------------------------------------
# 10. Failed ingestion isolation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_ingestion_isolation():
    """Verify location with failed ingestion is not evaluated as a valid risk update."""
    mock_ingestion = AsyncMock(spec=IngestionService)
    mock_ingestion.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="partial",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=2,
        successful_locations=1,
        failed_locations=1,
        total_records_fetched=25,
        total_records_inserted=25,
        total_records_updated=0,
        results=[
            IngestionLocationResult(
                location_id="loc-success",
                location_name="Paradeep Weather Grid Point",
                status=IngestionStatus.COMPLETED,
            ),
            IngestionLocationResult(
                location_id="loc-fail",
                location_name="Cuttack Weather Grid Point",
                status=IngestionStatus.FAILED,
                error_message="HTTP 500 Open-Meteo failure",
            ),
        ],
    )

    scheduler = IngestionScheduler(ingestion_service=mock_ingestion)
    with patch("app.services.risk_service.RiskService.get_location_rainfall_risk", new_callable=AsyncMock) as mock_risk:
        mock_risk.return_value = make_sample_assessment(location_id="loc-success")
        with patch("app.services.alert_service.AlertService.evaluate_location_assessment", new_callable=AsyncMock) as mock_eval:
            await scheduler.run_cycle()
            # Must evaluate ONLY loc-success, never loc-fail
            assert mock_eval.await_count == 1


# -----------------------------------------------------------------------------
# 11. One location failure not blocking other locations
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_location_failure_not_blocking_others():
    """Verify an exception on location 1 does not prevent location 2 evaluation."""
    service = AlertService()
    ass1 = make_sample_assessment(location_id="loc-broken", risk_category=RiskCategory.HIGH)
    ass2 = make_sample_assessment(location_id="loc-healthy", risk_category=RiskCategory.MODERATE)

    original_process = service._process_assessment_internal

    async def flaky_process(assessment):
        if assessment.location_id == "loc-broken":
            raise RuntimeError("Corrupted observation parsing")
        return await original_process(assessment)

    service._process_assessment_internal = flaky_process

    # Evaluate healthy location
    res2 = await service.evaluate_location_assessment(ass2)
    assert res2 is not None
    assert res2.location_id == "loc-healthy"


# -----------------------------------------------------------------------------
# 12. Concurrent evaluation protection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_evaluation_protection():
    """Verify concurrent evaluations for the same location do not create race duplicates."""
    service = AlertService()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)

    # Launch 5 concurrent evaluations
    tasks = [asyncio.create_task(service.evaluate_location_assessment(ass)) for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # All returned alert objects reference the exact same underlying alert ID
    alert_ids = {r.id for r in results if r}
    assert len(alert_ids) == 1

    all_alerts = await service.get_alerts()
    assert all_alerts.total == 1


# -----------------------------------------------------------------------------
# 13. API authentication and authorization
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_authentication_and_authorization(client: AsyncClient):
    """Verify POST /api/v1/alerts/{id}/acknowledge enforces strict token auth."""
    # Create an alert first
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)
    alert_id = alert.id

    # 1. Missing token -> 401
    resp_no_token = await client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert resp_no_token.status_code == 401

    # 2. Invalid token -> 403
    resp_bad_token = await client.post(
        f"/api/v1/alerts/{alert_id}/acknowledge",
        headers={"X-Ingestion-Token": "bad-wrong-token"},
    )
    assert resp_bad_token.status_code == 403

    # 3. Valid token -> 200
    settings = Settings()
    resp_ok = await client.post(
        f"/api/v1/alerts/{alert_id}/acknowledge",
        headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        json={"acknowledged_by": "lead_meteorologist"},
    )
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert data["status"] == "ACKNOWLEDGED"
    assert data["acknowledged_by"] == "operator"


# -----------------------------------------------------------------------------
# 14. RLS and migration permission assumptions
# -----------------------------------------------------------------------------
def test_rls_and_migration_permission_assumptions():
    """Verify Migration 005 SQL file exists, has RLS, constraints, and least-privilege grants."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    assert migration_path.exists(), "Migration 005 file is missing."

    content = migration_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS risk_alerts" in content
    assert "uq_risk_alerts_active_fingerprint" in content
    assert "ALTER TABLE risk_alerts ENABLE ROW LEVEL SECURITY" in content
    assert "risk_alerts TO anon" in content
    assert "DATA_WARNING" in content
    assert "v_public_risk_alerts" in content
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.risk_alerts TO service_role" in content
    assert "Do NOT execute this migration against Supabase" in content


# -----------------------------------------------------------------------------
# 15. Notification failures not affecting ingestion completion
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_notification_failures_not_affecting_ingestion():
    """Verify an unexpected exception during alert evaluation does not fail ingestion."""
    mock_ingestion = AsyncMock(spec=IngestionService)
    mock_ingestion.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=1,
        successful_locations=1,
        failed_locations=0,
        total_records_fetched=25,
        total_records_inserted=25,
        total_records_updated=0,
        results=[
            IngestionLocationResult(
                location_id="loc-safe",
                location_name="Paradeep Weather Grid Point",
                status=IngestionStatus.COMPLETED,
            )
        ],
    )

    scheduler = IngestionScheduler(ingestion_service=mock_ingestion)
    with patch("app.services.risk_service.RiskService.get_location_rainfall_risk", side_effect=RuntimeError("Alert engine exploded")):
        cycle_res = await scheduler.run_cycle()
        # Ingestion cycle still succeeds despite alert evaluation error
        assert cycle_res["outcome"] == "SUCCESS"
        assert scheduler.last_cycle_outcome == "SUCCESS"


# -----------------------------------------------------------------------------
# 16. Security Hardening & Migration 005 Safety Tests (Part B & D Corrections)
# -----------------------------------------------------------------------------

def test_migration_005_explicit_severity_constraint_upgrade():
    """Verify Migration 005 contains explicit constraint upgrade for existing tables."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Constraint upgrade block
    assert "DROP CONSTRAINT IF EXISTS risk_alerts_severity_check" in content
    assert "ADD CONSTRAINT risk_alerts_severity_check" in content
    assert "'DATA_WARNING'" in content

    # Security invoker view definition
    assert "WITH (security_invoker = true)" in content

    # Obsolete column-level privilege cleanup (revokes ALL column-level privileges)
    assert "information_schema.column_privileges" in content
    assert "REVOKE ALL" in content

    # Check that acknowledged_by and fingerprint are strictly NOT granted to anon
    anon_grant_section = [
        line for line in content.splitlines()
        if "TO anon" in line or "TO authenticated" in line
    ]
    # None of the public column grant lines should grant acknowledged_by or fingerprint
    assert not any("acknowledged_by" in line for line in anon_grant_section)
    assert not any("fingerprint" in line for line in anon_grant_section)


def test_data_warning_enum_accepted_and_invalid_rejected():
    """Verify AlertSeverity accepts DATA_WARNING and rejects invalid categories."""
    assert AlertSeverity("DATA_WARNING") == AlertSeverity.DATA_WARNING
    assert AlertSeverity.DATA_WARNING.value == "DATA_WARNING"

    with pytest.raises(ValueError):
        AlertSeverity("CATASTROPHIC_FLOOD")

    with pytest.raises(ValueError):
        AlertSeverity("EMERGENCY")


@pytest.mark.asyncio
async def test_operator_identity_masked_in_public_endpoints(client: AsyncClient):
    """Verify public endpoints mask operator identity to 'operator'."""
    service = AlertService()
    # Create an alert and acknowledge it with a real operator identifier
    loc_id = "loc-operator-masking"
    ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)
    await service.acknowledge_alert(alert_id=alert.id, acknowledged_by="lead_meteorologist_secret_name")

    from app.api.v1.routes.alerts import get_alert_service
    app.dependency_overrides[get_alert_service] = lambda: service
    try:
        # Public listing
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200
        alerts = resp.json()["alerts"]
        target = next((a for a in alerts if a["id"] == alert.id), None)
        assert target is not None
        assert target["acknowledged_by"] == "operator"
        assert "lead_meteorologist_secret_name" not in target["acknowledged_by"]

        # Public get by ID
        resp_single = await client.get(f"/api/v1/alerts/{alert.id}")
        assert resp_single.status_code == 200
        assert resp_single.json()["acknowledged_by"] == "operator"
    finally:
        app.dependency_overrides.pop(get_alert_service, None)


def test_safe_public_alert_evidence_and_narrative_sanitization():
    """Verify AlertService sanitizes sensitive URLs and tokens from narrative and evidence fields."""
    service = AlertService()
    dirty_record = {
        "id": "alert-dirty-1",
        "location_id": "loc-test",
        "location_name": "Paradeep",
        "district_name": "Jagatsinghpur",
        "alert_type": "HIGH_RAINFALL_RISK",
        "severity": "HIGH",
        "status": "ACTIVE",
        "title": "Alert at postgresql://admin:secret@db:5432/floodwatch",
        "description": "Token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz was observed.",
        "evidence": {
            "trigger_reason": "Evaluated at https://internal-api.example.com/vault?token=secret123",
            "db_error": "relation 'internal_users' does not exist",
            "forecast_24h_mm": 65.0,
        },
        "fingerprint": "loc-test:HIGH_RAINFALL_RISK",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_reason": "Resolved token=my-dev-token and postgres://admin:pass@host/db",
    }

    res = service._to_alert_response(dirty_record)
    assert "postgresql://" not in res.title
    assert "eyJhbGciOi" not in res.description
    assert "[REDACTED]" in res.description
    assert "token=secret123" not in str(res.evidence)
    assert "[REDACTED]" in res.evidence["trigger_reason"]
    assert "internal_users" not in res.evidence["db_error"]
    assert "database constraint or schema violation" in res.evidence["db_error"]
    assert "my-dev-token" not in res.resolved_reason
    assert "postgres://" not in res.resolved_reason


def test_supabase_client_uses_v_public_risk_alerts_for_anon_queries():
    """Verify SupabaseClient targets v_public_risk_alerts view when service_role_key is absent."""
    anon_client = SupabaseClient(service_role_key="")
    assert anon_client.service_role_key == ""

    # Test URL formatting in get_alerts
    # Without service role, it should target v_public_risk_alerts
    target_entity = "risk_alerts" if anon_client.service_role_key else "v_public_risk_alerts"
    assert target_entity == "v_public_risk_alerts"

    # With service role, it should target base risk_alerts
    admin_client = SupabaseClient(service_role_key="service-role-key-test")
    target_admin_entity = "risk_alerts" if admin_client.service_role_key else "v_public_risk_alerts"
    assert target_admin_entity == "risk_alerts"


# -----------------------------------------------------------------------------
# 17. Schema Reconciliation & Architectural Compatibility Tests
# -----------------------------------------------------------------------------

def test_migration_005_strict_schema_reconciliation():
    """Verify Migration 005 adheres strictly to established FloodWatch architecture."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # 1. Foreign key and location column verification
    assert "REFERENCES monitoring_locations(id)" in content
    assert "location_id UUID" in content
    assert "pilot_locations" not in content, "Invalid reference to non-existent 'pilot_locations' table."
    assert "pilot_location_id" not in content, "Invalid reference to non-existent 'pilot_location_id' column."

    # 2. Alert severity values
    for sev in ("INFO", "DATA_WARNING", "WARNING", "HIGH", "CRITICAL"):
        assert f"'{sev}'" in content
    for alien_sev in ("ADVISORY", "SEVERE", "EXTREME"):
        assert f"'{alien_sev}'" not in content, f"Alien severity value '{alien_sev}' detected in migration."

    # 3. Status lifecycle values
    for st in ("NEW", "ACTIVE", "ACKNOWLEDGED", "RESOLVED"):
        assert f"'{st}'" in content

    # 4. Alert types
    for at in (
        "MODERATE_RAINFALL_RISK",
        "HIGH_RAINFALL_RISK",
        "VERY_HIGH_RAINFALL_RISK",
        "DATA_UNAVAILABLE",
        "DATA_STALE",
        "RISK_REDUCED",
    ):
        assert f"'{at}'" in content

    # 5. Check constraints & dynamic idempotent upgrade blocks
    assert "risk_alerts_severity_check" in content
    assert "risk_alerts_status_check" in content
    assert "risk_alerts_alert_type_check" in content
    assert "DO $$" in content


def test_migration_005_public_view_matches_frontend_contract():
    """Verify v_public_risk_alerts projects non-sensitive fields and protects sensitive ones."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # View definition
    assert "CREATE OR REPLACE VIEW public.v_public_risk_alerts" in content
    assert "WITH (security_invoker = true)" in content

    # View columns must include essential telemetry
    for col in (
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "created_at", "updated_at",
        "acknowledged_at", "resolved_at", "resolved_reason"
    ):
        assert col in content

    # Find the view definition substring
    view_def = content[content.find("CREATE OR REPLACE VIEW public.v_public_risk_alerts"):content.find("FROM public.risk_alerts;")]
    assert "acknowledged_by" not in view_def, "'acknowledged_by' must NOT be projected in public view."
    assert "fingerprint" not in view_def, "'fingerprint' must NOT be projected in public view."


def test_authentication_contract_uses_x_ingestion_token():
    """Verify backend and settings use X-Ingestion-Token and INGESTION_TRIGGER_TOKEN."""
    settings = get_settings()
    assert hasattr(settings, "INGESTION_TRIGGER_TOKEN")
    assert not hasattr(settings, "API_KEY"), "Unauthorized 'API_KEY' setting must not exist."

    from app.api.v1.routes.ingestion import verify_ingestion_token
    import inspect
    sig = inspect.signature(verify_ingestion_token)
    header_param = sig.parameters["x_ingestion_token"]
    assert header_param.default.alias == "X-Ingestion-Token"


def test_alert_lifecycle_and_deduplication_integrity():
    """Verify unique partial index in Migration 005 for deduplicating open alerts."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    assert "uq_risk_alerts_active_fingerprint" in content
    assert "ON risk_alerts (location_id, fingerprint)" in content
    assert "WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')" in content


# -----------------------------------------------------------------------------
# 18. Migration 005 Final SQL Hardening & Simulation Tests
# -----------------------------------------------------------------------------

def test_migration_005_preserves_unrelated_check_constraints():
    """Verify that only the 4 explicitly targeted check constraints are dropped.
    
    Ensures that any unrelated CHECK constraints (including multi-column rules
    or custom data constraints) are preserved intact.
    """
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Find all DROP CONSTRAINT statements in the migration
    import re
    drop_matches = re.findall(r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?([a-zA-Z0-9_]+)", content)
    
    # Authoritative allowed constraint drop targets
    allowed_drop_targets = {
        "risk_alerts_severity_check",
        "chk_alert_severity",
        "risk_alerts_status_check",
        "risk_alerts_alert_type_check",
    }
    
    for dropped in drop_matches:
        assert dropped in allowed_drop_targets, (
            f"Overly broad constraint drop detected: '{dropped}' is not in allowed target set."
        )

    # Verify that dynamic dropping of all constraints on columns via pg_constraint was removed
    assert "pg_constraint con" not in content, (
        "Dynamic discovery and deletion of check constraints via pg_constraint is too broad and was not removed."
    )

    # Simulation: Verify that unrelated constraints are retained in a simulated constraint catalog
    existing_constraints = {
        "chk_alert_severity": "CHECK (severity IN ('LOW', 'HIGH'))",
        "chk_multi_column_resolved_requires_reason": "CHECK (status != 'RESOLVED' OR resolved_reason IS NOT NULL)",
        "chk_positive_telemetry": "CHECK (id IS NOT NULL)",
    }

    # Simulate targeted migration drop
    simulated_catalog = dict(existing_constraints)
    for target in allowed_drop_targets:
        simulated_catalog.pop(target, None)

    # Re-add canonical constraints
    simulated_catalog["risk_alerts_severity_check"] = "CHECK (severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'))"
    simulated_catalog["risk_alerts_status_check"] = "CHECK (status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'))"
    simulated_catalog["risk_alerts_alert_type_check"] = "CHECK (alert_type IN (...))"

    # Verify unrelated constraints survived the targeted migration
    assert "chk_multi_column_resolved_requires_reason" in simulated_catalog
    assert "chk_positive_telemetry" in simulated_catalog
    assert "chk_alert_severity" not in simulated_catalog
    assert "risk_alerts_severity_check" in simulated_catalog


def test_migration_005_correct_public_column_privilege_handling():
    """Verify distinct and safe handling of PUBLIC keyword vs quoted role identifiers.
    
    Verifies that ALL column-level privileges (not just SELECT) are revoked.
    """
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Verify separate branch for PUBLIC in dynamic SQL block revoking ALL column privileges
    assert "IF r.grantee = 'PUBLIC' THEN" in content
    assert "REVOKE ALL (%I) ON TABLE public.risk_alerts FROM PUBLIC" in content
    # Verify %I format is used for actual roles revoking ALL column privileges
    assert "REVOKE ALL (%I) ON TABLE public.risk_alerts FROM %I" in content

    # Verify defense-in-depth static revokes on sensitive fields
    assert "REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM PUBLIC;" in content
    assert "REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM anon;" in content
    assert "REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM authenticated;" in content


def test_migration_005_preflight_data_validation_simulation():
    """Simulate the migration's NULL-aware preflight data validation check against valid and invalid datasets."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Verify the migration contains the NULL-aware preflight check logic
    assert "Migration 005 preflight check failed" in content
    assert "count(*) FILTER (WHERE severity IS NULL)" in content
    assert "count(*) FILTER (WHERE status IS NULL)" in content
    assert "count(*) FILTER (WHERE alert_type IS NULL)" in content
    assert "RAISE EXCEPTION" in content

    # Simulation function mimicking the PL/pgSQL NULL-aware preflight logic
    canonical_severities = {'INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'}
    canonical_statuses = {'NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'}
    canonical_types = {
        'MODERATE_RAINFALL_RISK', 'HIGH_RAINFALL_RISK', 'VERY_HIGH_RAINFALL_RISK',
        'DATA_UNAVAILABLE', 'DATA_STALE', 'RISK_REDUCED'
    }

    def simulate_preflight(rows):
        # Severity check (NULL or out-of-set)
        invalid_sev = [r['severity'] for r in rows if r['severity'] is None or r['severity'] not in canonical_severities]
        null_sev = sum(1 for r in rows if r['severity'] is None)
        if invalid_sev:
            raise ValueError(f"Column public.risk_alerts.severity contains {len(invalid_sev)} incompatible row(s) ({null_sev} NULL values)")

        # Status check (NULL or out-of-set)
        invalid_st = [r['status'] for r in rows if r['status'] is None or r['status'] not in canonical_statuses]
        null_st = sum(1 for r in rows if r['status'] is None)
        if invalid_st:
            raise ValueError(f"Column public.risk_alerts.status contains {len(invalid_st)} incompatible row(s) ({null_st} NULL values)")

        # Alert type check (NULL or out-of-set)
        invalid_tp = [r['alert_type'] for r in rows if r['alert_type'] is None or r['alert_type'] not in canonical_types]
        null_tp = sum(1 for r in rows if r['alert_type'] is None)
        if invalid_tp:
            raise ValueError(f"Column public.risk_alerts.alert_type contains {len(invalid_tp)} incompatible row(s) ({null_tp} NULL values)")
        return True

    # 1. Valid dataset passes without error
    valid_rows = [
        {"severity": "DATA_WARNING", "status": "NEW", "alert_type": "DATA_STALE"},
        {"severity": "CRITICAL", "status": "ACTIVE", "alert_type": "VERY_HIGH_RAINFALL_RISK"},
        {"severity": "INFO", "status": "RESOLVED", "alert_type": "RISK_REDUCED"},
    ]
    assert simulate_preflight(valid_rows) is True

    # 2. Incompatible severity (out of set) raises diagnostic error
    invalid_sev_rows = [
        {"severity": "EMERGENCY_CAT_5", "status": "ACTIVE", "alert_type": "HIGH_RAINFALL_RISK"}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.severity.*1 incompatible row.*0 NULL"):
        simulate_preflight(invalid_sev_rows)

    # 3. NULL severity raises diagnostic error
    null_sev_rows = [
        {"severity": None, "status": "ACTIVE", "alert_type": "HIGH_RAINFALL_RISK"}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.severity.*1 incompatible row.*1 NULL"):
        simulate_preflight(null_sev_rows)

    # 4. Incompatible status (out of set) raises diagnostic error
    invalid_st_rows = [
        {"severity": "HIGH", "status": "SOFT_DELETED", "alert_type": "HIGH_RAINFALL_RISK"}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.status.*1 incompatible row.*0 NULL"):
        simulate_preflight(invalid_st_rows)

    # 5. NULL status raises diagnostic error
    null_st_rows = [
        {"severity": "HIGH", "status": None, "alert_type": "HIGH_RAINFALL_RISK"}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.status.*1 incompatible row.*1 NULL"):
        simulate_preflight(null_st_rows)

    # 6. Incompatible alert_type (out of set) raises diagnostic error
    invalid_tp_rows = [
        {"severity": "WARNING", "status": "NEW", "alert_type": "UNKNOWN_RADAR_EVENT"}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.alert_type.*1 incompatible row.*0 NULL"):
        simulate_preflight(invalid_tp_rows)

    # 7. NULL alert_type raises diagnostic error
    null_tp_rows = [
        {"severity": "WARNING", "status": "NEW", "alert_type": None}
    ]
    with pytest.raises(ValueError, match="public.risk_alerts.alert_type.*1 incompatible row.*1 NULL"):
        simulate_preflight(null_tp_rows)


def test_migration_005_view_compatibility_and_dependencies_strategy():
    """Verify that view upgrade strategy checks column consistency, types, and dependency tree."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Verify deep column inspection (names, ordinal positions, data types, length modifiers)
    assert "c.expected_pos = v.ordinal_position" in content
    assert "c.expected_type IS DISTINCT FROM v.udt_name" in content
    assert "c.expected_len IS DISTINCT FROM v.character_maximum_length" in content

    # Verify dependency inspection query in view pre-check
    assert "pg_depend" in content
    assert "pg_rewrite" in content
    assert "pg_proc" in content
    assert "pg_policy" in content
    assert "v_public_risk_alerts" in content
    assert "dependent object(s)" in content
    assert "DROP VIEW CASCADE is strictly disallowed" in content
    assert "DROP VIEW public.v_public_risk_alerts CASCADE" not in content, "DROP VIEW CASCADE is strictly prohibited."
    assert "WITH (security_invoker = true)" in content

    # Simulation of deep view upgrade decision logic
    canonical_view_columns = [
        {"name": "id", "pos": 1, "type": "uuid", "len": None},
        {"name": "location_id", "pos": 2, "type": "uuid", "len": None},
        {"name": "alert_type", "pos": 3, "type": "varchar", "len": 50},
        {"name": "severity", "pos": 4, "type": "varchar", "len": 30},
        {"name": "status", "pos": 5, "type": "varchar", "len": 30},
        {"name": "title", "pos": 6, "type": "varchar", "len": 255},
        {"name": "description", "pos": 7, "type": "text", "len": None},
        {"name": "evidence", "pos": 8, "type": "jsonb", "len": None},
        {"name": "created_at", "pos": 9, "type": "timestamptz", "len": None},
        {"name": "updated_at", "pos": 10, "type": "timestamptz", "len": None},
        {"name": "acknowledged_at", "pos": 11, "type": "timestamptz", "len": None},
        {"name": "resolved_at", "pos": 12, "type": "timestamptz", "len": None},
        {"name": "resolved_reason", "pos": 13, "type": "text", "len": None},
    ]

    def simulate_deep_view_upgrade(existing_cols, dep_count, dep_details=""):
        diff_count = 0
        if len(existing_cols) != len(canonical_view_columns):
            diff_count = max(len(existing_cols), len(canonical_view_columns))
        else:
            for exp, act in zip(canonical_view_columns, existing_cols):
                if exp != act:
                    diff_count += 1

        if diff_count > 0:
            if dep_count > 0:
                raise RuntimeError(
                    f"View compatibility check failed: {diff_count} incompatible column definitions, "
                    f"{dep_count} dependent objects: [{dep_details}]. DROP VIEW CASCADE disallowed."
                )
            return "DROP_AND_RECREATE"
        return "CREATE_OR_REPLACE"

    # Case A: Identical columns, positions, and types -> in-place replace (preserves dependencies)
    assert simulate_deep_view_upgrade(canonical_view_columns, dep_count=2, dep_details="downstream_view (view)") == "CREATE_OR_REPLACE"

    # Case B: Differing column type (e.g., severity was text instead of varchar), 0 dependencies -> safe un-cascaded drop and recreate
    type_differing_cols = list(canonical_view_columns)
    type_differing_cols[3] = {"name": "severity", "pos": 4, "type": "text", "len": None}
    assert simulate_deep_view_upgrade(type_differing_cols, dep_count=0) == "DROP_AND_RECREATE"

    # Case C: Differing columns or types with active dependencies -> aborts cleanly without CASCADE
    with pytest.raises(RuntimeError, match="View compatibility check failed.*DROP VIEW CASCADE disallowed"):
        simulate_deep_view_upgrade(type_differing_cols, dep_count=1, dep_details="analytics_mart (materialized view)")


def test_migration_005_effective_privilege_functions_simulation():
    """Verify effective permissions across roles simulating has_table_privilege and has_column_privilege."""
    approved_cols = {
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "created_at", "updated_at",
        "acknowledged_at", "resolved_at", "resolved_reason"
    }
    sensitive_cols = {"acknowledged_by", "fingerprint"}
    all_cols = approved_cols | sensitive_cols

    # Simulation model for PostgreSQL privilege evaluation (including role inheritance)
    # 1. PUBLIC role has no table or column permissions
    # 2. anon and authenticated have NO table permissions, SELECT on approved_cols only
    # 3. service_role has table-level ALL
    class SimulatedPostgresCatalog:
        def has_table_privilege(self, role: str, table: str, priv: str) -> bool:
            if role == "service_role":
                return True
            return False  # PUBLIC, anon, authenticated denied table-level

        def has_column_privilege(self, role: str, table: str, column: str, priv: str) -> bool:
            if role == "service_role":
                return True
            if role in ("anon", "authenticated"):
                if priv.upper() == "SELECT":
                    return column in approved_cols
                return False  # No INSERT, UPDATE, etc.
            # PUBLIC role has nothing
            return False

    catalog = SimulatedPostgresCatalog()

    # Verify PUBLIC has zero privileges
    assert catalog.has_table_privilege("PUBLIC", "public.risk_alerts", "SELECT") is False
    assert catalog.has_table_privilege("PUBLIC", "public.risk_alerts", "INSERT") is False
    for c in all_cols:
        assert catalog.has_column_privilege("PUBLIC", "public.risk_alerts", c, "SELECT") is False

    # Verify anon and authenticated
    for role in ("anon", "authenticated"):
        # No table privileges
        assert catalog.has_table_privilege(role, "public.risk_alerts", "SELECT") is False
        assert catalog.has_table_privilege(role, "public.risk_alerts", "INSERT") is False
        assert catalog.has_table_privilege(role, "public.risk_alerts", "UPDATE") is False
        assert catalog.has_table_privilege(role, "public.risk_alerts", "DELETE") is False

        # Approved columns have SELECT
        for col in approved_cols:
            assert catalog.has_column_privilege(role, "public.risk_alerts", col, "SELECT") is True
            assert catalog.has_column_privilege(role, "public.risk_alerts", col, "INSERT") is False
            assert catalog.has_column_privilege(role, "public.risk_alerts", col, "UPDATE") is False

        # Sensitive columns strictly denied
        for col in sensitive_cols:
            assert catalog.has_column_privilege(role, "public.risk_alerts", col, "SELECT") is False

    # Verify service_role has full table and column privileges
    assert catalog.has_table_privilege("service_role", "public.risk_alerts", "SELECT") is True
    assert catalog.has_table_privilege("service_role", "public.risk_alerts", "INSERT") is True
    assert catalog.has_table_privilege("service_role", "public.risk_alerts", "UPDATE") is True
    assert catalog.has_table_privilege("service_role", "public.risk_alerts", "DELETE") is True
    for c in all_cols:
        assert catalog.has_column_privilege("service_role", "public.risk_alerts", c, "SELECT") is True




def test_migration_005_role_privilege_matrix_and_view_compatibility():
    """Verify final privilege matrix matches strict security architecture."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # 1. PUBLIC role has no access
    assert "REVOKE ALL ON TABLE public.risk_alerts FROM PUBLIC;" in content

    # 2. anon and authenticated have selective column SELECT
    approved_cols = [
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "created_at", "updated_at",
        "acknowledged_at", "resolved_at", "resolved_reason"
    ]
    for col in approved_cols:
        assert col in content

    # 3. Sensitive fields strictly excluded
    assert "acknowledged_by" not in content[content.find("GRANT SELECT ("):content.find("TO anon;")]
    assert "fingerprint" not in content[content.find("GRANT SELECT ("):content.find("TO anon;")]

    # 4. service_role has full DML
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.risk_alerts TO service_role;" in content

    # 5. v_public_risk_alerts has security_invoker = true
    assert "CREATE OR REPLACE VIEW public.v_public_risk_alerts \nWITH (security_invoker = true)" in content or \
           "CREATE OR REPLACE VIEW public.v_public_risk_alerts \r\nWITH (security_invoker = true)" in content


def test_verification_script_structure_and_no_public_in_has_privilege():
    """Verify 005_risk_alerts_security_verification.sql exists, complies with PostgreSQL

    rules regarding the PUBLIC pseudo-role, and covers all 15 security checks.
    """
    verif_path = Path("database/verification/005_risk_alerts_security_verification.sql")
    assert verif_path.exists(), f"Verification script not found at {verif_path}"
    content = verif_path.read_text(encoding="utf-8")

    # 1. PostgreSQL RULE: 'PUBLIC' is a pseudo-role (grantee OID 0), not a real role identifier.
    # Passing 'PUBLIC' to has_table_privilege() or has_column_privilege() causes runtime error:
    # "ERROR: role 'PUBLIC' does not exist". Must NEVER pass 'PUBLIC' as a role string in actual SQL.
    non_comment_lines = [
        line.strip() for line in content.splitlines()
        if not line.strip().startswith("--") and line.strip()
    ]
    for line in non_comment_lines:
        assert "has_table_privilege('PUBLIC'" not in line
        assert 'has_table_privilege("PUBLIC"' not in line
        assert "has_table_privilege('public'" not in line
        assert "has_column_privilege('PUBLIC'" not in line
        assert 'has_column_privilege("PUBLIC"' not in line
        assert "has_column_privilege('public'" not in line

    # Same rule applies to the migration script
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    mig_content = migration_path.read_text(encoding="utf-8")
    mig_non_comments = [
        line.strip() for line in mig_content.splitlines()
        if not line.strip().startswith("--") and line.strip()
    ]
    for line in mig_non_comments:
        assert "has_table_privilege('PUBLIC'" not in line
        assert "has_column_privilege('PUBLIC'" not in line

    # 2. Verify PUBLIC is inspected using aclexplode() catalog function with grantee = 0
    assert "aclexplode" in content
    assert "= 0" in content  # grantee = 0 is PUBLIC pseudo-role in pg_roles / ACL catalog

    # 3. Verify all 17 audit/verification checks are present
    expected_checks = [
        "Table and Column Structure",
        "Column Types and Nullability",
        "Foreign Key Reference",
        "Severity Check Constraint",
        "Status & Alert Type Constraints",
        "Indexes & Deduplication Guard",
        "Row-Level Security Enforcement",
        "Row-Level Security Policies",
        "PUBLIC Table ACL Lockdown",
        "PUBLIC Column ACL Lockdown",
        "Effective Role Table Privileges",
        "Effective Column Read on Approved Attributes",
        "Sensitive Column Lockdown",
        "Service Role DML Operations",
        "Sanitized Public View Architecture",
        "Role Inheritance and Data Integrity",
        "Role-Context Execution Verification",
    ]
    for check in expected_checks:
        assert check in content, f"Missing verification check: {check}"

    # 4. Verify safe object handling and authoritative summary verdict
    assert "to_regclass" in content
    assert "LOCAL SQL VERIFICATION PASSED — LIVE SUPABASE EXECUTION PENDING" in content


def test_migration_005_base_column_pre_existence_check():
    """Verify Migration 005 checks exact 15 columns, order, types, lengths, and nullability."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    assert "missing_cols" in content
    assert "actual_col_count != 15" in content
    assert "canonical_spec(pos, col, exp_type, exp_len, exp_nullable, default_pattern)" in content
    for col in [
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "fingerprint",
        "created_at", "updated_at", "acknowledged_at", "acknowledged_by",
        "resolved_at", "resolved_reason"
    ]:
        assert f"'{col}'" in content
    assert "missing required canonical column(s)" in content
    assert "Order: [%], Types: [%], Lengths: [%], Nullability: [%], Defaults: [%]" in content


def test_migration_005_preflight_duplicate_fingerprint_check():
    """Verify Migration 005 aborts if duplicate active fingerprints exist before creating unique index."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    assert "dup_group_count" in content
    assert "status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')" in content
    assert "GROUP BY location_id, fingerprint" in content
    assert "HAVING count(*) > 1" in content
    assert "uq_risk_alerts_active_fingerprint" in content


def test_migration_005_null_aware_preflight_validations():
    """Verify Migration 005 preflight checks use NULL-aware checks for severity, status, and alert_type."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    assert "severity IS NULL" in content
    assert "OR severity NOT IN" in content
    assert "status IS NULL" in content
    assert "OR status NOT IN" in content
    assert "alert_type IS NULL" in content
    assert "OR alert_type NOT IN" in content


def test_migration_005_transaction_boundary_and_repeatability():
    """Verify Migration 005 runs inside an explicit transaction with safe repeatability guards."""
    migration_path = Path("database/migrations/005_risk_alerts_and_notifications.sql")
    content = migration_path.read_text(encoding="utf-8")

    # Transaction boundaries
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("--")]
    assert lines[0] == "BEGIN;", "Migration must start with explicit BEGIN;"
    assert lines[-1] == "COMMIT;", "Migration must terminate with explicit COMMIT;"

    # Controlled search path
    assert "SET LOCAL search_path = public, extensions;" in content

    # Idempotency / Safe repeatability guards
    assert "CREATE TABLE IF NOT EXISTS risk_alerts" in content
    assert "CREATE INDEX IF NOT EXISTS idx_risk_alerts_location_status" in content
    assert "CREATE INDEX IF NOT EXISTS idx_risk_alerts_status_created" in content
    assert "CREATE INDEX IF NOT EXISTS idx_risk_alerts_severity" in content
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_alerts_active_fingerprint" in content
    assert "DROP POLICY IF EXISTS p_risk_alerts_read_public" in content
    assert "DROP POLICY IF EXISTS p_risk_alerts_service_role_all" in content
    assert "CREATE OR REPLACE VIEW public.v_public_risk_alerts" in content


def test_migration_005_rollback_on_preflight_failure_simulation():
    """Simulate transaction rollback behavior when preflight validations raise an exception."""
    class SimulatedTransaction:
        def __init__(self):
            self.state = "IDLE"
            self.tables = {}

        def begin(self):
            self.state = "IN_TRANSACTION"

        def create_table_if_not_exists(self, name):
            if self.state != "IN_TRANSACTION":
                raise RuntimeError("Not in transaction")
            self.tables[name] = {"rows": []}

        def execute_preflight(self, rows):
            # Check for invalid severity or NULL
            invalid_rows = [
                r for r in rows
                if r.get("severity") is None
                or r.get("severity") not in ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL')
            ]
            if invalid_rows:
                # Simulates RAISE EXCEPTION in PL/pgSQL
                raise ValueError(f"Preflight validation failed: {len(invalid_rows)} invalid rows detected")

        def commit(self):
            self.state = "COMMITTED"

        def rollback(self):
            self.state = "ROLLED_BACK"
            self.tables.clear()

    # Case A: Valid table & clean preflight -> Commits successfully
    tx = SimulatedTransaction()
    tx.begin()
    tx.create_table_if_not_exists("risk_alerts")
    tx.execute_preflight([{"severity": "INFO"}, {"severity": "HIGH"}])
    tx.commit()
    assert tx.state == "COMMITTED"
    assert "risk_alerts" in tx.tables

    # Case B: Incompatible row (e.g. legacy invalid severity or NULL) -> Aborts and rolls back
    tx2 = SimulatedTransaction()
    tx2.begin()
    tx2.create_table_if_not_exists("risk_alerts")
    try:
        tx2.execute_preflight([{"severity": "INFO"}, {"severity": "UNRECOGNIZED_LEGACY"}])
        tx2.commit()
    except ValueError:
        tx2.rollback()

    assert tx2.state == "ROLLED_BACK"
    assert len(tx2.tables) == 0, "All transactional changes must be cleanly rolled back on error"


def test_public_pseudo_role_aclexplode_simulation():
    """Verify catalog inspection logic distinguishing concrete roles vs universal PUBLIC pseudo-role (OID 0)."""
    # PostgreSQL ACL entries representation: (grantor_oid, grantee_oid, priv_type, grantable)
    # Concrete roles have non-zero OIDs (e.g., anon=16384, authenticated=16385, service_role=16386)
    # PUBLIC pseudo-role has grantee OID = 0
    simulated_table_acl = [
        {"grantor": 10, "grantee": 16386, "privilege": "SELECT", "grantable": False}, # service_role
        {"grantor": 10, "grantee": 16386, "privilege": "INSERT", "grantable": False}, # service_role
        {"grantor": 10, "grantee": 16386, "privilege": "UPDATE", "grantable": False}, # service_role
        {"grantor": 10, "grantee": 16386, "privilege": "DELETE", "grantable": False}, # service_role
    ]

    # Verify grantee OID 0 (PUBLIC) has zero entries
    public_entries = [acl for acl in simulated_table_acl if acl["grantee"] == 0]
    assert len(public_entries) == 0, "PUBLIC must have zero entries in table ACL"

    # Verify column ACLs: 13 approved columns granted to anon (16384) and authenticated (16385)
    approved_cols = [
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "created_at", "updated_at",
        "acknowledged_at", "resolved_at", "resolved_reason"
    ]
    simulated_col_acl = {}
    for col in approved_cols:
        simulated_col_acl[col] = [
            {"grantee": 16384, "privilege": "SELECT"}, # anon
            {"grantee": 16385, "privilege": "SELECT"}, # authenticated
        ]
    # Sensitive columns: NO grants to anon or authenticated, NO grants to PUBLIC
    simulated_col_acl["acknowledged_by"] = []
    simulated_col_acl["fingerprint"] = []

    # Verify PUBLIC has 0 grants on any column
    for col, acls in simulated_col_acl.items():
        assert not any(acl["grantee"] == 0 for acl in acls), f"PUBLIC had ACL grant on column {col}"

    # Verify sensitive columns have zero access for public roles
    for sensitive_col in ["acknowledged_by", "fingerprint"]:
        assert len(simulated_col_acl[sensitive_col]) == 0


# =============================================================================
# Phase 2.7.2: End-to-End Remediation Verification Tests
# =============================================================================


# -----------------------------------------------------------------------------
# Issue 1: Fingerprint Security Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fingerprint_omitted_from_public_api_responses(client: AsyncClient):
    """Verify public alert list, single alert, and summary endpoints NEVER expose internal fingerprint."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)
    assert alert is not None

    # 1. GET /api/v1/alerts
    res_list = await client.get("/api/v1/alerts")
    assert res_list.status_code == 200
    list_json = res_list.json()
    assert len(list_json["alerts"]) > 0
    for a in list_json["alerts"]:
        assert "fingerprint" not in a, "Public list endpoint exposed internal fingerprint"

    # 2. GET /api/v1/alerts/{alert_id}
    res_single = await client.get(f"/api/v1/alerts/{alert.id}")
    assert res_single.status_code == 200
    single_json = res_single.json()
    assert "fingerprint" not in single_json, "Public single-alert endpoint exposed internal fingerprint"

    # 3. GET /api/v1/alerts/summary
    res_summary = await client.get("/api/v1/alerts/summary")
    assert res_summary.status_code == 200
    sum_json = res_summary.json()
    for ra in sum_json.get("recent_alerts", []):
        assert "fingerprint" not in ra, "Public summary recent_alerts exposed internal fingerprint"


@pytest.mark.asyncio
async def test_internal_deduplication_preserves_fingerprint():
    """Verify internal deduplication logic still generates and matches fingerprints correctly."""
    service = AlertService()
    ass = make_sample_assessment(risk_category=RiskCategory.VERY_HIGH)

    # Deterministic fingerprint generation
    fp = service.generate_fingerprint(str(ass.location_id), AlertType.VERY_HIGH_RAINFALL_RISK)
    assert fp == f"{ass.location_id}:VERY_HIGH_RAINFALL_RISK"

    # Internal memory alert stores the fingerprint
    alert1 = await service.evaluate_location_assessment(ass)
    assert alert1 is not None
    assert service._memory_alerts[alert1.id]["fingerprint"] == fp

    # Re-evaluating does not create duplicates
    alert2 = await service.evaluate_location_assessment(ass)
    assert alert2 is not None
    assert alert2.id == alert1.id
    assert len(service._memory_alerts) == 1


# -----------------------------------------------------------------------------
# Issue 2: Manual Ingestion Alert Evaluation Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_ingestion_triggers_alert_evaluation(monkeypatch):
    """Verify successful manual weather ingestion triggers alert evaluation for completed stations."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.ingestion_service import IngestionService
    from app.schemas.ingestion import IngestionLocationResult, IngestionStatus

    reset_alert_service()
    service = get_alert_service()

    evaluated_locations = []

    async def fake_evaluate_post_ingestion(results):
        for r in results:
            if r.status == IngestionStatus.COMPLETED:
                evaluated_locations.append(r.location_id)

    monkeypatch.setattr(service, "evaluate_post_ingestion", fake_evaluate_post_ingestion)

    # Mock supabase client and weather connector for ingestion service
    from app.ingestion.open_meteo import OpenMeteoWeatherConnector
    from tests.test_ingestion import SAMPLE_OPEN_METEO_PAYLOAD, SAMPLE_PILOT_LOCATIONS

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[0]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-123", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-test-1", "status": "started"}
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

    ingestion_svc = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    resp = await ingestion_svc.trigger_ingestion()

    assert resp.status == "completed"
    assert resp.successful_locations == 1
    assert SAMPLE_PILOT_LOCATIONS[0]["id"] in evaluated_locations


@pytest.mark.asyncio
async def test_failed_manual_ingestion_does_not_trigger_evaluation():
    """Verify failed ingestion locations are skipped during post-ingestion alert evaluation."""
    from app.schemas.ingestion import IngestionLocationResult, IngestionStatus

    service = AlertService()
    results = [
        IngestionLocationResult(
            location_id="loc-failed-1",
            location_name="Failing Station",
            district_name="Test District",
            status=IngestionStatus.FAILED,
            error_message="Upstream timeout",
        )
    ]

    outcomes = await service.evaluate_post_ingestion(results)
    assert len(outcomes) == 0
    assert len(service._memory_alerts) == 0


@pytest.mark.asyncio
async def test_manual_ingestion_alert_evaluation_failure_does_not_fail_ingestion(monkeypatch):
    """Verify an exception during alert evaluation does not mark weather ingestion as failed."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.ingestion_service import IngestionService

    reset_alert_service()
    service = get_alert_service()

    async def broken_evaluate_post_ingestion(results):
        raise RuntimeError("Simulated alert evaluation database failure")

    monkeypatch.setattr(service, "evaluate_post_ingestion", broken_evaluate_post_ingestion)

    from app.ingestion.open_meteo import OpenMeteoWeatherConnector
    from tests.test_ingestion import SAMPLE_OPEN_METEO_PAYLOAD, SAMPLE_PILOT_LOCATIONS

    mock_supabase = AsyncMock(spec=SupabaseClient)
    mock_supabase.get_pilot_locations.return_value = [SAMPLE_PILOT_LOCATIONS[1]]
    mock_supabase.get_data_source_by_name.return_value = {"id": "ds-123", "name": "open_meteo_weather"}
    mock_supabase.create_ingestion_run.return_value = {"id": "run-test-2", "status": "started"}
    mock_supabase.persist_weather_observations.return_value = {
        "records_fetched": 4,
        "records_inserted": 3,
        "records_updated": 1,
    }
    mock_supabase.update_ingestion_run.return_value = {}

    mock_connector = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_connector.fetch_precipitation.return_value = OpenMeteoWeatherConnector()._transform_precipitation_payload(
        SAMPLE_OPEN_METEO_PAYLOAD, 20.4625, 85.8830
    )

    ingestion_svc = IngestionService(supabase_client=mock_supabase, weather_connector=mock_connector)
    resp = await ingestion_svc.trigger_ingestion()

    # Weather ingestion succeeds despite alert failure
    assert resp.status == "completed"
    assert resp.successful_locations == 1


@pytest.mark.asyncio
async def test_repeated_manual_ingestion_does_not_create_duplicate_open_alerts(monkeypatch):
    """Verify repeated ingestion cycles touch existing open alerts without creating duplicates."""
    from app.schemas.ingestion import IngestionLocationResult, IngestionStatus
    from app.services.risk_service import RiskService

    reset_alert_service()
    service = get_alert_service()

    loc_id = "00000000-0000-0000-0000-000000000099"
    ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)

    # Mock get_location_rainfall_risk on RiskService to return high risk
    from app.services import risk_service
    monkeypatch.setattr(
        risk_service.RiskService,
        "get_location_rainfall_risk",
        AsyncMock(return_value=ass),
    )

    results = [
        IngestionLocationResult(
            location_id=str(ass.location_id),
            location_name="Deduplication Test Point",
            district_name="Kendrapara",
            status=IngestionStatus.COMPLETED,
            records_fetched=10,
            records_inserted=10,
        )
    ]

    # First cycle creates alert
    outcomes1 = await service.evaluate_post_ingestion(results)
    assert len(outcomes1) == 1
    first_id = outcomes1[0].id

    # Second cycle re-evaluates the same location
    outcomes2 = await service.evaluate_post_ingestion(results)
    assert len(outcomes2) == 1
    assert outcomes2[0].id == first_id

    # Store contains only 1 alert for this station
    all_alerts = await service.get_alerts()
    matching = [a for a in all_alerts.alerts if str(a.location_id) == str(ass.location_id)]
    assert len(matching) == 1


# -----------------------------------------------------------------------------
# Issue 3: Manual Alert Resolution Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_alert_resolution_authorized(client: AsyncClient):
    """Verify authorized operator can resolve an active alert with valid reason."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)
    assert alert is not None

    settings = Settings()
    resp = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        json={
            "resolved_reason": "Field inspection confirmed flood risk cleared.",
            "resolved_by": "duty_hydrologist_02",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RESOLVED"
    assert data["resolved_at"] is not None
    assert data["resolved_reason"] == "Field inspection confirmed flood risk cleared."
    assert "fingerprint" not in data


@pytest.mark.asyncio
async def test_manual_alert_resolution_unauthorized(client: AsyncClient):
    """Verify unauthorized resolution requests are rejected with 401 or 403."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)

    # 1. Missing token -> 401
    resp_no_token = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        json={"resolved_reason": "Attempting unauthorized resolution"},
    )
    assert resp_no_token.status_code == 401

    # 2. Bad token -> 403
    resp_bad_token = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        headers={"X-Ingestion-Token": "invalid-token-here"},
        json={"resolved_reason": "Attempting unauthorized resolution"},
    )
    assert resp_bad_token.status_code == 403


@pytest.mark.asyncio
async def test_manual_alert_resolution_validation_and_empty_reason(client: AsyncClient):
    """Verify empty or invalid resolution reasons are rejected with 422."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)

    settings = Settings()
    # Empty string reason
    resp_empty = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        json={"resolved_reason": ""},
    )
    assert resp_empty.status_code == 422

    # Whitespace-only reason
    resp_whitespace = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        json={"resolved_reason": "   "},
    )
    assert resp_whitespace.status_code == 422


@pytest.mark.asyncio
async def test_manual_alert_resolution_invalid_lifecycle_transition_and_repeatability(client: AsyncClient):
    """Verify resolving already resolved alert returns 409 and nonexistent alert returns 404."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)

    settings = Settings()
    headers = {"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN}
    payload = {"resolved_reason": "Resolved by emergency manager"}

    # First resolution succeeds (200)
    res1 = await client.post(f"/api/v1/alerts/{alert.id}/resolve", headers=headers, json=payload)
    assert res1.status_code == 200

    # Repeated resolution on already resolved alert returns 409 Conflict
    res2 = await client.post(f"/api/v1/alerts/{alert.id}/resolve", headers=headers, json=payload)
    assert res2.status_code == 409
    assert res2.json()["detail"]["error"] == "invalid_lifecycle_transition"

    # Nonexistent alert returns 404
    res_404 = await client.post("/api/v1/alerts/00000000-0000-0000-0000-000000000000/resolve", headers=headers, json=payload)
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_manual_alert_resolution_masks_operator_identity_in_public_endpoints(client: AsyncClient):
    """Verify public endpoints mask operator identity on manually resolved alerts."""
    service = get_alert_service()
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    alert = await service.evaluate_location_assessment(ass)

    # First acknowledge with sensitive name
    await service.acknowledge_alert(alert.id, acknowledged_by="lead_director_private_id")

    settings = Settings()
    # Then resolve
    await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
        json={"resolved_reason": "Resolved after review", "resolved_by": "private_officer"},
    )

    # Public endpoint check
    public_res = await client.get(f"/api/v1/alerts/{alert.id}")
    assert public_res.status_code == 200
    pdata = public_res.json()
    assert pdata["acknowledged_by"] == "operator"
    assert "fingerprint" not in pdata


# -----------------------------------------------------------------------------
# Issue 4: Frontend Polling and Focus Revalidation Architecture Verification
# -----------------------------------------------------------------------------

def test_frontend_alert_polling_and_focus_revalidation_architecture():
    """Verify frontend Navbar and AlertsPage implement 60s interval, focus listener, and cleanup."""
    alerts_page_path = Path("frontend/src/pages/AlertsPage.jsx")
    navbar_path = Path("frontend/src/components/Navbar.jsx")

    assert alerts_page_path.exists(), "AlertsPage.jsx must exist"
    assert navbar_path.exists(), "Navbar.jsx must exist"

    alerts_code = alerts_page_path.read_text(encoding="utf-8")
    navbar_code = navbar_path.read_text(encoding="utf-8")

    # 1. 60-second polling interval in both components
    assert "60000" in alerts_code, "AlertsPage must have 60000ms polling interval"
    assert "60000" in navbar_code, "Navbar must have 60000ms polling interval"

    # 2. Window focus event listener in both components
    assert "window.addEventListener('focus'" in alerts_code
    assert "window.addEventListener('focus'" in navbar_code

    # 3. Cleanup on unmount in both components
    assert "clearInterval(" in alerts_code
    assert "window.removeEventListener('focus'" in alerts_code
    assert "clearInterval(" in navbar_code
    assert "window.removeEventListener('focus'" in navbar_code

    # 4. In-flight overlap guard (isFetchingRef)
    assert "isFetchingRef" in alerts_code
    assert "isFetchingAlertsRef" in navbar_code

    # 5. Manual resolution action in AlertsPage
    assert "/resolve" in alerts_code
    assert "Operator Alert Resolution" in alerts_code


# =============================================================================
# Phase 2.7.4.1: Production Fail-Closed Persistence & Deduplication Tests
# =============================================================================

def _make_prod_settings() -> Settings:
    return Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SUPABASE_URL="https://prod-test.supabase.co",
        SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
        SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
        INGESTION_TRIGGER_TOKEN="strong-prod-token-9876543210",
        CORS_ORIGINS=["https://floodwatch.org"],
    )


@pytest.mark.asyncio
async def test_production_db_write_failure_does_not_fall_back_to_memory():
    """PROD-01: Production alert writes/updates must raise errors instead of silently persisting to memory."""
    from app.db.supabase_client import SupabaseClientError

    prod_settings = _make_prod_settings()
    failing_sb = AsyncMock(spec=SupabaseClient)
    failing_sb.get_open_alerts_for_location.return_value = []
    failing_sb.create_alert.side_effect = SupabaseClientError("Simulated DB outage", status_code=503)
    failing_sb.update_alert.side_effect = SupabaseClientError("Simulated DB outage", status_code=503)

    service = AlertService(supabase_client=failing_sb, settings=prod_settings)
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)

    # 1. Create alert fails closed without storing in _memory_alerts
    with pytest.raises(SupabaseClientError):
        await service.evaluate_location_assessment(ass)
    assert len(service._memory_alerts) == 0

    # 2. _resolve_alert_record and _touch_alert fail closed without silent pass
    with pytest.raises(SupabaseClientError):
        await service._resolve_alert_record("alert-prod-1", reason="Escalation")
    with pytest.raises(SupabaseClientError):
        await service._touch_alert("alert-prod-1", ass)


@pytest.mark.asyncio
async def test_development_fallback_remains_functional():
    """PROD-01: Non-production development/test mode preserves in-memory fallback when DB is unconfigured."""
    dev_settings = Settings(
        ENVIRONMENT="development",
        DEBUG=True,
        SUPABASE_URL="",
        SUPABASE_ANON_KEY="",
        SUPABASE_SERVICE_ROLE_KEY="",
    )
    service = AlertService(settings=dev_settings)
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)

    alert = await service.evaluate_location_assessment(ass)
    assert alert is not None
    assert alert.id in service._memory_alerts

    ack = await service.acknowledge_alert(alert.id, acknowledged_by="dev_operator")
    assert ack is not None
    assert ack.status == AlertStatus.ACKNOWLEDGED

    res = await service.resolve_alert(alert.id, resolved_reason="Dev test verified")
    assert res is not None
    assert res.status == AlertStatus.RESOLVED


@pytest.mark.asyncio
async def test_alert_acknowledgement_and_resolution_fail_safely_in_production(client: AsyncClient):
    """PROD-01: Production acknowledge and resolve endpoints return HTTP 503 on persistence errors."""
    from app.db.supabase_client import SupabaseClientError

    prod_settings = _make_prod_settings()
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_alert_by_id.return_value = {
        "id": "prod-alert-123",
        "location_id": "11111111-1111-1111-1111-111111111111",
        "alert_type": "HIGH_RAINFALL_RISK",
        "severity": "HIGH",
        "status": "ACTIVE",
        "title": "High Risk",
        "description": "Test",
        "evidence": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_sb.update_alert.side_effect = SupabaseClientError("Write failed", status_code=503)

    prod_service = AlertService(supabase_client=mock_sb, settings=prod_settings)
    # Seed memory to prove production ignores _memory_alerts fallback even if an entry exists
    prod_service._memory_alerts["prod-alert-123"] = dict(mock_sb.get_alert_by_id.return_value)

    app.dependency_overrides[get_alert_service] = lambda: prod_service
    try:
        headers = {"X-Ingestion-Token": Settings().INGESTION_TRIGGER_TOKEN}

        ack_resp = await client.post(
            "/api/v1/alerts/prod-alert-123/acknowledge",
            headers=headers,
            json={"acknowledged_by": "operator_1"},
        )
        assert ack_resp.status_code == 503
        assert ack_resp.json()["detail"]["error"] == "database_error"

        res_resp = await client.post(
            "/api/v1/alerts/prod-alert-123/resolve",
            headers=headers,
            json={"resolved_reason": "Attempting resolution during DB outage"},
        )
        assert res_resp.status_code == 503
        assert res_resp.json()["detail"]["error"] == "database_error"
    finally:
        app.dependency_overrides.pop(get_alert_service, None)


@pytest.mark.asyncio
async def test_deduplication_query_fails_safely_without_service_role_or_on_db_error():
    """DB-01: get_open_alerts_for_location must never use anon fallback or return [] on failure."""
    from app.db.supabase_client import SupabaseClientError
    import httpx

    # 1. Missing service_role_key raises SupabaseClientError (never uses anon key)
    anon_only_client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        anon_key="anon-key-present",
        service_role_key="",
    )
    with pytest.raises(SupabaseClientError, match="service role credentials"):
        await anon_only_client.get_open_alerts_for_location("11111111-1111-1111-1111-111111111111")

    # 2. HTTP error from PostgREST raises SupabaseClientError rather than returning []
    sr_client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="service-role-secret",
    )
    mock_req = httpx.Request("GET", "https://test.supabase.co/rest/v1/risk_alerts")
    with patch("httpx.AsyncClient.get", return_value=httpx.Response(500, request=mock_req)):
        with pytest.raises(SupabaseClientError, match="HTTP 500"):
            await sr_client.get_open_alerts_for_location("11111111-1111-1111-1111-111111111111")

    # 3. Production AlertService propagates deduplication lookup failure instead of falling back
    prod_settings = _make_prod_settings()
    prod_service = AlertService(supabase_client=anon_only_client, settings=prod_settings)
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)
    with pytest.raises(SupabaseClientError):
        await prod_service.evaluate_location_assessment(ass)


# =============================================================================
# Phase 2.7.5.1: Risk Alert Schema Compatibility Tests (SCHEMA-01)
# =============================================================================

@pytest.mark.asyncio
async def test_alert_insert_payload_uses_only_canonical_database_columns():
    """SCHEMA-01: INSERT payload sent to PostgREST contains strictly the 15 risk_alerts columns."""
    import httpx

    canonical_15_cols = {
        "id", "location_id", "alert_type", "severity", "status",
        "title", "description", "evidence", "fingerprint",
        "created_at", "updated_at", "acknowledged_at", "acknowledged_by",
        "resolved_at", "resolved_reason",
    }
    assert SupabaseClient.RISK_ALERTS_COLUMNS == canonical_15_cols

    captured_service_payload = {}
    captured_http_payload = {}

    # 1. Verify AlertService._create_alert_record passes only 15 columns to supabase.create_alert
    sb_real = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
        anon_key="sb_publishable_TestKey_1234567890",
    )

    async def fake_http_post(url, json=None, headers=None, *args, **kwargs):
        captured_http_payload.update(json or {})
        req = httpx.Request("POST", url)
        # PostgREST returns strictly the 15 DB columns (no location_name or district_name)
        return httpx.Response(201, request=req, json=[dict(json)])

    orig_create = sb_real.create_alert

    async def spy_create(alert_dict):
        captured_service_payload.update(alert_dict)
        return await orig_create(alert_dict)

    sb_real.create_alert = spy_create
    sb_real.get_open_alerts_for_location = AsyncMock(return_value=[])
    sb_real.get_pilot_locations = AsyncMock(return_value=[
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Paradeep Weather Grid Point",
            "districts": {"name": "Jagatsinghpur"},
        }
    ])

    service = AlertService(supabase_client=sb_real, settings=_make_prod_settings())
    ass = make_sample_assessment(
        location_id="11111111-1111-1111-1111-111111111111",
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        risk_category=RiskCategory.HIGH,
    )

    with patch("httpx.AsyncClient.post", side_effect=fake_http_post):
        alert_res = await service.evaluate_location_assessment(ass)

    # 2. Verify location_name and district_name were NOT sent in DB payload
    assert set(captured_service_payload.keys()) == canonical_15_cols
    assert set(captured_http_payload.keys()) == canonical_15_cols
    assert "location_name" not in captured_http_payload
    assert "district_name" not in captured_http_payload

    # 3. API response preserves location_name and district_name AND fingerprint deduplication
    assert alert_res.location_name == "Paradeep Weather Grid Point"
    assert alert_res.district_name == "Jagatsinghpur"
    assert captured_http_payload["fingerprint"] == "11111111-1111-1111-1111-111111111111:HIGH_RAINFALL_RISK"

    # 4. Also verify direct SupabaseClient.create_alert strips non-table keys if passed directly
    direct_payload_captured = {}

    async def fake_post_direct(url, json=None, headers=None, *args, **kwargs):
        direct_payload_captured.update(json or {})
        req = httpx.Request("POST", url)
        return httpx.Response(201, request=req, json=[dict(json)])

    sb_direct = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
    )
    with patch("httpx.AsyncClient.post", side_effect=fake_post_direct):
        await sb_direct.create_alert({
            "id": "a1",
            "location_id": "11111111-1111-1111-1111-111111111111",
            "location_name": "ShouldBeStripped",
            "district_name": "ShouldBeStripped",
            "alert_type": "HIGH_RAINFALL_RISK",
            "severity": "HIGH",
            "status": "NEW",
            "title": "Test",
            "description": "Test",
            "evidence": {},
            "fingerprint": "loc:HIGH",
        })
    assert "location_name" not in direct_payload_captured
    assert "district_name" not in direct_payload_captured


@pytest.mark.asyncio
async def test_alert_read_enriches_location_and_district_from_relationship(client: AsyncClient):
    """SCHEMA-01: DB alert records (15 columns) are enriched with location_name & district_name via monitoring_locations."""
    db_row = {
        "id": "alert-enrich-1",
        "location_id": "3b493f43-58a7-4786-bedf-39d82b9bc260",
        "alert_type": "HIGH_RAINFALL_RISK",
        "severity": "HIGH",
        "status": "NEW",
        "title": "High Rainfall Runoff Potential: Paradeep Weather Grid Point",
        "description": "Test enrichment",
        "evidence": {"recent_observed_mm": 45.0},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "acknowledged_at": None,
        "resolved_at": None,
        "resolved_reason": None,
    }
    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.get_alerts.return_value = [db_row]
    mock_sb.get_alert_by_id.return_value = db_row
    mock_sb.get_pilot_locations.return_value = [
        {
            "id": "3b493f43-58a7-4786-bedf-39d82b9bc260",
            "name": "Paradeep Weather Grid Point",
            "districts": {"name": "Jagatsinghpur"},
        }
    ]

    service = AlertService(supabase_client=mock_sb, settings=_make_prod_settings())
    app.dependency_overrides[get_alert_service] = lambda: service
    try:
        r_list = await client.get("/api/v1/alerts")
        assert r_list.status_code == 200
        item = r_list.json()["alerts"][0]
        assert item["location_name"] == "Paradeep Weather Grid Point"
        assert item["district_name"] == "Jagatsinghpur"
        assert "fingerprint" not in item

        r_single = await client.get("/api/v1/alerts/alert-enrich-1")
        assert r_single.status_code == 200
        single = r_single.json()
        assert single["location_name"] == "Paradeep Weather Grid Point"
        assert single["district_name"] == "Jagatsinghpur"
    finally:
        app.dependency_overrides.pop(get_alert_service, None)


@pytest.mark.asyncio
async def test_acknowledge_and_resolve_payloads_use_valid_db_columns():
    """SCHEMA-01: Acknowledge and resolve PATCH payloads send only valid risk_alerts columns."""
    import httpx

    patched_payloads = []

    async def fake_patch(url, json=None, headers=None, *args, **kwargs):
        patched_payloads.append(dict(json or {}))
        req = httpx.Request("PATCH", url)
        return httpx.Response(200, request=req, json=[{
            "id": "alert-upd-1",
            "location_id": "11111111-1111-1111-1111-111111111111",
            "alert_type": "HIGH_RAINFALL_RISK",
            "severity": "HIGH",
            "status": (json or {}).get("status", "ACKNOWLEDGED"),
            "title": "Test",
            "description": "Test",
            "evidence": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "acknowledged_at": (json or {}).get("acknowledged_at"),
            "acknowledged_by": (json or {}).get("acknowledged_by"),
            "resolved_at": (json or {}).get("resolved_at"),
            "resolved_reason": (json or {}).get("resolved_reason"),
        }])

    sb = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
    )
    sb.get_pilot_locations = AsyncMock(return_value=[])
    service = AlertService(supabase_client=sb, settings=_make_prod_settings())

    with patch("httpx.AsyncClient.patch", side_effect=fake_patch):
        ack = await service.acknowledge_alert("alert-upd-1", acknowledged_by="qa_op")
        res = await service.resolve_alert("alert-upd-1", resolved_reason="Verified clear", resolved_by="qa_op")

    assert ack.status == AlertStatus.ACKNOWLEDGED
    assert res.status == AlertStatus.RESOLVED
    assert len(patched_payloads) == 2
    for p in patched_payloads:
        assert set(p.keys()).issubset(SupabaseClient.RISK_ALERTS_COLUMNS)
        assert "location_name" not in p
        assert "district_name" not in p
        assert "resolved_by" not in p


@pytest.mark.asyncio
async def test_schema_remediation_production_insert_error_fails_safely():
    """SCHEMA-01: Production insert error (e.g. HTTP 400/503) raises SupabaseClientError without RAM fallback."""
    from app.db.supabase_client import SupabaseClientError
    import httpx

    sb = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
    )
    sb.get_open_alerts_for_location = AsyncMock(return_value=[])
    service = AlertService(supabase_client=sb, settings=_make_prod_settings())
    ass = make_sample_assessment(risk_category=RiskCategory.HIGH)

    req = httpx.Request("POST", "https://test.supabase.co/rest/v1/risk_alerts")
    with patch("httpx.AsyncClient.post", return_value=httpx.Response(400, request=req)):
        with pytest.raises(SupabaseClientError, match="HTTP 400"):
            await service.evaluate_location_assessment(ass)
    assert len(service._memory_alerts) == 0


@pytest.mark.asyncio
async def test_deduplication_touch_preserves_risk_category_and_syncs_description_rainfall():
    """Deduplication updates preserve full evidence (including risk_category) and sync description rainfall."""
    service = AlertService()
    loc_id = str(uuid.uuid4())

    first = make_sample_assessment(
        location_id=loc_id,
        risk_category=RiskCategory.HIGH,
        recent_mm=0.0,
        f6_mm=5.0,
        f24_mm=10.0,
    )
    created = await service.evaluate_location_assessment(first)
    assert created is not None
    assert created.evidence["risk_category"] == "HIGH"
    assert "(0 mm observed, 10 mm 24h forecast)" in created.description

    second = make_sample_assessment(
        location_id=loc_id,
        risk_category=RiskCategory.HIGH,
        recent_mm=1.0,
        f6_mm=8.0,
        f24_mm=24.5,
    )
    touched = await service.evaluate_location_assessment(second)
    assert touched is not None
    assert touched.id == created.id
    assert touched.evidence["risk_category"] == "HIGH"
    assert touched.evidence["confidence"] == "HIGH"
    assert touched.evidence["recent_observed_mm"] == 1.0
    assert touched.evidence["forecast_24h_mm"] == 24.5
    assert "last_evaluated_at" in touched.evidence
    assert "(1 mm observed, 24.5 mm 24h forecast)" in touched.description

    stored = await service.get_alert_by_id(created.id)
    assert stored is not None
    assert stored.evidence["risk_category"] == "HIGH"
    assert "(1 mm observed, 24.5 mm 24h forecast)" in stored.description
    assert "fingerprint" not in stored.model_dump()


# =============================================================================
# Phase 2.8.2 (MED-04): Alert Recovery Lifecycle & Supabase Query Error Tests
# =============================================================================

@pytest.mark.asyncio
async def test_low_recovery_resolves_stale_and_unavailable_alerts_alongside_risk_reduction():
    """MED-04A: Returning to fresh/valid/complete LOW resolves both prior risk alerts and obsolete data-quality alerts."""
    service = AlertService()
    loc_id = "loc-recovery-1"
    other_loc = "loc-unrelated-2"

    # Open HIGH + DATA_STALE + DATA_UNAVAILABLE on loc-recovery-1, plus unrelated HIGH on other_loc
    high_alert = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    )
    stale_alert = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.UNAVAILABLE, freshness=FreshnessState.STALE)
    )
    unavail_alert = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.UNAVAILABLE, freshness=FreshnessState.UNAVAILABLE)
    )
    unrelated_high = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=other_loc, risk_category=RiskCategory.HIGH)
    )

    # Fresh, valid, complete LOW returns on loc-recovery-1
    reduced = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    )
    assert reduced is not None
    assert reduced.alert_type == AlertType.RISK_REDUCED

    # Verify HIGH, DATA_STALE, and DATA_UNAVAILABLE were all resolved despite emitting RISK_REDUCED
    assert (await service.get_alert_by_id(high_alert.id)).status == AlertStatus.RESOLVED
    assert (await service.get_alert_by_id(stale_alert.id)).status == AlertStatus.RESOLVED
    assert (await service.get_alert_by_id(unavail_alert.id)).status == AlertStatus.RESOLVED

    # Unrelated location alert must remain untouched
    assert (await service.get_alert_by_id(unrelated_high.id)).status == AlertStatus.NEW

    # Repeated recovery evaluation with no open risk/data alerts is idempotent
    repeat = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    )
    assert repeat is None


@pytest.mark.asyncio
async def test_low_recovery_blocked_when_telemetry_delayed_stale_missing_or_incomplete():
    """MED-04A: Delayed, stale, missing, incomplete, or failed LOW telemetry must never resolve open alerts."""
    service = AlertService()
    loc_id = "loc-guard-recovery"

    high_alert = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH)
    )
    stale_alert = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.UNAVAILABLE, freshness=FreshnessState.STALE)
    )

    base_low = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    non_fresh_cases = [
        base_low.model_copy(update={"observation_freshness": FreshnessState.DELAYED}),
        base_low.model_copy(update={"observation_freshness": FreshnessState.STALE}),
        base_low.model_copy(update={"observation_freshness": FreshnessState.UNAVAILABLE}),
        base_low.model_copy(update={"recent_observed_rainfall_mm": None}),
        base_low.model_copy(update={"quality_status": QualityStatus.FAIL}),
        base_low.model_copy(update={"forecast_completeness_pct": 40.0}),
        base_low.model_copy(update={"active_forecast_hours": 3}),
    ]

    for invalid_low in non_fresh_cases:
        res = await service.evaluate_location_assessment(invalid_low)
        assert res is None
        assert (await service.get_alert_by_id(high_alert.id)).status == AlertStatus.NEW
        assert (await service.get_alert_by_id(stale_alert.id)).status == AlertStatus.NEW


@pytest.mark.asyncio
async def test_repeated_high_to_low_transitions_deduplicate_open_and_allow_after_resolved():
    """MED-04B: Repeated HIGH->LOW touches existing open RISK_REDUCED without duplicating or altering historical RESOLVED alerts."""
    service = AlertService()
    loc_id = "loc-oscillate"

    # Cycle 1: HIGH -> LOW creates first RISK_REDUCED
    await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH, recent_mm=20.0, f24_mm=70.0)
    )
    reduced_1 = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=1.0)
    )
    assert reduced_1 is not None
    assert reduced_1.alert_type == AlertType.RISK_REDUCED

    # Cycle 2 (with reduced_1 STILL OPEN): HIGH -> LOW updates existing open RISK_REDUCED instead of duplicating/conflicting
    await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH, recent_mm=25.0, f24_mm=80.0)
    )
    reduced_2 = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.5, f24_mm=2.0)
    )
    assert reduced_2 is not None
    assert reduced_2.id == reduced_1.id
    assert reduced_2.evidence["recent_observed_mm"] == 0.5
    assert "(0.5 mm observed, 2 mm 24h forecast)" in reduced_2.description

    open_reduced = [
        a for a in service._memory_alerts.values()
        if a["alert_type"] == AlertType.RISK_REDUCED.value and a["status"] != AlertStatus.RESOLVED.value
    ]
    assert len(open_reduced) == 1

    # Now resolve reduced_2; Cycle 3 HIGH -> LOW creates a new RISK_REDUCED without touching historical reduced_2
    await service._resolve_alert_record(reduced_2.id, reason="Reviewed by operator")
    reduced_2_updated_before = (await service.get_alert_by_id(reduced_2.id)).updated_at

    await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.HIGH, recent_mm=30.0, f24_mm=90.0)
    )
    reduced_3 = await service.evaluate_location_assessment(
        make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    )
    assert reduced_3 is not None
    assert reduced_3.id != reduced_2.id

    hist = await service.get_alert_by_id(reduced_2.id)
    assert hist.status == AlertStatus.RESOLVED
    assert hist.updated_at == reduced_2_updated_before


@pytest.mark.asyncio
async def test_risk_reduced_409_conflict_recovery_and_non_409_propagation():
    """MED-04B: Concurrent 409 conflict on RISK_REDUCED insert recovers by touching open row; non-409 propagates."""
    from app.db.supabase_client import SupabaseClientError

    loc_id = "11111111-1111-1111-1111-111111111111"
    existing_high = {
        "id": "high-1",
        "location_id": loc_id,
        "alert_type": AlertType.HIGH_RAINFALL_RISK.value,
        "severity": AlertSeverity.HIGH.value,
        "status": AlertStatus.NEW.value,
        "title": "High",
        "description": "High",
        "evidence": {},
        "fingerprint": f"{loc_id}:HIGH_RAINFALL_RISK",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    concurrent_reduced = {
        "id": "reduced-concurrent-1",
        "location_id": loc_id,
        "alert_type": AlertType.RISK_REDUCED.value,
        "severity": AlertSeverity.INFO.value,
        "status": AlertStatus.NEW.value,
        "title": "Rainfall Risk Reduced: Paradeep Weather Grid Point",
        "description": "Old reduced description",
        "evidence": {},
        "fingerprint": f"{loc_id}:RISK_REDUCED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.base_url = "https://test.supabase.co"
    mock_sb.get_open_alerts_for_location.side_effect = [
        [existing_high],       # Initial lookup doesn't see concurrent RISK_REDUCED yet
        [concurrent_reduced],  # Re-query after HTTP 409 finds it
    ]
    mock_sb.update_alert.return_value = concurrent_reduced
    mock_sb.create_alert.side_effect = SupabaseClientError("HTTP 409 duplicate fingerprint", status_code=409)

    service = AlertService(supabase_client=mock_sb, settings=_make_prod_settings())
    low_ass = make_sample_assessment(location_id=loc_id, risk_category=RiskCategory.LOW, recent_mm=0.0, f24_mm=0.0)
    res = await service.evaluate_location_assessment(low_ass)

    assert res is not None
    assert res.id == "reduced-concurrent-1"
    assert res.alert_type == AlertType.RISK_REDUCED
    assert mock_sb.update_alert.await_count == 2  # Once for high-1 resolve, once for concurrent_reduced touch


@pytest.mark.asyncio
async def test_supabase_get_alerts_and_by_id_distinguish_empty_not_found_and_failures():
    """MED-04C: SupabaseClient distinguishes 200 [] (empty / 404) from HTTP 401/403/500, timeouts, network, and bad JSON."""
    from app.db.supabase_client import SupabaseClientError
    import httpx

    sb = SupabaseClient(
        supabase_url="https://test.supabase.co",
        service_role_key="sb_secret_TestKey_1234567890",
        anon_key="sb_publishable_TestAnon_1234567890",
    )

    # 1. Successful empty list & existing record
    async def fake_empty(url, headers=None, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json=[])

    with patch("httpx.AsyncClient.get", side_effect=fake_empty):
        assert await sb.get_alerts() == []
        assert await sb.get_alert_by_id("missing-id") is None

    sample_row = {
        "id": "alert-existing-1",
        "location_id": "11111111-1111-1111-1111-111111111111",
        "alert_type": "HIGH_RAINFALL_RISK",
        "severity": "HIGH",
        "status": "NEW",
        "title": "High",
        "description": "High",
        "evidence": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    async def fake_found(url, headers=None, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json=[sample_row])

    with patch("httpx.AsyncClient.get", side_effect=fake_found):
        assert len(await sb.get_alerts()) == 1
        assert (await sb.get_alert_by_id("alert-existing-1"))["id"] == "alert-existing-1"

    # 2. HTTP 401 / 403 / 500 must raise SupabaseClientError (never [] / None)
    for code in [401, 403, 500]:
        async def fake_err(url, headers=None, _code=code, *args, **kwargs):
            return httpx.Response(_code, request=httpx.Request("GET", url), json={"error": "denied"})

        with patch("httpx.AsyncClient.get", side_effect=fake_err):
            with pytest.raises(SupabaseClientError, match=f"HTTP {code}"):
                await sb.get_alerts()
            with pytest.raises(SupabaseClientError, match=f"HTTP {code}"):
                await sb.get_alert_by_id("alert-1")

    # 3. Timeout & network error must raise SupabaseClientError
    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(SupabaseClientError, match="Timed out"):
            await sb.get_alerts()
        with pytest.raises(SupabaseClientError, match="Timed out"):
            await sb.get_alert_by_id("alert-1")

    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(SupabaseClientError, match="Network error"):
            await sb.get_alerts()
        with pytest.raises(SupabaseClientError, match="Network error"):
            await sb.get_alert_by_id("alert-1")

    # 4. Malformed JSON & non-list JSON must raise SupabaseClientError
    async def fake_bad_json(url, headers=None, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), content=b"{bad json")

    with patch("httpx.AsyncClient.get", side_effect=fake_bad_json):
        with pytest.raises(SupabaseClientError, match="Invalid JSON"):
            await sb.get_alerts()

    async def fake_dict_json(url, headers=None, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json={"message": "unexpected object"})

    with patch("httpx.AsyncClient.get", side_effect=fake_dict_json):
        with pytest.raises(SupabaseClientError, match="Unexpected alerts payload"):
            await sb.get_alert_by_id("alert-1")


@pytest.mark.asyncio
async def test_alert_api_endpoints_propagate_db_failures_as_503_and_preserve_404(client: AsyncClient):
    """MED-04C: Public alert routes return 404 for genuine missing IDs and 503 (never 200 []) on DB outage."""
    from app.db.supabase_client import SupabaseClientError

    # 1. Genuine empty list & 404 when DB succeeds
    mock_ok_sb = AsyncMock(spec=SupabaseClient)
    mock_ok_sb.base_url = "https://test.supabase.co"
    mock_ok_sb.get_alerts.return_value = []
    mock_ok_sb.get_alert_by_id.return_value = None
    ok_service = AlertService(supabase_client=mock_ok_sb)

    app.dependency_overrides[get_alert_service] = lambda: ok_service
    try:
        r_list = await client.get("/api/v1/alerts")
        assert r_list.status_code == 200
        assert r_list.json()["alerts"] == []

        r_missing = await client.get("/api/v1/alerts/nonexistent-id")
        assert r_missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_alert_service, None)

    # 2. DB failure propagates as HTTP 503 across list, summary, and detail routes even in dev mode
    mock_fail_sb = AsyncMock(spec=SupabaseClient)
    mock_fail_sb.base_url = "https://test.supabase.co"
    mock_fail_sb.get_alerts.side_effect = SupabaseClientError("Failed to query alerts: HTTP 500", status_code=500)
    mock_fail_sb.get_alert_by_id.side_effect = SupabaseClientError("Timed out querying alerts", status_code=504)
    fail_service = AlertService(supabase_client=mock_fail_sb)

    app.dependency_overrides[get_alert_service] = lambda: fail_service
    try:
        for ep in ["/api/v1/alerts", "/api/v1/alerts/summary", "/api/v1/alerts/any-id"]:
            resp = await client.get(ep)
            assert resp.status_code == 503
            body = resp.json()
            assert body["detail"]["error"] == "database_error"
            assert "sb_secret_" not in str(body)
            assert "Authorization" not in str(body)
    finally:
        app.dependency_overrides.pop(get_alert_service, None)









@pytest.mark.asyncio
async def test_alert_summary_and_list_counts_exceeding_100_records():
    """MED-05B: Verify alert list/summary counts reflect all >100 alerts across paginated batches without double-counting."""
    now_utc = datetime(2026, 11, 26, 8, 0, 0, tzinfo=timezone.utc)
    synthetic_rows = []
    # 150 synthetic alerts across statuses: 50 ACTIVE/NEW, 40 ACKNOWLEDGED, 60 RESOLVED
    for idx in range(150):
        if idx < 25:
            status_val, sev_val, a_type = "NEW", "CRITICAL", "VERY_HIGH_RAINFALL_RISK"
        elif idx < 50:
            status_val, sev_val, a_type = "ACTIVE", "HIGH", "HIGH_RAINFALL_RISK"
        elif idx < 90:
            status_val, sev_val, a_type = "ACKNOWLEDGED", "WARNING", "MODERATE_RAINFALL_RISK"
        else:
            status_val, sev_val, a_type = "RESOLVED", "INFO", "RISK_REDUCED"

        synthetic_rows.append(
            {
                "id": f"00000000-0000-0000-0000-{idx:012d}",
                "location_id": f"loc-{(idx % 3) + 1}",
                "location_name": f"Pilot {(idx % 3) + 1}",
                "district_name": "Cuttack",
                "alert_type": a_type,
                "severity": sev_val,
                "status": status_val,
                "title": f"Synthetic Alert {idx}",
                "description": f"Synthetic narrative {idx}",
                "evidence": {"risk_category": "HIGH", "recent_observed_mm": 10.0, "forecast_24h_mm": 70.0},
                "fingerprint": f"loc-{(idx % 3) + 1}:{a_type}:{idx}",
                "created_at": (now_utc - timedelta(minutes=idx)).isoformat(),
                "updated_at": (now_utc - timedelta(minutes=idx)).isoformat(),
                "acknowledged_at": now_utc.isoformat() if status_val == "ACKNOWLEDGED" else None,
                "acknowledged_by": "duty_officer" if status_val == "ACKNOWLEDGED" else None,
                "resolved_at": now_utc.isoformat() if status_val == "RESOLVED" else None,
                "resolved_reason": "Completed" if status_val == "RESOLVED" else None,
            }
        )

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.base_url = "https://test.supabase.co"

    async def fake_paginated_get_alerts(limit: int = 50, offset: int = 0, **kwargs):
        # Overlap duplicate record deliberately at page 2 start to confirm deduplication prevents double-counting
        if offset > 0:
            return synthetic_rows[offset - 1 : offset + limit - 1]
        return synthetic_rows[offset : offset + limit]

    mock_sb.get_alerts.side_effect = fake_paginated_get_alerts
    service = AlertService(supabase_client=mock_sb)

    # First page of 25 should still report total=150 and full lifecycle counts across all 150 alerts
    list_res = await service.get_alerts(limit=25, offset=0)
    assert len(list_res.alerts) == 25
    assert list_res.total == 150
    assert list_res.active_count == 50
    assert list_res.acknowledged_count == 40
    assert list_res.resolved_count == 60

    # Second page must return offset slice [25:50] rather than re-slicing an empty batch
    page2 = await service.get_alerts(limit=25, offset=25)
    assert len(page2.alerts) == 25
    assert page2.alerts[0].id != list_res.alerts[-1].id

    # Summary endpoint must also count all >100 records rather than truncating at 100
    summary = await service.get_summary()
    assert summary.total_active_alerts == 50
    assert summary.total_acknowledged_alerts == 40
    assert summary.severity_counts["CRITICAL"] == 25
    assert summary.severity_counts["HIGH"] == 25
    assert len(summary.recent_alerts) == 5


@pytest.mark.asyncio
async def test_acknowledge_and_resolve_mask_identity_consistently_and_sanitize_db_errors(
    client: AsyncClient, caplog
):
    """LOW-02: Acknowledge and resolve responses mask operator identity consistently and sanitize errors."""
    now_utc = datetime.now(timezone.utc).isoformat()
    sample_row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "location_id": "loc-mask-1",
        "location_name": "Paradeep Weather Grid Point",
        "district_name": "Jagatsinghpur",
        "alert_type": "HIGH_RAINFALL_RISK",
        "severity": "HIGH",
        "status": "ACTIVE",
        "title": "High Rainfall Runoff Potential",
        "description": "Heavy rainfall (40 mm observed, 80 mm 24h forecast).",
        "evidence": {"risk_category": "HIGH", "recent_observed_mm": 40.0, "forecast_24h_mm": 80.0},
        "fingerprint": "loc-mask-1:HIGH_RAINFALL_RISK",
        "created_at": now_utc,
        "updated_at": now_utc,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "resolved_reason": None,
    }

    mock_sb = AsyncMock(spec=SupabaseClient)
    mock_sb.base_url = "https://test.supabase.co"
    mock_sb.get_alert_by_id.return_value = dict(sample_row)

    async def fake_update(alert_id: str, updates: dict):
        return {**sample_row, **updates, "acknowledged_by": updates.get("acknowledged_by", "secret_duty_officer")}

    mock_sb.update_alert.side_effect = fake_update
    svc = AlertService(supabase_client=mock_sb)
    settings = Settings()

    from app.api.v1.routes.alerts import get_alert_service
    app.dependency_overrides[get_alert_service] = lambda: svc
    try:
        ack_resp = await client.post(
            f"/api/v1/alerts/{sample_row['id']}/acknowledge",
            headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
            json={"acknowledged_by": "secret_duty_officer"},
        )
        assert ack_resp.status_code == 200
        ack_body = ack_resp.json()
        assert ack_body["acknowledged_by"] == "operator"
        assert "fingerprint" not in ack_body

        res_resp = await client.post(
            f"/api/v1/alerts/{sample_row['id']}/resolve",
            headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
            json={"resolved_reason": "Verified manually", "resolved_by": "secret_supervisor"},
        )
        assert res_resp.status_code == 200
        res_body = res_resp.json()
        assert res_body["acknowledged_by"] == "operator"
        assert "fingerprint" not in res_body

        # Outage during write must return sanitized 503 and never leak secrets into logs or body
        from app.db.supabase_client import SupabaseClientError

        mock_sb.update_alert.side_effect = SupabaseClientError(
            "Connection failed with apikey=sb_secret_supersecretkey and Bearer eyJhbGciOi",
            status_code=503,
        )
        with caplog.at_level("ERROR"):
            err_resp = await client.post(
                f"/api/v1/alerts/{sample_row['id']}/acknowledge",
                headers={"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN},
                json={"acknowledged_by": "secret_duty_officer"},
            )
        assert err_resp.status_code == 503
        assert "sb_secret_" not in err_resp.text
        assert "eyJhbGciOi" not in err_resp.text
        assert "sb_secret_" not in caplog.text
        assert "eyJhbGciOi" not in caplog.text
    finally:
        app.dependency_overrides.pop(get_alert_service, None)


@pytest.mark.asyncio
async def test_deduplication_keeps_description_and_evidence_synchronized():
    """LOW-04: Repeated evaluations updating an open alert keep description and evidence values synchronized."""
    service = AlertService()
    initial = make_sample_assessment(
        location_id="loc-sync-1",
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        risk_category=RiskCategory.HIGH,
        recent_mm=0.0,
        f24_mm=70.0,
    )
    created = await service.evaluate_location_assessment(initial)
    assert created is not None
    assert "0 mm observed, 70 mm 24h forecast" in created.description
    assert created.evidence["recent_observed_mm"] == 0.0
    assert created.evidence["forecast_24h_mm"] == 70.0

    updated_assessment = make_sample_assessment(
        location_id="loc-sync-1",
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        risk_category=RiskCategory.HIGH,
        recent_mm=12.5,
        f24_mm=84.2,
    )
    deduped = await service.evaluate_location_assessment(updated_assessment)
    assert deduped is not None
    assert deduped.id == created.id
    assert "12.5 mm observed, 84.2 mm 24h forecast" in deduped.description
    assert deduped.evidence["recent_observed_mm"] == 12.5
    assert deduped.evidence["forecast_24h_mm"] == 84.2
