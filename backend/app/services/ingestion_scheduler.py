"""Automated Weather Data Ingestion Scheduler for FloodWatch (Phase 2.6).

Architectural Safety & Operational Principles:
1. In-Process Concurrency Protection:
   Uses an asyncio.Lock to guarantee that scheduled ingestion cycles NEVER overlap within
   the running process. If a cycle is already executing when a new trigger arrives, the
   overlapping execution is safely skipped and logged.
2. Graceful Shutdown & Cancellation:
   The background loop listens to an asyncio.Event stop signal and handles asyncio.CancelledError
   cleanly, ensuring pending tasks terminate without corrupting database states or leaving
   phantom runs.
3. Exception Isolation:
   All cycle exceptions are caught, sanitized, and logged. A failure in an ingestion cycle
   will NEVER crash the background loop or the FastAPI application.
4. Location Fault Isolation:
   Delegates directly to IngestionService.trigger_ingestion(), ensuring that if one pilot
   location fails, remaining locations continue processing without interruption.
5. Zero Secret Leakage:
   Scheduler logs record timestamps, durations, counts, and sanitized errors. Secrets,
   tokens, and database credentials are never logged or exposed.
6. Single-Process Limitation Notice:
   This in-process scheduler is designed strictly for a single application worker process.
   Running under multi-worker process managers (e.g. uvicorn --workers > 1) or distributed
   container replicas will result in duplicated ingestion cycles. Multi-worker deployments
   REQUIRE an external orchestrator (e.g., cron, AWS EventBridge, Celery) or a distributed
   lock (e.g., PostgreSQL advisory locks / Redis lock).
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

from app.core.config import Settings, detect_configured_workers, get_settings
from app.core.sanitizer import sanitize_error_message
from app.schemas.monitoring import SchedulerStatusResponse
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)


class IngestionScheduler:
    """Asynchronous background scheduler for periodic weather data ingestion."""

    def __init__(
        self,
        ingestion_service: Optional[IngestionService] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.ingestion_service = ingestion_service or IngestionService()

        # Configuration validation
        self.enabled: bool = bool(self.settings.ENABLE_INGESTION_SCHEDULER)
        self.interval_minutes: int = int(self.settings.INGESTION_SCHEDULE_INTERVAL_MINUTES)
        if self.interval_minutes < 1:
            raise ValueError(
                f"Invalid INGESTION_SCHEDULE_INTERVAL_MINUTES: {self.interval_minutes}. "
                "Schedule interval must be a positive integer >= 1."
            )

        # Operational state & concurrency lock
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # Telemetry metrics
        self.last_cycle_start: Optional[datetime] = None
        self.last_cycle_completed: Optional[datetime] = None
        self.last_cycle_outcome: Optional[str] = None  # "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "SKIPPED"
        self.last_cycle_duration_seconds: Optional[float] = None
        self.next_expected_cycle: Optional[datetime] = None
        self.total_cycles_executed: int = 0
        self.location_outcomes: List[Dict[str, Any]] = []

    @property
    def is_running(self) -> bool:
        """Return True if background scheduling loop is actively running."""
        return self._task is not None and not self._task.done()

    @property
    def is_cycle_in_progress(self) -> bool:
        """Return True if an ingestion cycle is currently actively executing."""
        return self._lock.locked()

    def get_status(self) -> SchedulerStatusResponse:
        """Produce operational status telemetry for monitoring endpoints."""
        if not self.enabled:
            state = "disabled"
        elif self.is_cycle_in_progress:
            state = "running"
        elif self.is_running:
            state = "idle"
        else:
            state = "stopped"

        return SchedulerStatusResponse(
            enabled=self.enabled,
            interval_minutes=self.interval_minutes,
            state=state,
            is_cycle_in_progress=self.is_cycle_in_progress,
            last_cycle_start=self.last_cycle_start,
            last_cycle_completed=self.last_cycle_completed,
            last_cycle_outcome=self.last_cycle_outcome,
            last_cycle_duration_seconds=self.last_cycle_duration_seconds,
            next_expected_cycle=self.next_expected_cycle if self.is_running else None,
            total_cycles_executed=self.total_cycles_executed,
            location_outcomes=self.location_outcomes,
        )

    async def run_cycle(self) -> Dict[str, Any]:
        """Execute a single ingestion cycle across all pilot locations with overlap protection."""
        if self._lock.locked():
            logger.warning(
                "Scheduled ingestion cycle triggered while previous cycle is still in progress; skipping trigger."
            )
            return {
                "status": "SKIPPED",
                "reason": "Previous cycle still in progress",
            }

        async with self._lock:
            cycle_start = datetime.now(timezone.utc)
            self.last_cycle_start = cycle_start
            logger.info("Starting automated weather ingestion cycle for pilot locations (Paradeep, Cuttack, Kendrapara)...")

            outcome = "FAILED"
            location_summaries: List[Dict[str, Any]] = []

            try:
                # Direct internal invocation of IngestionService
                resp = await self.ingestion_service.trigger_ingestion()

                # Map location results
                for loc in resp.results:
                    location_summaries.append({
                        "location_id": loc.location_id,
                        "location_name": loc.location_name,
                        "district_name": loc.district_name,
                        "status": loc.status.value,
                        "records_fetched": loc.records_fetched,
                        "records_inserted": loc.records_inserted,
                        "records_updated": loc.records_updated,
                        "error_message": loc.error_message,
                    })

                # Classify overall cycle outcome
                if resp.failed_locations == 0 and resp.successful_locations > 0:
                    outcome = "SUCCESS"
                elif resp.successful_locations > 0 and resp.failed_locations > 0:
                    outcome = "PARTIAL_SUCCESS"
                else:
                    outcome = "FAILED"

                self.location_outcomes = location_summaries

                # Trigger alert evaluation for successful pilot locations (Phase 2.7.2)
                try:
                    from app.services.alert_service import get_alert_service

                    alert_svc = get_alert_service()
                    await alert_svc.evaluate_post_ingestion(resp.results)
                except Exception as eval_exc:
                    logger.warning("Pipeline alert evaluation error: %s", eval_exc.__class__.__name__)

            except Exception as exc:
                clean_err = sanitize_error_message(f"{exc.__class__.__name__}: {str(exc)}")
                logger.error("Automated weather ingestion cycle failed unexpectedly: %s", clean_err)
                outcome = "FAILED"
                self.location_outcomes = [{"error": clean_err}]

            finally:
                cycle_end = datetime.now(timezone.utc)
                duration = round((cycle_end - cycle_start).total_seconds(), 2)

                self.last_cycle_completed = cycle_end
                self.last_cycle_outcome = outcome
                self.last_cycle_duration_seconds = duration
                self.total_cycles_executed += 1
                self.next_expected_cycle = cycle_end + timedelta(minutes=self.interval_minutes)

                logger.info(
                    "Automated weather ingestion cycle finished in %.2fs with outcome: %s (total executed: %d).",
                    duration,
                    outcome,
                    self.total_cycles_executed,
                )

            return {
                "outcome": outcome,
                "duration_seconds": duration,
                "locations": self.location_outcomes,
            }

    async def _schedule_loop(self) -> None:
        """Internal background loop running ingestion periodically while application is alive."""
        logger.info(
            "Ingestion scheduler background loop started (interval: %d minutes).",
            self.interval_minutes,
        )
        try:
            while not self._stop_event.is_set():
                # Execute cycle
                await self.run_cycle()

                # Calculate next run interval
                interval_seconds = self.interval_minutes * 60

                # Responsive wait on stop event
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
                    # If wait_for returns without TimeoutError, stop_event was signaled!
                    break
                except asyncio.TimeoutError:
                    # Timeout reached, proceed to next cycle
                    continue

        except asyncio.CancelledError:
            logger.info("Ingestion scheduler background task received cancellation.")
            raise
        except Exception as exc:
            logger.critical("Unexpected fatal error in ingestion scheduler loop: %s", exc.__class__.__name__)
        finally:
            logger.info("Ingestion scheduler background loop terminated.")

    def start(self) -> None:
        """Start the background scheduler task if enabled by configuration."""
        if not self.enabled:
            logger.info(
                "Ingestion scheduler is disabled by configuration (ENABLE_INGESTION_SCHEDULER=false). "
                "Background task not started."
            )
            return

        # Fail-safe check (SCHED-01): verify single-process safety before spawning task
        configured_workers = max(self.settings.WORKERS, detect_configured_workers(self.settings))
        if configured_workers > 1:
            raise RuntimeError(
                f"Scheduler activation rejected: multiple worker processes detected ({configured_workers} workers). "
                "The in-process scheduler requires a single process (workers=1). For multi-worker deployments, "
                "keep ENABLE_INGESTION_SCHEDULER=false and trigger ingestion externally via /api/v1/ingestion/trigger "
                "or use distributed coordination."
            )

        if self.is_running:
            logger.warning("Ingestion scheduler task is already running.")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._schedule_loop(), name="floodwatch_ingestion_scheduler")
        logger.info("Ingestion scheduler task spawned successfully.")

    async def stop(self) -> None:
        """Gracefully stop and cancel the background scheduler task."""
        if not self._task:
            return

        logger.info("Stopping ingestion scheduler task gracefully...")
        self._stop_event.set()
        self._task.cancel()

        try:
            await asyncio.gather(self._task, return_exceptions=True)
        except Exception as exc:
            logger.warning("Exception during scheduler task cancellation: %s", exc.__class__.__name__)
        finally:
            self._task = None
            logger.info("Ingestion scheduler task stopped cleanly.")


# Singleton instance container
_scheduler_instance: Optional[IngestionScheduler] = None


def get_ingestion_scheduler() -> IngestionScheduler:
    """Return application-wide singleton instance of IngestionScheduler."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = IngestionScheduler()
    return _scheduler_instance


def reset_ingestion_scheduler() -> None:
    """Reset scheduler singleton (used primarily in test suites)."""
    global _scheduler_instance
    if _scheduler_instance and _scheduler_instance.is_running:
        _scheduler_instance._task.cancel()
    _scheduler_instance = None
