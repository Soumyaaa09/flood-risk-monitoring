# FloodWatch — Phase 2.8.1: High-Priority Data Integrity Remediation Report

**Date:** 2026-11-25  
**Reference Audit:** [phase_2_8_final_whole_project_audit_report.md](file:///c:/Users/rsoum/Desktop/FloodWatch/artifacts/phase_2_8_final_whole_project_audit_report.md)  
**Final Verdict:** **`PASS`**

---

## 1. Scope & Remediated Findings

Only the two Phase 2.8.1 data-integrity findings (`HIGH-01` and `MED-03`) were remediated. No database migrations, SQL statements, live weather ingestion runs, alert mutations, or unrelated Phase 2.8.2 items were executed.

---

### Finding A — `HIGH-01`: Historical Observation Query (`SupabaseClient.get_historical_observations_for_location`)

- **Original Audit Finding:** `get_historical_observations_for_location(location_id, days=7, limit=2000)` in [supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py) accepted `days` without applying a timestamp cutoff filter, queried `order=valid_at.asc`, and capped results at `2000`, risking omission of recent observations once a station exceeded 2,000 rows, while also silently returning `[]` on HTTP error responses.
- **Root Cause:** Missing `valid_at=gte.<cutoff>` query predicate, ascending sort prior to `limit`, and unchecked HTTP status fallback.
- **Exact Remediation Implemented ([supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py#L657-L788)):**
  - Added `MAX_HISTORICAL_OBSERVATIONS_LIMIT = 5000`, `HISTORICAL_OBSERVATION_TYPES = frozenset({"observed", "analysis", "reanalysis"})`, and `_parse_utc_timestamp()` for UTC normalization.
  - Added strict parameter validation for `days >= 1`, `limit >= 1`, `offset >= 0`, and non-empty `location_id` (raising `ValueError` on invalid inputs), while keeping backward-compatible defaults (`days=7, limit=2000`) plus optional `offset=0` and `reference_time=None`.
  - Computes UTC `cutoff_dt = ref_utc - timedelta(days=days)` and applies `&valid_at=gte.<UTC_ISO>` (`Z`-suffixed) alongside `&observation_type=in.(observed,analysis,reanalysis)`.
  - Queries `&order=valid_at.desc&limit=<effective_limit>` (plus `&offset=<offset>` when `offset > 0`) so PostgREST retains the **newest** records in the window, then performs defense-in-depth filtering (skipping invalid/missing `valid_at`, `< cutoff_dt`, or non-historical `observation_type`) and sorts results back into ascending chronological order `(valid_at, fetched_at)`.
  - Raises `SupabaseClientError` on timeouts (`504`), network errors (`503`), non-`200` HTTP status codes, and malformed JSON payloads rather than silently returning `[]` as valid empty history.
- **Before / After Behavior:**
  - *Before:* Queried `order=valid_at.asc&limit=2000` with no date filter and returned `[]` on non-200 responses.
  - *After:* Applies exact `days` UTC cutoff (`7`, `14`, `30`, etc.), prevents recent-data truncation via descending DB fetch followed by ascending chronological sort, preserves `limit`/`offset` pagination and forecast exclusion, and surfaces API/network failures via `SupabaseClientError`.

---

### Finding B — `MED-03`: Missing Observations Incorrectly Classified as `LOW` Risk (`RainfallRiskAssessor.assess_location_risk`)

- **Original Audit Finding:** `RainfallRiskAssessor.assess_location_risk()` in [rainfall_assessor.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/rainfall_assessor.py) only returned `UNAVAILABLE` when **both** `obs_val` and `f24_val` were `None`, and did not treat `QualityStatus.FAIL` as an invalid assessment state. If observed rainfall was missing or invalid (`obs_val is None` or `QualityStatus.FAIL`) while `forecast_24h_precipitation_mm == 0.0`, the engine could categorize the station as `LOW` with `HIGH` confidence.
- **Root Cause:** Missing guard for single-sided `obs_val is None` and unvalidated `risk_input.quality_status == QualityStatus.FAIL`.
- **Exact Remediation Implemented ([rainfall_assessor.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/rainfall_assessor.py#L177-L200)):**
  - After existing physics, dual-missing, freshness (`UNAVAILABLE`/`STALE`), and forecast-completeness guards, added explicit guards:
    1. `if obs_val is None:` -> returns `_build_unavailable_response(...)` (`RiskCategory.UNAVAILABLE`, `RiskConfidence.NONE`, `QualityStatus.FAIL`).
    2. `if risk_input.quality_status == QualityStatus.FAIL:` -> returns `_build_unavailable_response(...)` (`RiskCategory.UNAVAILABLE`, `RiskConfidence.NONE`, `QualityStatus.FAIL`).
  - Preserved all existing thresholds (`VERY_HIGH`, `HIGH`, `MODERATE`, `LOW`), genuine `0.0 mm` fresh `LOW` classifications, and `DELAYED` / `QualityStatus.WARNING` confidence reductions.
- **Before / After Behavior:**
  - *Before:* `obs_val=None, f24_val=0.0` or `quality_status=QualityStatus.FAIL, f24_val=0.0` fell through to `_evaluate_thresholds` and produced `RiskCategory.LOW` with `RiskConfidence.HIGH`.
  - *After:* Both conditions return `RiskCategory.UNAVAILABLE`, `RiskConfidence.NONE`, and `QualityStatus.FAIL` with transparent `RiskContextExplanation` (`data_quality_context.status == "fail"`).

---

## 2. Files Modified

| File | Purpose |
| :--- | :--- |
| [supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py) | `HIGH-01` remediation in `get_historical_observations_for_location` and `_parse_utc_timestamp`. |
| [rainfall_assessor.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/rainfall_assessor.py) | `MED-03` remediation in `assess_location_risk`. |
| [test_historical_rainfall.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_historical_rainfall.py) | Added 6 regression test cases (including 7d/14d/30d parametrization) for `HIGH-01`. |
| [test_risk_engine.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_risk_engine.py) | Added 3 regression test cases for `MED-03`. |

---

## 3. Regression Tests Added

- **Historical Query (`backend/tests/test_historical_rainfall.py`):**
  - `test_historical_query_applies_window_cutoff_and_excludes_forecasts[7|14|30]` — verifies exact UTC cutoff (`valid_at=gte.<cutoff>`), forecast exclusion, and ascending chronological sorting.
  - `test_historical_query_retains_recent_records_under_limit_and_pagination` — verifies `order=valid_at.desc` prevents ascending-order truncation of newest records under `limit`/`offset` pagination while returning chronological ascending order.
  - `test_historical_query_handles_empty_missing_timestamps_and_validation` — verifies invalid `days`/`limit`, empty responses, and missing/invalid timestamps in rows.
  - `test_historical_query_raises_on_supabase_errors_instead_of_empty_history` — verifies HTTP 500 and network timeouts raise `SupabaseClientError`.
- **Rainfall Risk Engine (`backend/tests/test_risk_engine.py`):**
  - `test_missing_observed_rainfall_with_zero_forecast_not_low`
  - `test_quality_status_fail_with_zero_metrics_not_low`
  - `test_risk_service_invalid_or_missing_observed_value_with_zero_forecast_not_low` (`obs=None` and `obs="corrupt-sensor"` with `0.0 mm` 24h forecast)

*Note: Automated unit/integration tests use mocked `httpx`/Supabase responses and do not replace live ingestion verification.*

---

## 4. Verification Results (Run After Final Code Edit)

| Check | Command | Result |
| :--- | :--- | :--- |
| **Focused Regression Suites** | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_historical_rainfall.py backend/tests/test_risk_engine.py -v` | **46 passed in 7.43s** (`0` failed, `0` skipped) |
| **Full Backend Test Suite** | `backend\.venv\Scripts\python.exe -m pytest backend/tests -q` | **259 passed in 75.28s** (`0` failed, `0` skipped) |
| **Python Compileall** | `backend\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests` | **Passed** (`exit 0`) |
| **Frontend Production Build** | `npm run build --prefix frontend` | **Passed** (`vite v5.4.21`, `2136` modules transformed in `25.35s`) |
| **Frontend Display Unit Tests** | `node --test frontend/tests/alertDisplay.test.mjs` | **4 passed, 0 failed** (`0` skipped) |
| **Database Non-Mutation Check** | Read-only `SupabaseClient.get_alerts()` / `get_ingestion_runs()` | **Confirmed zero mutations**: `risk_alerts` retains 1 `RESOLVED` `[TEST-AUDIT]` row (`dd028e7d-1771-424b-a984-3b1718f24886`); `ingestion_runs` = `0` |

---

## 5. Remaining Limitations & Findings Out of Scope

- All remaining Phase 2.8 findings (`MED-01`, `MED-02`, `MED-04`, `MED-05`, `MED-06`, `MED-07`, `LOW-01`–`LOW-04`) were intentionally left untouched in Phase 2.8.1.
- Live Supabase `observations` remains empty (`0` rows) until an authorized ingestion trigger is approved, so live runtime behavior of `get_historical_observations_for_location` with non-empty historical rows and >2,000-row windows is verified by mocked unit tests rather than live multi-month data.
- **Unresolved within scope:** None (`HIGH-01` and `MED-03` are fully remediated).
