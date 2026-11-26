# FloodWatch — Phase 2.8.2: Freshness Policy Consistency & Alert Lifecycle Reliability Report

**Date:** 2026-11-26  
**Reference Audit:** [phase_2_8_final_whole_project_audit_report.md](file:///c:/Users/rsoum/Desktop/FloodWatch/artifacts/phase_2_8_final_whole_project_audit_report.md)  
**Final Verdict:** **`PASS`**

---

## 1. Executive Summary

Phase 2.8.2 remediated **only** findings **MED-02** (Freshness Policy Consistency) and **MED-04** (Alert Recovery Lifecycle Reliability & Supabase Query Error Propagation) from the Phase 2.8 audit without altering database schemas, executing SQL, triggering live ingestion, modifying alert types/risk thresholds, or mutating the retained `[TEST-AUDIT]` alert (`dd028e7d-1771-424b-a984-3b1718f24886`).

- **MED-02:** Centralized freshness evaluation in `FreshnessEvaluator` with explicit, documented policies separating physical observation validity (`valid_at`: `120m` FRESH / `360m` DELAYED) from pipeline retrieval age (`fetched_at`: `60m` FRESH / `180m` DELAYED), eliminating contradictory ingestion-freshness classifications across `IngestionService` and `MonitoringService`.
- **MED-04:** Fixed the `RISK_REDUCED` early return that skipped resolving open `DATA_STALE` / `DATA_UNAVAILABLE` alerts, enforced strict fresh/valid/complete telemetry preconditions for LOW-risk recovery, prevented duplicate open `RISK_REDUCED` alerts and `uq_risk_alerts_active_fingerprint` `409` collisions on repeated HIGH-to-LOW transitions, and propagated Supabase HTTP/timeout/network/JSON errors through `SupabaseClient.get_alerts()`, `SupabaseClient.get_alert_by_id()`, `AlertService`, and `routes/alerts.py` (HTTP `503` on outage vs `404` on genuine missing records).
- **Verification:** **147/147** focused tests passed, **280/280** full backend tests passed (`0` skipped), `compileall` passed (`exit 0`), and `npm run build --prefix frontend` passed (`2136` modules).

---

## 2. MED-02 Root Cause & Remediation

### Root Cause
1. `IngestionService.calculate_freshness()` read `Settings.FRESHNESS_FRESH_MINUTES` (`60`) and `Settings.FRESHNESS_DELAYED_MINUTES` (`180`) to evaluate `fetched_at`.
2. `FreshnessEvaluator` in `data_quality.py` hardcoded `FRESH_MINUTES = 120.0` and `DELAYED_MINUTES = 360.0` (matching `RainfallRiskConfig` defaults), and `MonitoringService.get_location_quality_health()` called `FreshnessEvaluator.evaluate()` for **both** `valid_at` (`observation_freshness`) and `fetched_at` (`ingestion_freshness`).
3. Consequently, a `90`-minute-old `fetched_at` timestamp was classified as `DELAYED` by `IngestionService.get_latest_weather_for_location()` (`60 < 90 <= 180`) while `MonitoringService.get_location_quality_health()` classified the same `fetched_at` as `FRESH` (`90 <= 120`).
4. In addition, `IngestionService.get_latest_weather_for_location()` called raw `datetime.fromisoformat()` on `valid_at` and `fetched_at`, risking unhandled `ValueError` on malformed timestamps, and `FreshnessEvaluator.evaluate()` lacked string coercion guards if passed malformed timestamp strings directly.

### Exact Remediation
- **[config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/core/config.py#L13-L20) & [config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/config.py#L15-L60):** Defined canonical observation freshness constants (`OBSERVATION_FRESHNESS_FRESH_MINUTES = 120.0`, `OBSERVATION_FRESHNESS_DELAYED_MINUTES = 360.0`, `FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS = 60.0`) in `app.core.config`, wired `RainfallRiskConfig` defaults directly to those shared constants, and documented `Settings.FRESHNESS_FRESH_MINUTES` (`60`) / `FRESHNESS_DELAYED_MINUTES` (`180`) as governing pipeline retrieval (`fetched_at`).
- **[data_quality.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/data_quality.py#L20-L157):** Introduced `FreshnessThresholdPolicy`, `get_observation_freshness_policy()`, `get_ingestion_freshness_policy()`, `OBSERVATION_FRESHNESS_POLICY`, `_coerce_timestamp_utc()`, `FreshnessEvaluator.evaluate_observation()`, and `FreshnessEvaluator.evaluate_ingestion()`. Missing, blank, non-datetime, malformed ISO strings, and future timestamps `> 60s` ahead deterministically return `(FreshnessState.UNAVAILABLE, None)`.
- **[ingestion_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/ingestion_service.py#L296-L390):** Delegated `IngestionService.calculate_freshness()` to `FreshnessEvaluator.evaluate_ingestion(..., settings=self.settings)` and validated `valid_at` / `fetched_at` in `get_latest_weather_for_location()` via `DataQualityValidator.validate_timestamp()`.
- **[monitoring_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/monitoring_service.py#L84-L108):** Updated `MonitoringService.get_location_quality_health()` to evaluate `valid_at` with `FreshnessEvaluator.evaluate_observation(..., config=get_risk_config())` and `fetched_at` with `FreshnessEvaluator.evaluate_ingestion(..., settings=self.settings)`.
- **[risk_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/risk_service.py#L140-L150):** Updated `RiskService.build_rainfall_input_for_location()` to pass `self.config` into `FreshnessEvaluator.evaluate_observation()`.

---

## 3. Final Freshness Policy & Timestamp Semantics

FloodWatch enforces **two distinct, centralized freshness policies** corresponding to two physically different timestamps:

| Policy Profile | Timestamp | Governed Meaning | `FRESH` | `DELAYED` | `STALE` | `UNAVAILABLE` | Consumers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Observation Freshness** | `analysis.valid_at` | Time the meteorological measurement/nowcast applies physically | `age <= 120.0m` | `120.0m < age <= 360.0m` | `age > 360.0m` | Missing, malformed, or `> 60s` future | `RiskService`, `MonitoringService.observation_freshness` (& overall station `freshness`) |
| **Ingestion Freshness** | `analysis.fetched_at` | Time FloodWatch retrieved the payload from Open-Meteo | `age <= 60.0m` | `60.0m < age <= 180.0m` | `age > 180.0m` | Missing, malformed, or `> 60s` future | `IngestionService`, `MonitoringService.ingestion_freshness` |

- **Clock Skew Tolerance:** Both policies treat slight clock skew (`0 < future_delta <= 60s`) as `0.0m` (`FRESH`) and reject `> 60s` future timestamps as `UNAVAILABLE`.
- **Rationale & Documented Ambiguity:**
  - `IngestionService` aligns `fetched_at` (`60m / 180m`) with the hourly scheduler interval (`INGESTION_SCHEDULE_INTERVAL_MINUTES=60`: 1 cycle fresh, up to 3 cycles delayed), while `RiskService` and `RainfallRiskConfig` evaluate physical `valid_at` telemetry (`120m / 360m`: >360m triggers `STALE` refusal in `RainfallRiskAssessor`).
  - `Settings.FRESHNESS_STALE_MINUTES = 360` exists in `core/config.py` alongside `FRESHNESS_DELAYED_MINUTES = 180`, leaving an unused numeric gap between `180` and `360` (ages `> 180m` are `STALE` in `test_ingestion.py` `300m -> STALE`). Consistent with audit rules, existing `60 / 180` ingestion thresholds and `120 / 360` observation thresholds were preserved rather than altered silently; `Settings.FRESHNESS_STALE_MINUTES` is documented as a legacy setting and recommended for formal deprecation or alignment in a future config cleanup.

---

## 4. MED-04 Root Causes & Remediations (Subproblems A, B, C)

### Subproblem A: Recovery Lifecycle Early Return
- **Root Cause:** In `AlertService._process_assessment_internal()` (`RiskCategory.LOW`), `if prior_risk_alerts:` executed `return await self._create_alert_record(...)` (`RISK_REDUCED`) before lines resolving `DATA_UNAVAILABLE` and `DATA_STALE` alerts. Also, `Case C` lacked an explicit guard against non-fresh, failed-quality, missing-observation, or incomplete-forecast assessments passed directly to `AlertService`.
- **Remediation ([alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py#L517-L563)):**
  - Added `has_valid_recovery_telemetry` guard requiring `observation_freshness == FRESH`, `quality_status != FAIL`, `recent_observed_rainfall_mm is not None`, `forecast_completeness_pct >= 50.0%`, and `active_forecast_hours >= 6h`; delayed/stale/missing/incomplete telemetry returns `None` without resolving open alerts.
  - Resolved all open `DATA_UNAVAILABLE` and `DATA_STALE` alerts for the location **before** resolving `prior_risk_alerts` and emitting/updating `RISK_REDUCED`. Other locations and unrelated alerts are unaffected, and repeated clean `LOW` cycles remain idempotent (`None`).

### Subproblem B: Repeated `RISK_REDUCED` Fingerprint Conflicts
- **Root Cause:** Migration 005 index `uq_risk_alerts_active_fingerprint` allows at most one unresolved (`NEW`, `ACTIVE`, `ACKNOWLEDGED`) alert per `(location_id, fingerprint)`. Previously, `Case C` unconditionally called `_create_alert_record` for `RISK_REDUCED`, which fails with HTTP `409` (or duplicates in memory) if a previous `RISK_REDUCED` alert is still open when another `HIGH -> LOW` transition occurs.
- **Remediation ([alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py#L565-L619)):**
  - Checks `open_alerts` for an existing open `RISK_REDUCED` alert (`fingerprint == fp` or `alert_type == "RISK_REDUCED"`). If present, calls `_touch_alert()` to update its timestamp, synchronized narrative `description`, and full `evidence` without violating `uq_risk_alerts_active_fingerprint`, creating duplicates, or modifying historical `RESOLVED` alerts.
  - If all prior `RISK_REDUCED` alerts are `RESOLVED`, inserts a new `RISK_REDUCED` record normally.
  - On `SupabaseClientError(status_code=409)` from concurrent creation races, re-queries `_get_open_alerts_for_location()` and touches the open `RISK_REDUCED` row if found; if no matching open row exists or for any non-`409` error, propagates `SupabaseClientError`.

### Subproblem C: Silent Swallowing of Database Errors in Alert Reads
- **Root Cause:** `SupabaseClient.get_alerts()` and `SupabaseClient.get_alert_by_id()` caught all exceptions and returned `[]` / `None` on non-200 HTTP responses, timeouts, and network errors; `AlertService.get_alerts()` used `db_records or list(self._memory_alerts.values())` and caught DB exceptions in non-production even when Supabase was configured; and `routes/alerts.py` (`list_alerts`, `get_alerts_summary`) lacked `SupabaseClientError` handlers returning HTTP `503`.
- **Remediation:**
  - **[supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py#L833-L917):** Extracted `_execute_alerts_get()` used by both `get_alerts()` and `get_alert_by_id()`. Valid HTTP `200 []` returns `[]` / `None` (`404` on `/alerts/{id}`), while missing API keys (`500`), non-`200` HTTP (`401`/`403`/`5xx`), timeouts (`504`), network errors (`503`), and malformed/non-list JSON (`502`) raise sanitized `SupabaseClientError`. Unconfigured `base_url == ""` still returns `[]` / `None` for offline unit tests.
  - **[alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py#L76-L176):** `AlertService.get_alerts()` and `get_alert_by_id()` unconditionally re-raise `SupabaseClientError` and restrict `_memory_alerts` fallback strictly to unconfigured non-production (`self._allow_memory_fallback and not self.supabase.base_url`).
  - **[alerts.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/alerts.py#L66-L108):** Added explicit `except SupabaseClientError` handlers returning sanitized `HTTP 503` (`database_error`) on `list_alerts` and `get_alerts_summary`, matching `get_alert`, `acknowledge_alert`, and `resolve_alert`.

---

## 5. Files Created and Modified

- **Modified Source Files:**
  - [config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/core/config.py)
  - [config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/config.py)
  - [data_quality.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/data_quality.py)
  - [ingestion_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/ingestion_service.py)
  - [monitoring_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/monitoring_service.py)
  - [risk_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/risk_service.py)
  - [supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py)
  - [alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py)
  - [alerts.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/alerts.py)
- **Modified Test Files:**
  - [test_monitoring.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_monitoring.py) (`+14` test cases)
  - [test_ingestion.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_ingestion.py) (`+1` test function + boundary checks)
  - [test_alerts.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_alerts.py) (`+6` test functions)
- **Created Report:**
  - [phase_2_8_2_freshness_and_alert_lifecycle_remediation_report.md](file:///c:/Users/rsoum/Desktop/FloodWatch/artifacts/phase_2_8_2_freshness_and_alert_lifecycle_remediation_report.md)

---

## 6. Tests Added & Verification Results (Run After Final Edit)

### Focused Tests (`backend/tests/test_monitoring.py`, `test_ingestion.py`, `test_alerts.py`)
- **Result:** **147 passed in 21.50s** (`0` failed, `0` skipped)
  - `test_centralized_freshness_policy_boundaries[...]` (`12` parametrized cases: `59.9`, `60.0`, `60.1`, `119.9`, `120.0`, `120.1`, `179.9`, `180.0`, `180.1`, `359.9`, `360.0`, `360.1`)
  - `test_freshness_evaluator_rejects_malformed_missing_and_future_timestamps`
  - `test_freshness_consistency_across_ingestion_monitoring_and_risk_services`
  - `test_latest_weather_handles_malformed_timestamps_without_crashing` + boundary assertions in `test_freshness_calculation_states`
  - `test_low_recovery_resolves_stale_and_unavailable_alerts_alongside_risk_reduction`
  - `test_low_recovery_blocked_when_telemetry_delayed_stale_missing_or_incomplete`
  - `test_repeated_high_to_low_transitions_deduplicate_open_and_allow_after_resolved`
  - `test_risk_reduced_409_conflict_recovery_and_non_409_propagation`
  - `test_supabase_get_alerts_and_by_id_distinguish_empty_not_found_and_failures`
  - `test_alert_api_endpoints_propagate_db_failures_as_503_and_preserve_404`

### Complete Backend, Compile & Frontend Verification
| Check | Command | Result |
| :--- | :--- | :--- |
| **Full Backend Suite** | `backend\.venv\Scripts\python.exe -m pytest backend/tests -q` | **280 passed in 29.60s** (`0` failed, `0` skipped) |
| **Python Compileall** | `backend\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests` | **Passed** (`exit 0`) |
| **Frontend Build** | `npm run build --prefix frontend` | **Passed** (`vite v5.4.21`, `2136` modules transformed in `8.31s`; no frontend files were changed) |
| **Frontend Unit Tests** | `node --test frontend/tests/alertDisplay.test.mjs` | **4 passed, 0 failed** (`0` skipped; ran after zero frontend edits) |

---

## 7. Regression & Compatibility Assessment

- Existing REST API JSON schemas (`LocationLatestWeather`, `LocationQualityHealth`, `RainfallRiskAssessmentResponse`, `AlertResponse`, `AlertsListResponse`, `AlertSummaryResponse`) remain unchanged.
- Existing `test_monitoring.py`, `test_ingestion.py`, `test_risk_engine.py`, `test_public_dashboard.py`, and `test_alerts.py` contracts remain 100% compatible.
- `SupabaseClient.get_alerts()` / `get_alert_by_id()` now raise `SupabaseClientError` on infrastructure errors when `base_url` is set instead of masquerading as empty results; unconfigured unit tests (`SUPABASE_URL=""`) continue using `_memory_alerts`.

---

## 8. Safety Confirmation

- **Database migrations / SQL executed:** None.
- **Live ingestion triggered:** None (`ingestion_runs` = `0` verified read-only).
- **Live alerts created/acknowledged/resolved/deleted:** None. Read-only check confirmed `risk_alerts` retains only the permanent `RESOLVED` `[TEST-AUDIT]` record (`dd028e7d-1771-424b-a984-3b1718f24886`).

---

## 9. Remaining Limitations

- Automated tests covering Supabase HTTP responses, `RISK_REDUCED` `409` races, and cross-service freshness checks use mocked `httpx`/Supabase responses and synthetic timestamps rather than live ingestion data (`observations` in live Supabase remains empty until ingestion is authorized).
- `Settings.FRESHNESS_STALE_MINUTES` (`360`) remains in `core/config.py` for configuration backward compatibility even though ingestion retrieval (`fetched_at`) transitions from `DELAYED` (`180m`) directly to `STALE`.
- Other Phase 2.8 audit items (`MED-01`, `MED-05`, `MED-06`, `MED-07`, `LOW-01`–`LOW-04`) remain intentionally out of scope for Phase 2.8.2.
