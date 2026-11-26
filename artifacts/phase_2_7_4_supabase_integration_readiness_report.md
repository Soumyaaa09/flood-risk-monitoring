# FloodWatch — Phase 2.7.4: Supabase Integration Readiness Audit Report

**Date:** 2026-11-25  
**Author:** Senior Supabase Integration Engineer, FastAPI Architect, PostgreSQL Security Auditor  
**Project:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Audit Scope:** Read-Only Readiness Audit for Live Supabase Alert & Ingestion Integration  
**Verdict:** **`NOT READY`**

---

## 1. Executive Summary

A comprehensive, read-only Supabase integration and production-safety audit was executed across configuration loading, PostgREST client initialization, alert persistence/fallback behavior, health reporting, Git hygiene, endpoint sanitization, and database role permissions.

### Verdict: **`NOT READY`**

While the Supabase database schema (Migrations 001–005) and `anon` read paths are confirmed live and enforcing least-privilege RLS/column protections, FloodWatch is **not yet ready** for live mutating alert workflow verification or production deployment due to three confirmed blockers:
1. **Missing Backend Service-Role Credential:** `backend/.env` has `SUPABASE_URL` and `SUPABASE_ANON_KEY` populated, but `SUPABASE_SERVICE_ROLE_KEY` is unpopulated/empty, blocking all trusted backend writes (`create_alert`, `update_alert`, `get_pilot_locations`, `ingestion_runs`, `observations`).
2. **Working-Directory-Dependent `.env` Loading (`CONF-01`):** `Settings.model_config` specifies a relative `env_file=".env"`. Launching Uvicorn from the repository root (`--app-dir backend`) causes `backend/.env` to be skipped entirely, leaving the running server in `not_configured` mode.
3. **Silent In-Memory Fallback in Production (`PROD-01`):** `AlertService` write paths (`_create_alert_record`, `acknowledge_alert`, `resolve_alert`, `_resolve_alert_record`, `_touch_alert`) catch database exceptions and silently store/update alerts in ephemeral `self._memory_alerts` without checking `settings.is_production`, and `Settings.validate_production_configuration()` fails to enforce `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_ANON_KEY`.

---

## 2. Configuration & `.env` Loading Inspection Results

### 2.1 Environment File Resolution (`CONF-01` — High)
- **File Inspected:** `backend/app/core/config.py` (lines 65–69)
- **Observation:**
  ```python
  model_config = SettingsConfigDict(
      env_file=".env",
      env_file_encoding="utf-8",
      extra="ignore",
  )
  ```
- **Verified Behavior:**
  - Running `Settings()` with `cwd` = workspace root (`C:\Users\rsoum\Desktop\FloodWatch`, where no root `.env` exists) yields: `SUPABASE_URL`: unset, `SUPABASE_ANON_KEY`: unset, `SUPABASE_SERVICE_ROLE_KEY`: unset.
  - Running `Settings(_env_file="backend/.env")` yields: `SUPABASE_URL`: set, `SUPABASE_ANON_KEY`: set, `SUPABASE_SERVICE_ROLE_KEY`: **unset (empty)**.
- **Impact:** Starting the backend from the workspace root (e.g., `uvicorn app.main:app --app-dir backend`) silently bypasses `backend/.env`, causing `/api/v1/health` to report `database.status = "not_configured"`.

### 2.2 Secret & Placeholder Hygiene (No Secrets Exposed)
- **`backend/.env` (inspected safely via presence/length checks only):**
  - `SUPABASE_URL`: Populated (valid `https://*.supabase.co` URL, non-placeholder).
  - `SUPABASE_ANON_KEY`: Populated (46-character non-placeholder key).
  - `SUPABASE_SERVICE_ROLE_KEY`: **Empty / Unconfigured**.
  - `DATABASE_URL`: Populated.
- **`frontend/.env`:** Contains strictly `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`; no service-role credential or database URL is present.
- **`.gitignore` Exclusion:** `.gitignore` lines 30–36 explicitly exclude `.env`, `.env.*`, `backend/.env`, `frontend/.env`, and `frontend/.env.local`, while keeping `!.env.example`. *(Note: the workspace directory is not currently initialized as a `.git` repository.)*
- **`.env.example` Templates:** Both `backend/.env.example` and `frontend/.env.example` contain strictly non-sensitive placeholder strings (`your-project-id`, `your-supabase-*`, `floodwatch-dev-secret-token`).

### 2.3 Production Validation Gap (`CONF-02` — High)
- **File Inspected:** `backend/app/core/config.py` (lines 189–230, `validate_production_configuration`)
- **Observation:** When `ENVIRONMENT="production"`, `Settings` rejects `DEBUG=True`, `CORS_ORIGINS=["*"]`, weak/default `INGESTION_TRIGGER_TOKEN`, and empty/placeholder `SUPABASE_URL`. However, it **does not validate** `SUPABASE_ANON_KEY` or `SUPABASE_SERVICE_ROLE_KEY`. A production server can start cleanly with `SUPABASE_SERVICE_ROLE_KEY=""`.

---

## 3. Database Client Initialization & Live Connectivity Check

### 3.1 `SupabaseClient` Credential Separation
- **File Inspected:** `backend/app/db/supabase_client.py` (lines 31–84)
- **Verified Design:**
  - Correctly separates `self.service_role_key` and `self.anon_key`.
  - `_get_headers(use_service_role=True)` and `_validate_service_role_configured()` strictly require `service_role_key` and **never** fall back to `anon_key` for writes (`create_alert`, `update_alert`, `get_pilot_locations`, `create_ingestion_run`, `persist_weather_observations`).

### 3.2 Live Read-Only PostgREST Verification (with `backend/.env` explicitly loaded)
Executed safe, read-only HTTP GET checks without modifying data or executing remote SQL:
- `check_database_connectivity()` (`districts?select=id&limit=1` with `anon` key):
  - **Result:** `{'status': 'connected', 'provider': 'supabase_postgrest', 'table_verified': 'districts'}`
- `GET /rest/v1/v_public_risk_alerts?limit=1` with `anon` key:
  - **Result:** `HTTP 200 OK` (0 rows currently present; confirms Migration 005 public view and `security_invoker = true` grants are live).
- `GET /rest/v1/risk_alerts?limit=1` (`SELECT *`) with `anon` key:
  - **Result:** `HTTP 401` (Permission denied on sensitive columns `acknowledged_by` / `fingerprint`; confirms Migration 005 column-level lockdown is actively enforced in Supabase).

### 3.3 Missing-Credentials Health Behavior (Live Server on `127.0.0.1:8000`)
- `GET /api/v1/health`: Returns `200 OK` with `status: "degraded"`, `database.status: "not_configured"`, `database.message: "Supabase URL or API key not configured"`.
- `GET /api/v1/health/readiness`: Returns `503 Service Unavailable` with `status: "not_ready"`, `database: "disconnected"`.
- **Health Reporting Discrepancy (`CONF-03` — Minor):** `GET /api/v1/sources/health` (`backend/app/services/ingestion_service.py:531, 542`) marks `supabase_status = "connected"` solely on `bool(self.supabase.base_url)` without probing Supabase, and returns `status: "healthy"` based solely on Open-Meteo even when `Database: not_configured` and `service_role_configured: false` (observed live on `127.0.0.1:8000/api/v1/sources/health`).

---

## 4. Persistent Storage vs. In-Memory Fallback Inspection (`AlertService`)

Inspected `backend/app/services/alert_service.py` and `backend/app/db/supabase_client.py` across all alert lifecycle paths:

| Operation | Method | Supabase Target | Behavior on DB Error / Missing Key | Production Risk |
| :--- | :--- | :--- | :--- | :--- |
| **List Alerts** | `AlertService.get_alerts()` | `risk_alerts` (service role) or `v_public_risk_alerts` (anon) | Silently returns `[]` on non-200 in `SupabaseClient`; `AlertService` falls back to `_memory_alerts`. | May serve stale/empty memory state silently if DB fails. |
| **Deduplication Lookup** | `AlertService._get_open_alerts_for_location()` | `risk_alerts` (`SELECT *`, no column filter) | Uses `use_service_role=bool(service_role_key)`. Without service key, `anon` hits `HTTP 401`, returns `[]`, falls back to `_memory_alerts`. | **Defect (`DB-01`):** Deduplication lookup against DB breaks silently if service key is absent. |
| **Alert Creation** | `AlertService._create_alert_record()` | `risk_alerts` (`POST`, service role) | Catches all exceptions (`DB alert insertion fallback to memory`), writes to `_memory_alerts`, and **returns `AlertResponse` as success**. | **CRITICAL (`PROD-01`):** Production silently reports persistent creation while writing only to ephemeral RAM. |
| **Alert Acknowledgement** | `AlertService.acknowledge_alert()` | `risk_alerts` (`PATCH`, service role) | Catches all exceptions; if `alert_id` is in `_memory_alerts`, updates RAM and **returns `AlertResponse` (`200 OK`)**. | **CRITICAL (`PROD-01`):** Silent non-persistent `200 OK` if alert originated in RAM fallback. |
| **Manual Resolution** | `AlertService.resolve_alert()` | `risk_alerts` (`PATCH`, service role) | Catches all exceptions; if `alert_id` is in `_memory_alerts`, updates RAM and **returns `AlertResponse` (`200 OK`)**. | **CRITICAL (`PROD-01`):** Silent non-persistent `200 OK` if alert originated in RAM fallback. |
| **Auto-Resolve / Touch** | `_resolve_alert_record()`, `_touch_alert()` | `risk_alerts` (`PATCH`, service role) | `except Exception: pass` (completely silent), updates `_memory_alerts`. | **HIGH (`PROD-01`):** Silent loss of escalation/de-escalation/touch persistence in production. |

---

## 5. Security Findings & Permission Verification

### 5.1 Public Endpoint Credential & Config Isolation
- Audited all 7 route modules (`alerts.py`, `health.py`, `ingestion.py`, `monitoring.py`, `reports.py`, `risk.py`, `weather.py`).
- No endpoint exposes `SUPABASE_URL`, API keys, `DATABASE_URL`, `INGESTION_TRIGGER_TOKEN`, or `fingerprint`.
- **Minor Finding (`SEC-01` — Low):** `backend/app/api/v1/routes/weather.py` (lines 40, 62, 67, 87, 105, 161) passes raw `str(exc)` directly into `HTTPException(detail={"message": str(exc)})` instead of routing non-provider exceptions through `sanitize_error_message()` as done in `alerts.py`, `risk.py`, `monitoring.py`, `ingestion.py`, and `reports.py`.

### 5.2 Database Role Permission Assumptions (Task 10)
Verified `database/migrations/004_least_privilege_permissions.sql` and `005_risk_alerts_and_notifications.sql`:
- **`service_role`:** Granted `SELECT` on reference/location tables (`basins`, `districts`, `data_sources`, `monitoring_locations`); `SELECT, INSERT, UPDATE` on `ingestion_runs` and `observations`; `SELECT, INSERT, UPDATE, DELETE` on `forecast_observation_revisions` and `risk_alerts`; `EXECUTE` on `upsert_weather_observations`. Matches every `use_service_role=True` method in `SupabaseClient`.
- **`anon` / `authenticated`:** Granted `SELECT` only on public reference tables (`basins`, `districts`, `data_sources`), `SELECT` on 13 non-sensitive columns of `risk_alerts`, and `SELECT` on `v_public_risk_alerts`. Zero privileges on private ingestion tables or `acknowledged_by` / `fingerprint`.

---

## 6. Tests Executed & Observed Results

All checks executed were strictly read-only:

| Check / Suite | Command / Target | Outcome | Notes |
| :--- | :--- | :--- | :--- |
| **Deployment Config Tests** | `pytest backend/tests/test_deployment_config.py -v` | **17 passed** (0.14s) | Verified settings validation, health/readiness probes (mocked DB). |
| **Live Health Probe (No `.env` loaded)** | `GET http://127.0.0.1:8000/api/v1/health` | `200 OK` (`degraded`) | Reported `database.status = "not_configured"`. |
| **Live Readiness Probe (No `.env` loaded)** | `GET http://127.0.0.1:8000/api/v1/health/readiness` | `503 Service Unavailable` | Correctly reported `status: "not_ready"`, `database: "disconnected"`. |
| **Live Sources Health Probe** | `GET http://127.0.0.1:8000/api/v1/sources/health` | `200 OK` (`healthy`) | Reported `Database: not_configured` while overall `status` was `"healthy"` (`CONF-03`). |
| **`.env` Path Resolution Check** | Python `Settings()` vs `Settings(_env_file='backend/.env')` | Confirmed discrepancy | `cwd` lookup missed `backend/.env`; explicit path loaded URL & anon key (`CONF-01`). |
| **Live Supabase PostgREST Check (Read-Only)** | `check_database_connectivity()` + `GET v_public_risk_alerts` / `risk_alerts` | **Connected** (`anon`) | `districts`: `200`; `v_public_risk_alerts`: `200`; `risk_alerts` (`*`): `401`. |

---

## 7. Required Manual Setup & Pre-Verification Remediation Steps

Before executing live mutating alert workflows (Phase 2.7.3 Tests T9–T13) or deploying to production, the following steps are required:

1. **Populate Service-Role Key Locally (Owner Action):**
   - Add the project's service-role key to `SUPABASE_SERVICE_ROLE_KEY` inside `backend/.env` locally (never commit or share in chat).
2. **Fix `.env` Path Resolution in `backend/app/core/config.py`:**
   - Resolve `env_file` relative to the `backend/` directory (e.g., `Path(__file__).resolve().parents[2] / ".env"`) so `Settings` loads `backend/.env` reliably regardless of launch `cwd`.
3. **Fail-Closed Production Persistence in `AlertService` & `Settings`:**
   - Update `Settings.validate_production_configuration()` to require non-empty, non-placeholder `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` when `ENVIRONMENT == "production"`.
   - Update `AlertService` (`_create_alert_record`, `acknowledge_alert`, `resolve_alert`, `_resolve_alert_record`, `_touch_alert`) so that when `self.settings.is_production` is `True`, database write failures raise `SupabaseClientError` instead of silently falling back to `self._memory_alerts`.
4. **Optional Security & Observability Hardening:**
   - In `SupabaseClient.get_alerts` / `get_alert_by_id` / `get_open_alerts_for_location`, log non-200 HTTP status codes safely (without credentials) rather than silently returning `[]`.
   - In `weather.py`, wrap exception messages with `sanitize_error_message()`.
   - In `ingestion_service.py:get_sources_health()`, use real connectivity status for `supabase_status` instead of `bool(self.supabase.base_url)`.
