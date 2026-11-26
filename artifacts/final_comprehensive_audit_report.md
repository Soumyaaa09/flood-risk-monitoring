# FloodWatch — Final Comprehensive Engineering, Security & Scientific Audit Report

**Auditor Role:** Senior Software Architect, Python/FastAPI Engineer, PostgreSQL/Supabase Security Auditor, Data Engineer, Hydrological Data Reviewer, DevOps Engineer  
**Project:** FloodWatch — Odisha Rainfall Monitoring and Research-Based Flood Decision-Support Platform  
**Audit Scope:** Comprehensive Full-Lifecycle Review (Phase 1.0 through Phase 2.9)  
**Audit Execution Mode:** Strictly Read-Only (Zero Project File Alterations, Zero Remote SQL Execution)  
**Date of Audit:** November 23, 2026  
**Final Audit Verdict:** **CONDITIONAL PASS**  

---

## 1. Executive Summary

An independent, rigorous, evidence-based audit of the **FloodWatch** platform was conducted across the entire codebase, database migration scripts, test suites, container definitions, and documentation.

FloodWatch has achieved an exceptionally strong engineering foundation across Phases 1.0 through 2.9:
- **Core Architecture & Code Quality:** Modular service layout, asynchronous I/O, rigorous typing with Pydantic v2, and clean separation between data ingestion, analytical aggregation, risk assessment, and reporting.
- **Testing Integrity:** 184 passing backend tests (1 skipped due to absent remote DB credentials), zero Python compilation errors, and clean React production builds.
- **Scientific Responsibility:** Strict segregation between observed telemetry and forward forecasts; discrete units ($mm$, $m$, $m^3/s$) are never cross-pollinated; missing data is preserved and never falsely coerced to zero in risk evaluations; and research disclaimers are prominently displayed.
- **Spreadsheet Security:** Built-in formula injection protection (CWE-1236) escapes formula prefix characters with single quotes, protecting exported CSV files.
- **Secret Hygiene:** Verification confirmed that previous development token fallbacks in client code were eradicated, resulting in zero secret leakage in compiled production frontend assets.

However, several important operational and architectural findings require resolution or administrative execution prior to production deployment:
1. **Multi-Worker Scheduler Concurrency Guard:** The automated weather ingestion scheduler's single-process constraint is documented and protected via in-process `asyncio.Lock()`, but is **not enforced at runtime**. Deploying multiple Uvicorn worker processes (`--workers > 1`) with `ENABLE_INGESTION_SCHEDULER=true` would cause concurrent, duplicate polling of external APIs.
2. **Unapplied Remote Migration 005:** Database Migration 005 (`risk_alerts`) has not yet been executed in the remote Supabase environment. While the backend implements an in-memory fallback, persistent alert history and acknowledgement tracking across container restarts require manual application of Migration 005.
3. **Documentation vs. Migration DDL Discrepancies:** Legacy documentation referenced schema entities (`weather_locations`, `weather_observations`, and function `ingest_weather_payload_atomic`), whereas the actual migration DDL establishes `monitoring_locations`, `observations`, and function `upsert_weather_observations`.

---

## 2. Overall Audit Summary Table

| Audit Area | Status | Key Evidence | Required Action |
|:---|:---:|:---|:---|
| **A. Architecture & Code Quality** | **PASS** | Clear separation between routes, services, schemas, and engines. Clean async lifespan management. | None. Architecture is modular and clean. |
| **B. Database & Supabase Security** | **CONDITIONAL PASS** | Migrations 001–004 feature strict RLS, defense-in-depth ACL grants, and SECURITY INVOKER RPC. Migration 005 is unapplied remotely. | Apply Migration 005 manually in Supabase; update verification queries in docs. |
| **C. Ingestion & Reliability** | **PASS WITH LIMITATIONS** | Exponential backoff, HTTP 429 Retry-After parsing, idempotency keys, and ambiguous POST recovery verified. In-process scheduler lacks multi-worker enforcement. | Restrict web workers to `--workers 1` or enforce multi-worker lock in code. |
| **D. Scientific Correctness** | **PASS** | Clear separation of forecast vs. observed telemetry; units preserved ($mm$ vs $m$ vs $m^3/s$); missing data never converted to zero in risk checks. | External hydrological calibration recommended for long-term validation. |
| **E. Risk Assessment Engine** | **PASS** | Hierarchical evaluation, strict confidence downgrades on stale/delayed data, and explicit riverine overflow disclaimers. | Maintain experimental prototype notices. |
| **F. Alerts & Notifications** | **PASS WITH LIMITATIONS** | Alert evaluation service directly consumes risk engine categories without duplicating thresholds; severity `WARNING` overloads moderate risk and data outages. | Differentiate data-quality warnings from meteorological risk severities. |
| **G. Reporting & CSV Export** | **PASS** | RFC 4180 CRLF formatting, formula injection protection (`sanitize_csv_cell`), numerical preservation, and A4 print layout. | None. Fully verified. |
| **H. Frontend & UI Integration** | **PASS** | Clear handling of `UNAVAILABLE` states in slate gray badges; never renders missing data as zero or low risk; zero secret leakage in build. | None. Fully verified. |
| **I. Deployment Readiness** | **CONDITIONAL PASS** | Multi-stage Dockerfiles, non-root execution (`floodwatch:10001`), liveness/readiness probes, and strict Pydantic production validation. | Keep scheduler disabled in multi-worker environments. |
| **J. Documentation & Claims** | **PASS WITH LIMITATIONS** | Comprehensive README and operational guides; some legacy documentation names require alignment with actual migration DDL. | Correct legacy table and RPC names in documentation. |

---

## 3. Detailed Audit Findings by Area

### A. Architecture and Code Quality
- **Modularity:** API routes under `backend/app/api/v1/routes/` cleanly delegate business logic to `backend/app/services/` and `backend/app/risk_engine/`.
- **Dependency Management:** All service singletons and database clients use FastAPI dependency injection (`Depends`) with overrides supported for testing.
- **Resource Cleanup:** FastAPI lifespan context manager in `app/main.py` properly awaits `scheduler.stop()` upon application shutdown.
- **Code Uniformity:** Consistent usage of Pydantic v2 schemas across requests and responses.

### B. Database and Supabase Security
- **Least-Privilege Role Grants (Migration 004):**
  - Migration 004 implements defense-in-depth: table-level `REVOKE ALL` on operational tables (`monitoring_locations`, `ingestion_runs`, `observations`, `forecast_observation_revisions`) from `PUBLIC`, `anon`, and `authenticated`.
  - Grants `SELECT` on reference tables (`basins`, `districts`, `data_sources`) to `anon` and `authenticated`.
  - Grants DML privileges exclusively to `service_role`.
- **RPC Function Security:**
  - Function `public.upsert_weather_observations` is created as `SECURITY INVOKER` with explicit `search_path = public, extensions, pg_temp`.
  - Direct execution is granted solely to `service_role` and revoked from `PUBLIC`, `anon`, and `authenticated`.
- **Row-Level Security (RLS):**
  - RLS is explicitly enabled on all core tables.
  - Policies enforce public read on published telemetry while restricting ingestion metadata.
- **Migration Status (Finding SEC-01):**
  - Migration 005 (`risk_alerts`) is fully drafted and syntactically validated, but is **NOT VERIFIED** as applied in the remote Supabase project.

### C. Weather Data Ingestion and Reliability
- **Connector Resilience (`backend/app/ingestion/open_meteo.py`):**
  - Evaluates HTTP 429 rate-limiting and parses `Retry-After` headers (both integer seconds and HTTP-date formats).
  - Implements exponential backoff with configurable jitter and max wait limits.
- **Pipeline Idempotency (`backend/app/services/ingestion_service.py`):**
  - Generates unique idempotency keys per location and cycle timestamp.
  - Safely handles ambiguous HTTP POST failures (`409 Conflict`), recovering existing run records without duplicating data.
- **Missing Telemetry Guard:**
  - Missing precipitation values in provider feeds are preserved as `None` or omitted from upserts; they are **never** coerced to `0.0 mm`.

### D. Data Quality and Scientific Correctness
- **Temporal Alignment & Timezones:**
  - All database observations are normalized and stored in UTC (`TIMESTAMPTZ`).
  - Reporting and daily aggregations are explicitly computed in Indian Standard Time (`Asia/Kolkata`, UTC+05:30).
  - Synoptic day boundaries (`08:30 IST` to `08:30 IST` next day) and calendar day boundaries (`00:00` to `24:00 IST`) are formally segregated.
- **IMD Rainy Day Definition:**
  - Daily rainfall $\ge 2.5\text{ mm}$ is strictly applied as the rainy-day threshold, adhering to India Meteorological Department standards.
- **Antecedent Rainfall Indicator (ARI):**
  - Weighted empirical accumulation: $0.5 \times R_{24\text{h}} + 0.3 \times R_{72\text{h}} + 0.2 \times R_{7\text{d}}$.
  - The system explicitly disclaims that ARI represents physical soil moisture, infiltration rate, or water table depth.
- **Unit Segregation:**
  - Rainfall: millimeters ($mm$)
  - River water level: meters ($m$)
  - River discharge: cubic meters per second ($m^3/s$)
  - The audit confirmed these metrics are modeled as distinct enumerations and never combined or compared.

### E. Risk Assessment Engine
- **Provisional Research Thresholds:**
  - Very High: Forecast 24h $\ge 115.6\text{ mm}$, or 6h $\ge 45.0\text{ mm}$, or observed $\ge 35.0\text{ mm}$.
  - High: Forecast 24h $\ge 64.5\text{ mm}$, or 6h $\ge 25.0\text{ mm}$, or observed $\ge 15.0\text{ mm}$.
  - Moderate: Forecast 24h $\ge 15.6\text{ mm}$, or 6h $\ge 10.0\text{ mm}$, or observed $\ge 5.0\text{ mm}$.
  - Low: All metrics below moderate thresholds.
- **Fail-Safe Missing Data Behavior:**
  - When observations or forecast horizons are missing or stale, the engine outputs `RiskCategory.UNAVAILABLE`.
  - It **never** falls back to `LOW` risk on missing data.
- **Disclaimers:**
  - Explicitly states that rainfall runoff indicators do not model river bank overtopping, levee breaches, or tidal surges.

### F. Alerts and Risk Engine Consistency
- **Direct Consumption (Zero Threshold Duplication):**
  - `AlertService` directly takes `RainfallRiskAssessmentResponse.risk_category` as input.
  - It does not define competing rainfall thresholds.
- **Severity Mapping Overload (Finding ALT-01):**
  - `RiskCategory.MODERATE` &rarr; `AlertSeverity.WARNING` (`MODERATE_RAINFALL_RISK`)
  - `RiskCategory.UNAVAILABLE` &rarr; `AlertSeverity.WARNING` (`DATA_UNAVAILABLE` / `DATA_STALE`)
  - Filtering alerts purely by severity `WARNING` groups active moderate meteorological runoff potential together with telemetry outages.

### G. Historical Reporting and CSV Export
- **Formula Injection Sanitization (CWE-1236):**
  - `sanitize_csv_cell()` prepends single quotes (`'`) to trigger characters (`=`, `+`, `-`, `@`, `\t`, `\r`).
  - Legitimate rainfall values ($mm$) remain pure numeric representations and are uncorrupted.
- **RFC 4180 Compliance:**
  - Strict CRLF (`\r\n`) line terminators, minimal quoting, and UTF-8 BOM (`\ufeff`) for Microsoft Excel compatibility.
- **Printable Layout:**
  - Clean `@media print` styling formats reports to standard A4 portrait, hiding interactive controls and buttons.

### H. Frontend and UI Integration
- **Zero False-Low Rendering:**
  - `UNAVAILABLE` risk categories render in neutral slate gray (`#64748b`) with explicit "Unavailable" badges.
- **Secret Hygiene:**
  - Audited `frontend/dist/assets/index-*.js`. Confirmed 0 occurrences of `SERVICE_ROLE`, `service_role`, `postgresql://`, or `floodwatch-dev-secret-token`.

### I. Deployment and Operational Readiness
- **Container Hardening:**
  - `backend/Dockerfile` runs under non-root user `floodwatch:10001`.
  - Multi-stage `frontend/Dockerfile` uses `node:20-alpine` build &rarr; `nginx:1.27-alpine` runtime.
  - Healthcheck probes configured on both containers.
- **Production Validator:**
  - Pydantic `@model_validator` strictly rejects `DEBUG=true`, wildcard CORS (`*`), or dev tokens in production.

---

## 4. Prioritized Findings Matrix

| Finding ID | Severity | Area | Description | Impact | Remediation |
|:---|:---:|:---:|:---|:---|:---|
| **SCHED-01** | **HIGH** | Ingestion / Deploy | Scheduler multi-worker restriction is documented but not enforced in code. | Running multiple Uvicorn workers causes duplicate API polling cycles. | Add runtime guard or integrate PostgreSQL advisory lock (`pg_try_advisory_lock`). Deploy with `--workers 1`. |
| **SEC-01** | **HIGH** | Database | Migration 005 (`risk_alerts`) is unapplied in remote Supabase. | Alert persistence relies on in-memory fallback; lost across container restarts. | Execute Migration 005 manually in Supabase following verification protocol. |
| **DOC-01** | **MEDIUM** | Docs / Schema | Documentation references legacy entity names (`weather_locations`, `ingest_weather_payload_atomic`). | Verification queries in documentation fail against actual migration DDL. | Align all documentation with actual table names (`monitoring_locations`, `observations`, `upsert_weather_observations`). |
| **ALT-01** | **LOW** | Alerts UI | Severity `WARNING` overloads both moderate rainfall risk and telemetry outages. | Operators filtering by `WARNING` see both meteorological and data outage alerts. | Differentiate data-quality warnings by assigning distinct severity or badge styling. |
| **DATA-01** | **LOW** | Analyzer | Days with zero observations in `HistoricalRainfallAnalyzer` store `day_total = 0.0`. | Downstream consumers must check `observation_count > 0` to avoid misinterpreting missing data as zero rain. | Refactor `DailyRainfallRecord.rainfall_mm` to `Optional[float] = None` when `observation_count == 0`. |
| **SCI-01** | **INFO** | Hydrology | Rainfall threshold bands are adapted from generic IMD scales, not locally calibrated to Mahanadi delta hydraulics. | Surface water indicator does not predict riverine water surface elevation. | Retain statutory disclaimers; schedule future calibration with CWC / OSDMA data. |

---

## 5. Verification Test Execution Log

The following read-only verification checks were executed locally during this audit:

```
============================= test session starts =============================
platform win32 -- Python 3.11.7, pytest-8.4.2, pluggy-1.7.0
rootdir: C:\Users\rsoum\Desktop\FloodWatch\backend
configfile: pyproject.toml
plugins: anyio-4.16.0, asyncio-0.26.0
collected 185 items

backend/tests/test_alerts.py ...............                             [  8%]
backend/tests/test_deployment_config.py .............                    [ 15%]
backend/tests/test_geospatial.py ....                                    [ 17%]
backend/tests/test_health.py ....s                                       [ 20%]
backend/tests/test_historical_rainfall.py ..................             [ 29%]
backend/tests/test_ingestion.py ........................................ [ 51%]
..........                                                               [ 56%]
backend/tests/test_monitoring.py ..................                      [ 66%]
backend/tests/test_public_dashboard.py .....                             [ 69%]
backend/tests/test_reporting.py ..............                           [ 76%]
backend/tests/test_risk_engine.py ..................                     [ 86%]
backend/tests/test_risk_explanation.py ......                            [ 89%]
backend/tests/test_scheduler.py .............                            [ 96%]
backend/tests/test_weather.py ......                                     [100%]

======================= 184 passed, 1 skipped in 39.32s =======================
```

- **Python Bytecode Compilation:** Verified via `python -m compileall backend` &rarr; **0 errors**.
- **Frontend Production Build:** Verified via `npm run build --prefix frontend` &rarr; **Built successfully in 18.17s**.
- **Frontend Secret Audit:** Scanned compiled production bundle &rarr; **0 occurrences of sensitive strings**.
- **Health Endpoints:**
  - `GET /api/v1/health/liveness` &rarr; **200 OK** (`{"status": "alive"}`)
  - `GET /api/v1/health/readiness` &rarr; **503 Service Unavailable** (correctly indicates remote DB unconfigured in local dev)
  - `GET /api/v1/health` &rarr; **200 OK** (`status: degraded`)

---

## 6. Manual Supabase Verification Checklist for Project Owner

When applying migrations to remote staging or production environments, execute the following SQL verification script in the Supabase SQL Editor:

```sql
-- 1. Verify all 8 core tables exist
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
  AND table_name IN (
    'basins', 'districts', 'data_sources', 'monitoring_locations',
    'ingestion_runs', 'observations', 'forecast_observation_revisions', 'risk_alerts'
  )
ORDER BY table_name;

-- 2. Verify RPC function exists with SECURITY INVOKER
SELECT routine_name, security_type 
FROM information_schema.routines 
WHERE routine_schema = 'public' 
  AND routine_name = 'upsert_weather_observations';

-- 3. Verify Row-Level Security is active
SELECT relname AS table_name, relrowsecurity AS rls_enabled 
FROM pg_class 
WHERE relnamespace = 'public'::regnamespace 
  AND relname IN ('observations', 'ingestion_runs', 'risk_alerts');

-- 4. Verify anon role has NO permissions on private observations
SELECT privilege_type 
FROM information_schema.role_table_grants 
WHERE table_name = 'observations' AND grantee = 'anon';
-- Expected: 0 rows (No privileges)

-- 5. Verify anon role has SELECT on public risk_alerts
SELECT privilege_type 
FROM information_schema.role_table_grants 
WHERE table_name = 'risk_alerts' AND grantee = 'anon';
-- Expected: SELECT
```

---

## 7. Final Audit Verdict & Remediation Roadmap

### Final Verdict: **CONDITIONAL PASS**

FloodWatch is **functionally complete, scientifically responsible, and architecturally robust**. It achieves a **CONDITIONAL PASS** because two concrete operational actions must be completed before authorizing a live production deployment:

1. **Remediation Task 1 (Pre-Deployment):** Apply Database Migration 005 (`005_risk_alerts_and_notifications.sql`) in the target Supabase instance and run the verification checklist.
2. **Remediation Task 2 (Pre-Deployment):** Enforce single-worker container execution (`uvicorn app.main:app --workers 1`) or implement a distributed lock before scaling out web worker processes.
3. **Remediation Task 3 (Maintenance):** Update legacy table and RPC names in `docs/MIGRATIONS.md` to reflect `monitoring_locations`, `observations`, and `upsert_weather_observations`.

Once Tasks 1 and 2 are carried out, FloodWatch is fully qualified for operational staging and controlled pilot deployment.
