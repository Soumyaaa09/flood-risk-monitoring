# FloodWatch — Phase 2.8.3: Performance & Reliability Optimization Report

**Date:** 2026-11-26  
**Target Finding:** `MED-01` — HTTPX Client Lifecycle, Sequential Supabase Requests, and API Latency (`Phase 2.8 Final Whole-Project Audit`)  
**Verdict:** **PASS**

---

## 1. Executive Summary

Phase 2.8.3 investigated and resolved `MED-01` across the FastAPI backend (`SupabaseClient`, `OpenMeteoWeatherConnector`, database health probing, `RiskService`, and `MonitoringService`).

Key engineering improvements delivered without altering database schemas, alert lifecycle rules, hydrological risk thresholds, or public/admin API contracts:

1. **Pooled HTTPX Client Lifecycle (`backend/app/core/http_clients.py`, `backend/app/main.py`):**
   - Replaced all per-request `async with httpx.AsyncClient(...)` allocations across 18 backend call sites (`15+` in `SupabaseClient`, `2` in `OpenMeteoWeatherConnector`, and `1` in `check_database_connectivity`) with separate long-lived pooled `httpx.AsyncClient` instances for Supabase (`max_connections=20`, `max_keepalive_connections=10`, `keepalive_expiry=30.0s`) and Open-Meteo (`max_connections=10`, `max_keepalive_connections=5`, `keepalive_expiry=30.0s`).
   - Initialized and cleanly closed pools inside FastAPI `lifespan(app)` (`try ... yield ... finally`) with event-loop scoping (`WeakKeyDictionary`) so ASGI workers share persistent TLS keep-alive pools while pytest loops stay isolated and injection-friendly (`http_client` parameter).

2. **Query Concurrency & Redundant-Fetch Elimination (`supabase_client.py`, `risk_service.py`, `monitoring_service.py`):**
   - `SupabaseClient.get_latest_observations_for_location()` now executes `analysis` and `forecasts` queries concurrently (`asyncio.gather`) and propagates HTTP/timeout/network failures via `SupabaseClientError`.
   - `RiskService` now runs `get_latest_observations_for_location()` and `get_historical_observations_for_location()` concurrently per location, reuses in-memory `obs_data["analysis"]` when history is empty (eliminating duplicate fallback `get_latest` calls), avoids redundant `get_pilot_locations()` calls in `get_all_locations_rainfall_history()`, and bounds multi-location fan-out with `asyncio.Semaphore(5)` while preserving location ordering and strict primary telemetry error propagation.
   - `MonitoringService.get_monitoring_summary()` runs Stage-1 fetches (`get_pilot_locations()`, `get_ingestion_runs(limit=20)`, `get_stale_started_runs(cutoff_minutes=60)`) concurrently before evaluating location quality health with bounded concurrency (`asyncio.Semaphore(5)`).

3. **Measured Live Local Latency Impact (vs. Same-Session Baseline):**
   - `/api/v1/risk/rainfall`: **7.926s median → 0.3458s median** (~**22.9x faster**, ~**95.6% reduction**).
   - `/api/v1/monitoring/summary`: **3.036s median → 0.3437s median** (~**8.8x faster**, ~**88.7% reduction**).

---

## 2. Root-Cause Call Trace Summary

Before optimization, local profiling and code inspection identified three structural bottlenecks:
- Every Supabase PostgREST call opened a brand-new `httpx.AsyncClient`, paying repeated DNS + TCP + TLS handshakes (~0.5–1.5s each on Windows/local-to-cloud).
- `/api/v1/risk/rainfall` executed **16 sequential Supabase HTTP requests** for 3 pilot locations (`1` `get_pilot_locations` + per location: `2` sequential inside `get_latest_observations_for_location`, `1` `get_historical_observations_for_location`, plus `2` duplicate `get_latest_observations_for_location` fallback calls whenever historical observations returned `[]`).
- `/api/v1/monitoring/summary` executed **9 sequential Supabase HTTP requests** (`1` `get_pilot_locations` + `3 × 2` inside `get_latest_observations_for_location` + `1` `get_ingestion_runs` + `1` `get_stale_started_runs`).

After Phase 2.8.3:
- All HTTP calls reuse persistent HTTPX connection pools managed by FastAPI `lifespan`.
- `/api/v1/risk/rainfall` runs `1` location-list fetch followed by bounded concurrent location assessments (`analysis`, `forecasts`, and `history` in parallel, with zero duplicate fallback queries).
- `/api/v1/monitoring/summary` runs Stage 1 (`locations`, `recent_runs`, `stale_runs`) in parallel followed by bounded concurrent per-location health evaluation (`analysis` and `forecasts` in parallel).

---

## 3. Live Local Benchmark Comparison (Read-Only, 5 Requests Each)

Measurements collected locally against `http://127.0.0.1:8000` backed by live Supabase, before and after restarting the local Uvicorn process with the Phase 2.8.3 changes:

| Endpoint | Phase 2.8.3 Baseline (5 runs) | Baseline Median | Post-Optimization (5 runs) | Post-Optimization Median | Latency Reduction |
|---|---|---|---|---|---|
| `GET /api/v1/risk/rainfall` | `[11.014, 7.926, 8.171, 6.620, 7.091]s` | **7.9260s** | `[1.6121, 0.3070, 0.3458, 0.3508, 0.3181]s` | **0.3458s** | **-95.6%** (~22.9x faster) |
| `GET /api/v1/monitoring/summary` | `[4.339, 4.364, 3.036, 3.005, 2.948]s` | **3.0360s** | `[0.3438, 0.3437, 0.8650, 0.3429, 0.3367]s` | **0.3437s** | **-88.7%** (~8.8x faster) |
| `GET /api/v1/health` | — | — | `[1.3226, 0.1268, 0.1321, 0.1193, 0.0888]s` | **0.1268s** | Pooled keep-alive after cold TLS |
| `GET /api/v1/risk/rainfall/history` | — | — | `[0.3409, 0.3180, 0.3472, 0.3323, 0.3379]s` | **0.3379s** | Single `get_pilot_locations()` + parallel |

### API Contract & Read-Only Safety Verification
- `/api/v1/risk/rainfall` returned HTTP `200` on all 5 requests with identical schema keys (`assessment_timestamp`, `assessments`, `category_counts`, `component_readiness`, `highest_risk_category`, `prototype_disclaimer`, `threshold_version`, `total_locations`), `total_locations=3`.
- `/api/v1/monitoring/summary` returned HTTP `200` on all 5 requests with identical schema keys (`freshness_summary`, `ingestion_summary`, `location_healths`, `prototype_notice`, `scheduler`, `system_health`, `timestamp`).
- Live Supabase state verified unchanged after benchmarks: `GET /api/v1/alerts` still shows `total=1`, retained `[TEST-AUDIT]` record `dd028e7d-1771-424b-a984-3b1718f24886` (`RESOLVED`), and `recent_runs=0`.

---

## 4. Files Created & Modified

### Created
- `backend/app/core/http_clients.py`
- `backend/tests/test_performance_lifecycle.py`
- `artifacts/phase_2_8_3_performance_and_reliability_optimization_report.md`

### Modified
- `backend/app/main.py` — wired `init_http_clients()` and `close_http_clients()` in `lifespan(app)` with exception-safe cleanup.
- `backend/app/db/session.py` — updated `check_database_connectivity()` to reuse pooled client or accept optional `http_client`.
- `backend/app/db/supabase_client.py` — replaced per-call `AsyncClient` contexts across all methods with `_get_http_client()` (supporting `http_client` injection), concurrent `analysis` + `forecasts` fetching in `get_latest_observations_for_location()`, and `SupabaseClientError` propagation.
- `backend/app/ingestion/open_meteo.py` — updated `_execute_request_with_retry()` and `fetch_weather_preview()` to use pooled client or optional `http_client` injection.
- `backend/app/services/risk_service.py` — added `MAX_CONCURRENT_LOCATION_QUERIES = 5`, shared `_assess_location_with_history()` helper, concurrent latest+historical fetch, request-scoped fallback reuse (`fallback_analysis`), and bounded `get_all_locations_rainfall_history()` without duplicate `get_pilot_locations()` queries.
- `backend/app/services/monitoring_service.py` — added `MAX_CONCURRENT_LOCATION_QUERIES = 5`, concurrent Stage-1 fetches in `get_monitoring_summary()`, and shared bounded location health evaluation helper.
- `backend/tests/conftest.py` — ensured `client` test fixture initializes and cleanly closes loop HTTPX pools.

---

## 5. Verification Results (Post-Final-Edit)

- **Python Bytecode Compilation:** `backend\.venv\Scripts\python.exe -m compileall -q backend/app` — **PASSED** (exit code `0`).
- **Backend Automated Test Suite (mocked transport / unit & FastAPI integration):** `backend\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short` — **291 passed in 36.59s** (`0` failures, `0` skipped), including all `11` new tests in `backend/tests/test_performance_lifecycle.py` covering lifespan pool initialization/reuse/shutdown cleanup, startup-failure pool cleanup, `http_client` injection without closing caller-owned clients, zero `ResourceWarning`s, concurrent barrier execution of `analysis` + `forecasts`, bounded concurrency `<= 5`, duplicate-fallback and `N+1` elimination, order preservation, strict error propagation, and endpoint contract / `missing != zero` parity.
- **Frontend Unit Tests:** `node --test frontend/tests/alertDisplay.test.mjs` — **4 passed, 0 failed**.
- **Frontend Production Build:** `npm run build --prefix frontend` (`vite build`) — **PASSED** (`dist/assets/index-BtopIJ1k.js` built in `10.41s`).
- **Live Local Read-Only Verification:** Confirmed against running local Uvicorn (`http://127.0.0.1:8000`) connected to live Supabase across 5 repeated requests per endpoint.
