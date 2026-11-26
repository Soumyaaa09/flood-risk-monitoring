# FloodWatch — Phase 2.8: Final Whole-Project Engineering Audit

**Audit Date:** 2026-11-25  
**Scope:** Full read-only architecture, security, data-integrity, QA, and deployment-readiness audit of the FloodWatch Odisha Rainfall Monitoring & Decision-Support Platform following Phase 2.7.5.4.  
**Audit Posture:** Strictly read-only (zero source edits, zero SQL execution, zero alert/ingestion mutations, zero credential disclosure).

> [!CAUTION]
> **RESEARCH PROTOTYPE DISCLAIMER:**  
> FloodWatch is an experimental rainfall-based hydrological decision-support prototype and is **NOT** an official emergency-warning service. It does not claim AWS deployment, CWC river-gauge telemetry integration, or IMD/OSDMA statutory authority. Official flood warnings and evacuation orders in Odisha are issued exclusively by IMD, CWC, SRC, and OSDMA.

---

## 1. Executive Summary & Overall Engineering Verdict

### Verdict: **`APPROVED WITH KNOWN LIMITATIONS`**

FloodWatch demonstrates strong engineering discipline across layer separation, Fail-Closed production configuration guards, defense-in-depth PostgreSQL/PostGIS RLS and column-level security, hydrological metric segregation, and unit/contract test coverage (**250/250** backend pytest tests passing, **4/4** frontend unit tests passing, clean Vite production build, and live read-only Supabase PostgREST + Open-Meteo verification).

The platform is **not** marked unconditional `APPROVED FOR FINAL DEMONSTRATION` solely because:
1. The live Supabase `observations` and `ingestion_runs` tables currently hold `0` ingested rows (`ENABLE_INGESTION_SCHEDULER=false` and `ENVIRONMENT=development` in local configuration), so live risk and monitoring views currently evaluate to `UNAVAILABLE` / `critical` until an authorized ingestion cycle runs; and
2. Several verified code-level limitations remain (notably an unbounded `days`-ignoring historical observation query in `SupabaseClient` that would truncate newest data after >2,000 rows, unpooled per-request HTTPX TLS connections causing `11.89s` latency on `/api/v1/risk/rainfall`, divergent freshness cutoffs between `IngestionService` and `FreshnessEvaluator`, alert lifecycle edge cases on `RISK_REDUCED` / `data_alerts`, and an `obs_val=None` + `f24_val=0.0` `LOW`-risk edge case).

---

## 2. Completed Phases & Implemented Modules

| Area / Phase | Implemented Modules | Status |
| :--- | :--- | :--- |
| **Core Config & Sanitization** (Phases 1, 2.7.4.1–2.7.4.3, 2.9) | [config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/core/config.py), [sanitizer.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/core/sanitizer.py), [main.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/main.py) | Implemented & tested |
| **Database & Migrations 001–005** | [001_initial_schema.sql](file:///c:/Users/rsoum/Desktop/FloodWatch/database/migrations/001_initial_schema.sql) through [005_risk_alerts_and_notifications.sql](file:///c:/Users/rsoum/Desktop/FloodWatch/database/migrations/005_risk_alerts_and_notifications.sql), [005_risk_alerts_security_verification.sql](file:///c:/Users/rsoum/Desktop/FloodWatch/database/verification/005_risk_alerts_security_verification.sql) | Applied & read-only verified in live Supabase |
| **Supabase Client & Session Health** | [supabase_client.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/supabase_client.py), [session.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/db/session.py), [health.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/health.py) | Implemented & live verified |
| **Weather Ingestion & Scheduler** (Phases 2.3.2, 2.6) | [open_meteo.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/ingestion/open_meteo.py), [ingestion_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/ingestion_service.py), [ingestion_scheduler.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/ingestion_scheduler.py), [ingestion.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/ingestion.py), [weather.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/weather.py) | Implemented; Open-Meteo preview live; ingestion trigger mocked in CI |
| **Data Quality & Monitoring** | [data_quality.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/data_quality.py), [monitoring_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/monitoring_service.py), [monitoring.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/monitoring.py) | Implemented & live verified |
| **Rainfall Risk, History, ARI & Explanations** (Phases 2.1–2.4) | [config.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/config.py), [interfaces.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/interfaces.py), [rainfall_assessor.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/rainfall_assessor.py), [historical_analyzer.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/historical_analyzer.py), [explanation_engine.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/risk_engine/explanation_engine.py), [risk_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/risk_service.py), [risk.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/risk.py) | Implemented & live verified |
| **Alerts & Notifications** (Phases 2.7–2.7.5.4) | [alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py), [alerts.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/alerts.py), [alert.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/schemas/alert.py), [notifications](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/notifications/__init__.py) | Implemented & Phase 2.7.5.3–2.7.5.4 verified |
| **Historical Reporting & CSV Export** (Phase 2.8) | [reporting_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/reporting_service.py), [reports.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/api/v1/routes/reports.py) | Implemented & unit tested (empty live history) |
| **Frontend SPA & Display Helpers** | [App.jsx](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/App.jsx), [Navbar.jsx](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/components/Navbar.jsx), [DisclaimerBanner.jsx](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/components/DisclaimerBanner.jsx), 8 pages under `frontend/src/pages/`, [alertDisplay.js](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/utils/alertDisplay.js) | Implemented, tested, built |
| **Containerization & CI** (Phase 2.9) | [backend/Dockerfile](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/Dockerfile), [frontend/Dockerfile](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/Dockerfile), [nginx.conf](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/nginx.conf), [docker-compose.yml](file:///c:/Users/rsoum/Desktop/FloodWatch/docker-compose.yml), [backend_ci.yml](file:///c:/Users/rsoum/Desktop/FloodWatch/.github/workflows/backend_ci.yml) | Static configuration audited; containers not built/run in this audit |

---

## 3. Architecture Overview (Frontend / Backend / Database)

- **Frontend (`frontend/`):** React 18 SPA bundled with Vite 5, Tailwind CSS, React Router v6, Recharts, and React-Leaflet across 8 routes (`/`, `/map`, `/risk`, `/alerts`, `/history`, `/monitoring`, `/about`, `/weather-preview`). All API calls use relative `/api/v1/*` paths (proxied by Vite dev server to `127.0.0.1:8000` and by Nginx in Docker to `backend:8000`). The frontend makes **zero** direct browser queries to Supabase; `frontend/.env` contains only `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
- **Backend (`backend/app/`):** FastAPI application mounted at `/api/v1` with layered routers (`routes/`), services (`services/`), hydrological domain engines (`risk_engine/`), Open-Meteo HTTPX connector (`ingestion/`), and PostgREST client (`db/`).
- **Database (`database/migrations/`):** PostgreSQL 15+ / PostGIS schema in Supabase comprising:
  - Public reference tables (`basins`, `districts`, `data_sources`) with `anon`/`authenticated` `SELECT` RLS policies.
  - Private operational tables (`monitoring_locations`, `ingestion_runs`, `observations`, `forecast_observation_revisions`) with RLS enabled and all `anon`/`authenticated`/`PUBLIC` privileges revoked (`Migration 004`), plus archival trigger `trg_observations_forecast_revision` and atomic `SECURITY INVOKER` RPC `upsert_weather_observations(jsonb, uuid)` executable exclusively by `service_role` (`Migration 003`).
  - Alerts table (`risk_alerts`) and sanitized `WITH (security_invoker = true)` view (`v_public_risk_alerts`) (`Migration 005`), enforcing partial unique deduplication index `uq_risk_alerts_active_fingerprint` and omitting `fingerprint` and `acknowledged_by` from `anon`/`authenticated` column grants.

---

## 4. Security Audit Findings

### Verified Strengths
- **Secret Isolation & `.gitignore`:** `.env`, `backend/.env`, and `frontend/.env` are excluded from Git in [.gitignore](file:///c:/Users/rsoum/Desktop/FloodWatch/.gitignore). `frontend/.env` contains zero service-role keys, database URLs, or backend secrets.
- **Supabase Header Compatibility & Sanitization:** `build_supabase_headers()` sends opaque `sb_secret_...` and `sb_publishable_...` keys strictly via the `apikey` header without `Authorization: Bearer`, while preserving legacy JWT and authenticated user JWT behavior. `sanitize_error_message()` and `redact_headers()` strip `sb_secret_`, `sb_publishable_`, `Bearer`, `eyJ...` JWTs, Postgres URIs, URLs, and schema/constraint error details.
- **Production Safety Gates (`Settings.validate_production_configuration`):** Rejects `DEBUG=True`, wildcard CORS `*`, empty/default/short `<16` char `INGESTION_TRIGGER_TOKEN`, missing/placeholder `SUPABASE_URL`, `SUPABASE_ANON_KEY`, or `SUPABASE_SERVICE_ROLE_KEY`. Multi-worker scheduler startup (`ENABLE_INGESTION_SCHEDULER=true` with `WORKERS > 1`) aborts fast (`SCHED-01`).
- **Live PostgreSQL / PostgREST RLS & Column-Level Security:** Verified read-only against the live Supabase project (`anon` HTTP `200` on `v_public_risk_alerts` and `risk_alerts` safe columns; HTTP `401` on `risk_alerts?select=id,fingerprint`, `risk_alerts?select=id,acknowledged_by`, `observations`, `monitoring_locations`, and `ingestion_runs`).
- **Endpoint Auth & Public Projection Masking:** `/api/v1/ingestion/*`, `/api/v1/monitoring/ingestion-health`, `/api/v1/alerts/{id}/acknowledge`, and `/api/v1/alerts/{id}/resolve` strictly enforce `X-Ingestion-Token` using `secrets.compare_digest` (header-only; rejects query strings). Public alert endpoints zero `fingerprint` and mask `acknowledged_by` to `"operator"`. CSV export applies CWE-1236 formula-injection quoting (`sanitize_csv_cell`).

### Security Gaps / Observations
- **Local Development Token:** Active `backend/.env` is currently in `ENVIRONMENT=development` using the default `INGESTION_TRIGGER_TOKEN` (`floodwatch-dev-secret-token`), which is permissible locally and blocked when `ENVIRONMENT=production`, but must be rotated prior to any network-exposed staging/demo deployment.
- **Acknowledge Route Operator Identity Return (`alerts.py:171`):** `POST /api/v1/alerts/{id}/acknowledge` returns raw `acknowledged_by` to the authorized caller whereas `POST /api/v1/alerts/{id}/resolve` masks `acknowledged_by = "operator"`. Returning it to the authorized caller is low-risk, though inconsistent.
- **Raw Exception Logging on Reporting Route (`reports.py:56, 112`):** Logs `sb_err.message` directly rather than wrapping with `sanitize_error_message(...)` (partially mitigated because `SupabaseClientError` sanitizes on construction).

---

## 5. Data Integrity & Scientific Limitations

- **Research Prototype Scope Only:** Pilot monitoring points (`Paradeep Weather Grid Point`, `Cuttack Weather Grid Point`, `Kendrapara Weather Grid Point`) are virtual Open-Meteo model grid centroids (`is_official_gauge = false`, `elevation_m = NULL`).
- **Non-Operational Hydrological Modules:** `RiverStageRiskAssessorInterface` (`NON_OPERATIONAL`) and `CombinedRiskAssessorInterface` (`UNAVAILABLE`) strictly refuse gauge/multi-hazard scoring without physical telemetry or calibrated 2D hydrodynamic models.
- **Observed vs. Forecast Separation:** `HistoricalRainfallAnalyzer.sanitize_and_deduplicate` and `SupabaseClient.get_historical_observations_for_location` filter strictly to `('observed', 'analysis', 'reanalysis')` and exclude `'forecast'`. Missing days remain `None` (JSON) / `""` (CSV) rather than `0.0`.
- **Empirical ARI Notice:** The Antecedent Rainfall Indicator (`0.5*24h + 0.3*72h + 0.2*7d`) is an empirical precipitation accumulation index and does not measure soil moisture, infiltration, groundwater, or dam releases.
- **Verified Code-Level Scientific / Data Gaps:**
  1. `RainfallRiskAssessor.assess_location_risk` (`rainfall_assessor.py:116`) rejects only when **both** `obs_val is None and f24_val is None`. If `analysis.value` is missing/corrupted (`obs_val is None`, causing `QualityStatus.FAIL` in `RiskService`) while `valid_at` is fresh and `f24_val == 0.0`, the assessor still evaluates thresholds and classifies `LOW` risk with `HIGH` confidence (`rainfall_assessor.py:197` checks `WARNING` but not `FAIL`).
  2. `Settings` configures `FRESHNESS_FRESH_MINUTES=60` and `FRESHNESS_DELAYED_MINUTES=180` (used by `IngestionService.calculate_freshness` on `fetched_at`), while `FreshnessEvaluator` (`data_quality.py:45-46`) hardcodes `FRESH_MINUTES=120.0` and `DELAYED_MINUTES=360.0` (used by `MonitoringService` and `RiskService` on `valid_at`), producing conflicting freshness labels across endpoints for telemetry aged 61–120m or 181–360m.
  3. `RiskService.get_location_rainfall_risk` and `get_all_locations_rainfall_risk` (`risk_service.py:247, 296`) pass `drivers=[]` when regenerating `risk_context`, dropping `"Primary classification criteria: ..."` from `contributing_factors` whenever historical context succeeds.

---

## 6. Automated Tests, Build & Live Read-Only Verification Results

All checks below were executed on the unmodified repository state in Phase 2.8:

| Check | Scope & Mode | Result | Notes |
| :--- | :--- | :--- | :--- |
| **Backend Pytest Suite** (`pytest backend/tests -q`) | 12 test modules (`backend/tests/test_*.py`) | **250 passed in 35.32s** (`0` failed) | `test_alerts`, `test_ingestion`, `test_monitoring`, `test_reporting`, `test_scheduler`, `test_deployment_config`, etc. mock Supabase HTTP/service calls; `test_live_open_meteo_integration` hits Open-Meteo (or skips if offline) |
| **Frontend Unit Tests** (`node --test frontend/tests/alertDisplay.test.mjs`) | Display helper & source assertions | **4 passed, 0 failed** (`0` skipped) | Covers `alertDisplay.js` and neutral wording in `AlertsPage.jsx`; UI React rendering is not DOM-tested |
| **Frontend Production Build** (`npm run build --prefix frontend`) | `vite build` (`vite v5.4.21`) | **Passed** (`11.17s`, `2136` modules) | Emits chunk-size warning for `dist/assets/index-BtopIJ1k.js` (`893.73 kB`) |
| **Backend Bytecode Compile** (`compileall -q backend/app backend/tests`) | All backend Python source & tests | **Passed** (`exit 0`) | Zero syntax/compile errors |
| **Live Supabase Read-Only Check** | Real Supabase project via `Settings` / `SupabaseClient` / `httpx` | **Connected** | `districts`, `data_sources` (`open_meteo_weather`), `3` pilot `monitoring_locations`, `1` resolved `[TEST-AUDIT]` alert, `v_public_risk_alerts`, and `/rpc/upsert_weather_observations` verified; `observations` = `0`, `ingestion_runs` = `0` |
| **Live FastAPI Endpoint Smoke Check** | Read-only `GET` requests to `127.0.0.1:8000` | **All 14 public routes HTTP 200; 1 protected route HTTP 401** | `/api/v1/sources/health` (`connected` + `operational`), `/api/v1/weather/preview` (`1.94s`, `24h`), `/api/v1/risk/rainfall` (`11.89s`, `UNAVAILABLE`), `/api/v1/monitoring/summary` (`5.97s`, `critical` due to 0 observations), `/api/v1/monitoring/ingestion-health` (`401` without token) |

---

## 7. Confirmed Live Integration Capabilities vs. Unverified / Mocked Paths

- **Confirmed Live:**
  - Backend `.env` resolution from any working directory (`BACKEND_DIR / ".env"`), opaque `sb_secret_` / `sb_publishable_` key loading and `apikey`-only PostgREST authentication.
  - Supabase PostgREST read connectivity (`districts`, `monitoring_locations`, `data_sources`, `risk_alerts`, `v_public_risk_alerts`), RLS + column-level `anon` enforcement (`401` on `observations`, `monitoring_locations`, `ingestion_runs`, `fingerprint`, `acknowledged_by`).
  - Open-Meteo weather preview connectivity (`/api/v1/sources/health` and `/api/v1/weather/preview`).
  - Prior Phase 2.7.5.3 alert lifecycle (`CREATE` -> `DEDUP` -> `ACKNOWLEDGE` -> `RESOLVE` -> `409`) evidenced by the permanent resolved `[TEST-AUDIT]` record (`dd028e7d-1771-424b-a984-3b1718f24886`) still visible via `/api/v1/alerts`.
- **Simulated / Mocked or Not Yet Live-Exercised in This Audit:**
  - Live `POST /api/v1/ingestion/trigger` + `upsert_weather_observations` RPC persistence against Open-Meteo + Supabase (mocked in pytest; `observations` and `ingestion_runs` currently empty in Supabase).
  - Live multi-day historical rainfall reports / CSV exports with real non-empty telemetry (`observations` currently empty).
  - Live `ENVIRONMENT=production` container boot via `docker compose up` (Dockerfiles and `nginx.conf` reviewed statically; production config rules tested in `test_deployment_config.py`).

---

## 8. Prioritized Audit Findings (Critical / High / Medium / Low)

### Critical Findings
- **None.**

### High Findings
1. **`HIGH-01` — Historical Observation Query Ignores `days`, Sorts Ascending, and Truncates at 2,000 Rows (`supabase_client.py:657-682`):**
   - `SupabaseClient.get_historical_observations_for_location(location_id, days=7, limit=2000)` accepts `days` but never applies a `valid_at=gte.<cutoff>` filter, and queries `order=valid_at.asc&limit=2000`. Once a station exceeds ~83 days of hourly observations (`>2,000` rows), PostgREST will return only the **oldest** 2,000 rows, silently dropping recent observations from `/risk/rainfall/history`, `/risk/rainfall/antecedent`, `/reports/rainfall`, and `/reports/rainfall.csv`.

### Medium Findings
2. **`MED-01` — Unpooled Per-Call `httpx.AsyncClient` and Sequential `N+1` Queries (`supabase_client.py`, `risk_service.py:72-80, 272-304`):**
   - Every `SupabaseClient` method opens a fresh `httpx.AsyncClient` (new TCP/TLS handshake). When `observations` is empty, `get_all_locations_rainfall_risk()` also invokes `get_latest_observations_for_location()` a second time inside `get_location_history_context()`, executing ~16 sequential Supabase requests. Measured live latency on `GET /api/v1/risk/rainfall` was **11.89s** (which timed out a standard 10s client request).
3. **`MED-02` — Divergent Freshness Cutoffs Between `Settings` and `FreshnessEvaluator` (`config.py:102-104`, `ingestion_service.py:316-321`, `data_quality.py:45-46`):**
   - `IngestionService` uses `60m` (`FRESH`) / `180m` (`DELAYED`) against `fetched_at`, whereas `FreshnessEvaluator` hardcodes `120m` (`FRESH`) / `360m` (`DELAYED`) against `valid_at`.
4. **`MED-03` — Missing `obs_val` Can Still Classify `LOW` Risk When `f24_val == 0.0` (`rainfall_assessor.py:116, 197`):**
   - `assess_location_risk()` checks `if obs_val is None and f24_val is None` and ignores `QualityStatus.FAIL`. If observed rainfall fails numeric parsing or is `None` while `valid_at` is fresh and `f24_val == 0.0`, risk is categorized as `LOW` despite missing/failed observed telemetry.
5. **`MED-04` — Alert Lifecycle Edge Cases (`alert_service.py:496-550`, `supabase_client.py:754-778`):**
   - When `RiskCategory.LOW` recovers with open `prior_risk_alerts`, early `return await self._create_alert_record(...)` (`RISK_REDUCED`) skips resolving open `DATA_STALE` / `DATA_UNAVAILABLE` alerts at lines 544–550; also, `RISK_REDUCED` creation does not check `existing_same` in `open_alerts`, so repeated `HIGH -> LOW` oscillations before resolution could collide with `uq_risk_alerts_active_fingerprint`.
   - `SupabaseClient.get_alerts()` and `get_alert_by_id()` catch `Exception` and return `[]` / `None` instead of raising `SupabaseClientError`, masking database outages as zero alerts on list/summary endpoints (also causing `alerts.py` `list_alerts` / `get_alerts_summary` to never distinguish DB unavailability).
6. **`MED-05` — Uncaught Forecast Timestamp Parse in `MonitoringService` & Truncated Summary Counts (`monitoring_service.py:139`, `alert_service.py:116-118, 258`):**
   - `MonitoringService.get_location_quality_health()` calls `datetime.fromisoformat(v_raw.replace("Z", "+00:00"))` without `try/except` (unlike `RiskService`).
   - `AlertService.get_alerts(limit=100)` computes `active_count`/`acknowledged_count`/`resolved_count` only from the fetched `limit` slice rather than total DB counts.
7. **`MED-06` — Empty Live Telemetry State & Unverified Live Ingestion Run:**
   - Live Supabase currently contains `0` `observations` and `0` `ingestion_runs`, so live dashboards show `UNAVAILABLE` risk and `critical` monitoring health until an initial ingestion trigger is authorized and executed.
8. **`MED-07` — Missing Frontend Tests/Build in CI & Incomplete Frontend Coverage:**
   - `.github/workflows/backend_ci.yml` executes only `pytest`, omitting `node --test frontend/tests/alertDisplay.test.mjs` and `npm run build --prefix frontend`. Frontend lacks component/DOM rendering tests for pages (`HomePage`, `AlertsPage`, `GeospatialMapPage`, `HistoricalAnalyticsPage`, `MonitoringDashboardPage`).

### Low Findings
9. **`LOW-01` — `RiskService` Clears `drivers` When Rebuilding `risk_context` (`risk_service.py:247, 296`):** Passing `drivers=[]` strips `"Primary classification criteria: ..."` from `contributing_factors`.
10. **`LOW-02` — Route Consistency (`alerts.py:66, 93, 171`, `reports.py:56, 112`):** `acknowledge_alert` returns unmasked `acknowledged_by` to the authorized caller while `resolve_alert` masks it to `"operator"`; `list_alerts`/`get_alerts_summary` return HTTP 500 instead of 503 on DB error; `reports.py` logs raw `sb_err.message`.
11. **`LOW-03` — UI & Metadata Polish (`Navbar.jsx:32`, `App.jsx:36`, `config.py:77`):** `Navbar.jsx` labels `/api/v1/health` HTTP 200 as `Online` even when `data.status === "degraded"`; `App.jsx` footer still references `Phase 2.5` and `APP_VERSION` remains `0.1.0-phase1`; Vite production bundle chunk `index-*.js` is `893.73 kB` (>500 kB warning).
12. **`LOW-04` — Retained `[TEST-AUDIT]` Record Narrative:** As required by non-mutation rules, the permanent resolved `[TEST-AUDIT]` DB record retains its pre-2.7.5.4 `description` string (`0 mm observed` vs `evidence` `1 mm`), while `AlertsPage.jsx` displays `Risk Category: HIGH` and `resolved alerts` cleanly.

---

## 9. Deployment Readiness & Final Recommendations

### Readiness Summary
- **Backend & Database Schema:** Ready for controlled staging/demonstration with `ENVIRONMENT=production` guards, Migration 001–005 security controls, and single-worker scheduler enforcement (`WORKERS=1`).
- **Pre-Demo / Pre-Production Actions Required:**
  1. **Populate Live Telemetry (Owner-Approved):** Trigger an authorized `POST /api/v1/ingestion/trigger` (or enable `ENABLE_INGESTION_SCHEDULER=true` on a single dedicated worker) so live dashboards reflect current Open-Meteo nowcasts and forecasts instead of `UNAVAILABLE`.
  2. **Production Secrets & CORS:** Set `ENVIRONMENT=production`, `DEBUG=false`, a strong random `INGESTION_TRIGGER_TOKEN`, and explicit `CORS_ORIGINS` before exposing externally.
  3. **Targeted Code Remediations Before Extended Runtime:**
     - Fix `SupabaseClient.get_historical_observations_for_location()` to filter `valid_at >= cutoff` (or query `order=valid_at.desc` and reverse chronologically) and reuse a pooled `httpx.AsyncClient` across `SupabaseClient` calls (plus avoid redundant fallback queries in `RiskService`).
     - Reject `QualityStatus.FAIL` / missing `obs_val` in `RainfallRiskAssessor`, unify freshness thresholds between `Settings` and `FreshnessEvaluator`, preserve `drivers` in `RiskService`, and refine `RISK_REDUCED` / `data_alerts` transitions in `AlertService`.
     - Add frontend build + `node --test` steps to `.github/workflows/backend_ci.yml`.
