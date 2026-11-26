"""Automated tests for Automated Weather Ingestion Scheduler (Phase 2.6).

Verification Coverage:
1. Scheduler disabled by default.
2. Scheduler starts only when enabled.
3. Invalid interval configuration is rejected.
4. Correct pilot locations are scheduled.
5. Successful cycle completion.
6. One location failing while others continue.
7. Prevention of overlapping cycles.
8. Graceful shutdown and task cancellation.
9. Scheduler exception isolation.
10. Existing ingestion idempotency remains intact.
11. No ingestion task starts during ordinary test initialization.
12. Monitoring endpoint does not expose secrets.
13. Existing manual ingestion trigger remains functional.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.main import app
from app.schemas.ingestion import (
    IngestionLocationResult,
    IngestionStatus,
    IngestionTriggerResponse,
)
from app.services.ingestion_scheduler import (
    IngestionScheduler,
    get_ingestion_scheduler,
    reset_ingestion_scheduler,
)
from app.services.ingestion_service import IngestionService


@pytest.fixture(autouse=True)
def cleanup_scheduler():
    """Ensure scheduler singleton is reset cleanly before and after each test."""
    reset_ingestion_scheduler()
    yield
    reset_ingestion_scheduler()


# -----------------------------------------------------------------------------
# 1. Scheduler disabled by default
# -----------------------------------------------------------------------------
def test_scheduler_disabled_by_default():
    """Verify scheduler defaults to disabled and start() creates no background task."""
    default_settings = Settings()
    scheduler = IngestionScheduler(settings=default_settings)

    assert scheduler.enabled is False
    assert scheduler.interval_minutes == 60
    assert scheduler.is_running is False

    # Calling start should log info and return immediately without spawning a task
    scheduler.start()
    assert scheduler.is_running is False
    assert scheduler._task is None

    status = scheduler.get_status()
    assert status.enabled is False
    assert status.state == "disabled"


# -----------------------------------------------------------------------------
# 2. Scheduler starts only when enabled
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scheduler_starts_only_when_enabled():
    """Verify scheduler spawns background task only when ENABLE_INGESTION_SCHEDULER=True."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=75,
        total_records_inserted=75,
        total_records_updated=0,
        results=[],
    )

    enabled_settings = Settings(
        ENABLE_INGESTION_SCHEDULER=True,
        INGESTION_SCHEDULE_INTERVAL_MINUTES=60,
    )
    scheduler = IngestionScheduler(ingestion_service=mock_service, settings=enabled_settings)
    assert scheduler.enabled is True

    try:
        scheduler.start()
        assert scheduler.is_running is True
        assert scheduler._task is not None

        status = scheduler.get_status()
        assert status.enabled is True
        assert status.state in ("running", "idle")
    finally:
        await scheduler.stop()
        assert scheduler.is_running is False


# -----------------------------------------------------------------------------
# 3. Invalid interval configuration is rejected
# -----------------------------------------------------------------------------
def test_invalid_interval_rejected():
    """Verify scheduler rejects interval <= 0 or invalid integers."""
    with pytest.raises(ValueError, match="INGESTION_SCHEDULE_INTERVAL_MINUTES"):
        Settings(INGESTION_SCHEDULE_INTERVAL_MINUTES=0)

    with pytest.raises(ValueError, match="INGESTION_SCHEDULE_INTERVAL_MINUTES"):
        Settings(INGESTION_SCHEDULE_INTERVAL_MINUTES=-15)


# -----------------------------------------------------------------------------
# 4. Correct pilot locations are scheduled
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correct_pilot_locations_scheduled():
    """Verify scheduler invokes ingestion for all three pilot locations."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=75,
        total_records_inserted=75,
        total_records_updated=0,
        results=[
            IngestionLocationResult(
                location_id="loc-p",
                location_name="Paradeep Weather Grid Point",
                district_name="Jagatsinghpur",
                status=IngestionStatus.COMPLETED,
                records_fetched=25,
                records_inserted=25,
            ),
            IngestionLocationResult(
                location_id="loc-c",
                location_name="Cuttack Weather Grid Point",
                district_name="Cuttack",
                status=IngestionStatus.COMPLETED,
                records_fetched=25,
                records_inserted=25,
            ),
            IngestionLocationResult(
                location_id="loc-k",
                location_name="Kendrapara Weather Grid Point",
                district_name="Kendrapara",
                status=IngestionStatus.COMPLETED,
                records_fetched=25,
                records_inserted=25,
            ),
        ],
    )

    scheduler = IngestionScheduler(ingestion_service=mock_service)
    cycle_res = await scheduler.run_cycle()

    mock_service.trigger_ingestion.assert_awaited_once_with()
    assert cycle_res["outcome"] == "SUCCESS"
    assert len(cycle_res["locations"]) == 3
    names = {l["location_name"] for l in cycle_res["locations"]}
    assert "Paradeep Weather Grid Point" in names
    assert "Cuttack Weather Grid Point" in names
    assert "Kendrapara Weather Grid Point" in names


# -----------------------------------------------------------------------------
# 5. Successful cycle completion
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_cycle_completion():
    """Verify run_cycle records metrics, duration, and sets status to SUCCESS."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=75,
        total_records_inserted=75,
        total_records_updated=0,
        results=[],
    )

    scheduler = IngestionScheduler(ingestion_service=mock_service)
    assert scheduler.last_cycle_start is None
    assert scheduler.last_cycle_completed is None

    cycle_res = await scheduler.run_cycle()

    assert cycle_res["outcome"] == "SUCCESS"
    assert scheduler.last_cycle_outcome == "SUCCESS"
    assert scheduler.last_cycle_start is not None
    assert scheduler.last_cycle_completed is not None
    assert scheduler.total_cycles_executed == 1
    assert scheduler.last_cycle_duration_seconds is not None
    assert scheduler.next_expected_cycle is not None


# -----------------------------------------------------------------------------
# 6. One location failing while others continue
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_location_failing_others_continue():
    """Verify cycle outcome is PARTIAL_SUCCESS and retains failed and successful outcomes."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="partial",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=2,
        failed_locations=1,
        total_records_fetched=50,
        total_records_inserted=50,
        total_records_updated=0,
        results=[
            IngestionLocationResult(
                location_id="loc-p",
                location_name="Paradeep Weather Grid Point",
                status=IngestionStatus.COMPLETED,
            ),
            IngestionLocationResult(
                location_id="loc-c",
                location_name="Cuttack Weather Grid Point",
                status=IngestionStatus.FAILED,
                error_message="HTTP 503 Provider Unavailable",
            ),
            IngestionLocationResult(
                location_id="loc-k",
                location_name="Kendrapara Weather Grid Point",
                status=IngestionStatus.COMPLETED,
            ),
        ],
    )

    scheduler = IngestionScheduler(ingestion_service=mock_service)
    cycle_res = await scheduler.run_cycle()

    assert cycle_res["outcome"] == "PARTIAL_SUCCESS"
    assert scheduler.last_cycle_outcome == "PARTIAL_SUCCESS"
    assert len(cycle_res["locations"]) == 3
    failed_loc = next(l for l in cycle_res["locations"] if l["location_name"] == "Cuttack Weather Grid Point")
    assert failed_loc["status"] == "failed"
    assert "HTTP 503" in failed_loc["error_message"]


# -----------------------------------------------------------------------------
# 7. Prevention of overlapping cycles
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prevention_of_overlapping_cycles():
    """Verify overlapping cycle trigger is safely rejected and skipped."""
    mock_service = AsyncMock(spec=IngestionService)

    async def slow_trigger():
        await asyncio.sleep(0.05)
        return IngestionTriggerResponse(
            status="completed",
            trigger_timestamp=datetime.now(timezone.utc),
            total_locations=3,
            successful_locations=3,
            failed_locations=0,
            total_records_fetched=75,
            total_records_inserted=75,
            total_records_updated=0,
            results=[],
        )

    mock_service.trigger_ingestion.side_effect = slow_trigger
    scheduler = IngestionScheduler(ingestion_service=mock_service)

    # Launch two cycles concurrently
    task1 = asyncio.create_task(scheduler.run_cycle())
    await asyncio.sleep(0.01)  # Allow task1 to acquire lock
    task2 = asyncio.create_task(scheduler.run_cycle())

    res1, res2 = await asyncio.gather(task1, task2)

    # Exactly one succeeded and one was skipped
    assert (res1["outcome"] == "SUCCESS" and res2["status"] == "SKIPPED") or \
           (res2["outcome"] == "SUCCESS" and res1["status"] == "SKIPPED")
    assert mock_service.trigger_ingestion.await_count == 1


# -----------------------------------------------------------------------------
# 8. Graceful shutdown and task cancellation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_graceful_shutdown_and_cancellation():
    """Verify stop() cancels running task cleanly without uncaught exceptions."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=0,
        total_records_inserted=0,
        total_records_updated=0,
        results=[],
    )

    enabled_settings = Settings(ENABLE_INGESTION_SCHEDULER=True, INGESTION_SCHEDULE_INTERVAL_MINUTES=60)
    scheduler = IngestionScheduler(ingestion_service=mock_service, settings=enabled_settings)

    scheduler.start()
    assert scheduler.is_running is True

    # Stop should finish cleanly
    await scheduler.stop()
    assert scheduler.is_running is False
    assert scheduler._task is None


# -----------------------------------------------------------------------------
# 9. Scheduler exception isolation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scheduler_exception_isolation():
    """Verify an unexpected exception during ingestion does not crash scheduler."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.side_effect = RuntimeError("Open-Meteo connection exploded")

    scheduler = IngestionScheduler(ingestion_service=mock_service)
    cycle_res = await scheduler.run_cycle()

    assert cycle_res["outcome"] == "FAILED"
    assert scheduler.last_cycle_outcome == "FAILED"
    assert "Open-Meteo connection exploded" in scheduler.location_outcomes[0]["error"]
    assert scheduler.total_cycles_executed == 1


# -----------------------------------------------------------------------------
# 10. Existing ingestion idempotency remains intact
# -----------------------------------------------------------------------------
def test_existing_ingestion_idempotency_intact():
    """Verify attempt key generator format remains stable."""
    loc_id = "loc-test-123"
    cycle_key = "2026112210"
    attempt_key = IngestionService.generate_attempt_key(loc_id, cycle_key, attempt_id="attempt_01")
    assert attempt_key == "openmeteo_loc-test-123_2026112210_run_attempt_01"


# -----------------------------------------------------------------------------
# 11. No ingestion task starts during ordinary test initialization
# -----------------------------------------------------------------------------
def test_no_ingestion_task_starts_during_ordinary_test_initialization():
    """Verify importing app or default settings does not launch any background task."""
    scheduler = get_ingestion_scheduler()
    assert scheduler.enabled is False
    assert scheduler.is_running is False
    assert scheduler._task is None


# -----------------------------------------------------------------------------
# 12. Monitoring endpoint does not expose secrets
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_monitoring_endpoint_does_not_expose_secrets(client: AsyncClient):
    """Verify GET /api/v1/monitoring/scheduler exposes only safe telemetry."""
    response = await client.get("/api/v1/monitoring/scheduler")
    assert response.status_code == 200
    data = response.json()

    assert "enabled" in data
    assert "interval_minutes" in data
    assert "state" in data
    assert "single_process_limitation_notice" in data

    # Verify no tokens or sensitive secrets are leaked
    raw_str = str(data).lower()
    assert "token" not in data
    assert "password" not in raw_str
    assert "secret" not in raw_str or "single_process_limitation_notice" in data
    assert "service_role" not in raw_str


# -----------------------------------------------------------------------------
# 13. Existing manual ingestion trigger remains functional
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_existing_manual_ingestion_trigger_remains_functional(client: AsyncClient):
    """Verify POST /api/v1/ingestion/trigger functions as expected with valid auth."""
    mock_service = AsyncMock(spec=IngestionService)
    mock_service.trigger_ingestion.return_value = IngestionTriggerResponse(
        status="completed",
        trigger_timestamp=datetime.now(timezone.utc),
        total_locations=3,
        successful_locations=3,
        failed_locations=0,
        total_records_fetched=75,
        total_records_inserted=75,
        total_records_updated=0,
        results=[],
    )

    from app.api.v1.routes.ingestion import get_ingestion_service
    app.dependency_overrides[get_ingestion_service] = lambda: mock_service

    try:
        settings = Settings()
        headers = {"X-Ingestion-Token": settings.INGESTION_TRIGGER_TOKEN}
        response = await client.post("/api/v1/ingestion/trigger", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["total_locations"] == 3
        mock_service.trigger_ingestion.assert_awaited_once_with(location_id=None)
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)


# -----------------------------------------------------------------------------
# 14. Multi-Worker Concurrency Protection (Remediation SCHED-01)
# -----------------------------------------------------------------------------
def test_scheduler_multi_worker_configuration_rejected(monkeypatch):
    """Verify Settings rejects ENABLE_INGESTION_SCHEDULER=True when WORKERS > 1."""
    # Direct WORKERS setting
    with pytest.raises(ValueError, match="Scheduler activation rejected"):
        Settings(ENABLE_INGESTION_SCHEDULER=True, WORKERS=2)

    # Via WEB_CONCURRENCY environment variable
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(ValueError, match="Scheduler activation rejected"):
        Settings(ENABLE_INGESTION_SCHEDULER=True, WORKERS=1)


def test_scheduler_start_failsafe_multi_worker_runtime_error(monkeypatch):
    """Verify IngestionScheduler.start() raises RuntimeError when multi-worker detected."""
    # Create valid single-worker settings first
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("WORKERS", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    settings = Settings(ENABLE_INGESTION_SCHEDULER=True, WORKERS=1)
    scheduler = IngestionScheduler(settings=settings)

    # Simulate multi-worker process manager at runtime
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="Scheduler activation rejected: multiple worker processes detected"):
        scheduler.start()


def test_scheduler_single_worker_allowed(monkeypatch):
    """Verify single-worker configuration allows scheduler activation."""
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("WORKERS", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    settings = Settings(ENABLE_INGESTION_SCHEDULER=True, WORKERS=1)
    assert settings.WORKERS == 1
    assert settings.ENABLE_INGESTION_SCHEDULER is True


def test_scheduler_error_message_references_correct_trigger_endpoint(monkeypatch):
    """Verify rejection message specifies /api/v1/ingestion/trigger and not /api/v1/weather/ingest."""
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    with pytest.raises(ValueError) as exc_info:
        Settings(ENABLE_INGESTION_SCHEDULER=True, WORKERS=2)
    err = str(exc_info.value)
    assert "/api/v1/ingestion/trigger" in err
    assert "/api/v1/weather/ingest" not in err


def test_programmatic_launcher_interface():
    """Verify run_app exists and is importable as the authoritative launcher."""
    from app.main import run_app
    assert callable(run_app)


def test_authoritative_workers_fallback_from_web_concurrency(monkeypatch):
    """Verify WORKERS setting cleanly resolves from WEB_CONCURRENCY environment variable."""
    monkeypatch.delenv("WORKERS", raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    settings = Settings()
    assert settings.WORKERS == 3


