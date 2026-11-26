# FloodWatch — Phase 2.7.5: Controlled Live Alert Workflow Verification Report (Preflight & Approval Gate)

**Date:** 2026-11-25  
**Author:** Senior Full-Stack Integration Engineer, Supabase Database Engineer, FastAPI QA Engineer  
**Project:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Scope:** Stage 1 (Read-Only Preflight), Stage 2 (Safe Test Design), Stage 3 (Owner Approval Gate)  
**Verdict:** **`BLOCKED`** *(Paused at Stage 3 Owner Approval Gate **AND** blocked on pre-mutation schema mismatch `SCHEMA-01` discovered during Stage 2 inspection)*

---

## 1. Executive Summary

Stage 1 (read-only preflight) and Stage 2 (safe test design) were executed against the configured Supabase backend and active FastAPI server (`127.0.0.1:8000`) without creating, modifying, acknowledging, resolving, or deleting any database records.

### Key Outcomes:
1. **Stage 1 Preflight (`PASS`):**
   - Trusted Supabase connectivity via `sb_secret_...` (`apikey`-only header, no `Authorization: Bearer`) is operational (`HTTP 200 OK` on `monitoring_locations`, `risk_alerts`, `v_public_risk_alerts`, `districts`).
   - Registered pilot locations verified (`3` stations: Cuttack, Kendrapara, Paradeep).
   - Existing `public.risk_alerts` count verified at **`0` rows** (`0` open alerts, `0` acknowledged, `0` resolved).
   - Automated ingestion scheduler is safely `disabled` (`state: "disabled"`).
2. **Critical Stage 2 Discovery (`SCHEMA-01` — Live Insert Blocker):**
   - Inspecting `database/migrations/005_risk_alerts_and_notifications.sql` alongside `backend/app/services/alert_service.py` revealed that `public.risk_alerts` has **strictly 15 canonical columns** and does **not** include `location_name` or `district_name`.
   - Read-only PostgREST verification confirmed `GET /rest/v1/risk_alerts?select=location_name,district_name&limit=0` fails with **`HTTP 400`** (PostgreSQL error **`42703`: undefined column**), whereas selecting the 15 canonical columns returns **`HTTP 200 OK`**.
   - However, `AlertService._create_alert_record()` (`backend/app/services/alert_service.py:577–578`) includes `"location_name"` and `"district_name"` in the `POST /rest/v1/risk_alerts` payload. Consequently, **every live alert insert via `AlertService` will currently fail with HTTP 400** (and silently fall back to RAM in development mode) until those two non-table keys are stripped prior to calling `supabase.create_alert()`.
3. **Stage 3 Approval Gate (`STOPPED`):**
   - No live database mutations were performed. Execution is halted awaiting project owner approval for both remediating `SCHEMA-01` in Python (no DB migration needed) and executing the controlled synthetic test record workflow.

---

## 2. Stage 1 — Read-Only Preflight Results

### 2.1 Live Supabase Trusted Backend & Table Checks
Executed strictly read-only queries (`SupabaseClient` using `sb_secret_...` via `apikey` only, zero row contents exposed):

| Check | Target | Result | Details |
| :--- | :--- | :--- | :--- |
| **Secret Header Format** | `SupabaseClient._get_headers()` | **PASS** | `is_opaque_supabase_key=True`; `Authorization` absent from both secret & publishable headers. |
| **Pilot Locations** | `SupabaseClient.get_pilot_locations()` | **PASS** | Returned `3` pilot UUIDs (`Cuttack`, `Kendrapara`, `Paradeep`). |
| **Existing Alert Count** | `SupabaseClient.get_alerts(limit=500)` | **PASS** | `0` total rows in `public.risk_alerts` (`0` open alerts on any station). |
| **15-Column Contract Check** | `GET /rest/v1/risk_alerts?select=<15_cols>&limit=0` | **PASS** | `HTTP 200 OK`. |
| **Unsupported Column Probe** | `GET /rest/v1/risk_alerts?select=location_name,district_name&limit=0` | **CONFIRMED `400`** | `HTTP 400` / PostgreSQL `42703` (`SCHEMA-01` proof). |

### 2.2 Live Local FastAPI Endpoints (`http://127.0.0.1:8000`)
- **`GET /api/v1/health`:** `200 OK` (`status: "ok"`, `environment: "development"`, `database.status: "connected"`, `table_verified: "districts"`).
- **`GET /api/v1/health/readiness`:** `200 OK` (`status: "ready"`, `database: "connected"`).
- **`GET /api/v1/sources/health`:** `200 OK` (`status: "healthy"`, `connectivity: "Database: connected; WeatherAPI: operational"`, `service_role_configured: true`).
- **`GET /api/v1/monitoring/scheduler`:** `200 OK` (`enabled: false`, `state: "disabled"`, `is_cycle_in_progress: false`).
- **`GET /api/v1/alerts/summary`:** `200 OK` (`total_active_alerts: 0`, `total_acknowledged_alerts: 0`, `recent_alerts: []`).
- **Config Status:** `INGESTION_TRIGGER_TOKEN` configured (`True`).

---

## 3. Stage 2 — Schema Alignment Finding (`SCHEMA-01`) & Safe Test Design

### 3.1 Schema & Constraint Audit (`005_risk_alerts_and_notifications.sql`)
- **15 Canonical Columns on `public.risk_alerts`:**
  `id`, `location_id`, `alert_type`, `severity`, `status`, `title`, `description`, `evidence`, `fingerprint`, `created_at`, `updated_at`, `acknowledged_at`, `acknowledged_by`, `resolved_at`, `resolved_reason`.
- **CHECK Constraints:**
  - `alert_type IN ('MODERATE_RAINFALL_RISK', 'HIGH_RAINFALL_RISK', 'VERY_HIGH_RAINFALL_RISK', 'DATA_UNAVAILABLE', 'DATA_STALE', 'RISK_REDUCED')`
  - `severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL')`
  - `status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED')`
- **Deduplication Partial Unique Index:**
  - `uq_risk_alerts_active_fingerprint` on `(location_id, fingerprint)` `WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')`.
- **Blocker `SCHEMA-01` Identified Before Mutation:**
  - `AlertService._create_alert_record()` (`backend/app/services/alert_service.py:574–598`) constructs `record` with `"location_name"` and `"district_name"` and passes `record` directly to `supabase.create_alert(record)`.
  - Mocked/unit tests in `test_alerts.py` did not catch this because `_memory_alerts` is an unconstrained Python dictionary.
  - Against live Supabase PostgREST, `POST /rest/v1/risk_alerts` with `location_name` and `district_name` will fail with `HTTP 400` (`PGRST204` / `42703`).
  - Additionally, on reads (`SupabaseClient.get_alerts` / `get_alert_by_id`), `location_name` and `district_name` are not present on `risk_alerts` or `v_public_risk_alerts`, so `AlertResponse.location_name` will serialize as `None` unless enriched from `monitoring_locations` (note: `AlertsPage.jsx` falls back gracefully when `district_name` is `None`, though enriching `location_name` in `AlertService` or `SupabaseClient` improves UI station filtering).

---

## 4. Stage 3 — Project Owner Approval Gate (STOPPED)

**No live writes were executed.** Before Stage 4 can proceed, explicit owner approval is requested for the following:

### 4.1 Prerequisite Code Fix (`SCHEMA-01`, Zero DB Migration Needed)
- Update `AlertService._create_alert_record()` so the payload sent to `supabase.create_alert()` contains **strictly the 15 canonical `risk_alerts` columns** (omitting `location_name` and `district_name` from the DB insert body while retaining them in memory / enriching them for API responses from `monitoring_locations`).

### 4.2 Exact Synthetic Test Record to Be Created
- **Target Station:** `Paradeep Weather Grid Point` (`location_id = 3b493f43-58a7-4786-bedf-39d82b9bc260`, currently `0` open alerts).
- **Columns & Values:**
  - `id`: `<generated UUID v4>`
  - `location_id`: `"3b493f43-58a7-4786-bedf-39d82b9bc260"`
  - `alert_type`: `"HIGH_RAINFALL_RISK"`
  - `severity`: `"HIGH"`
  - `status`: `"NEW"`
  - `title`: `"[TEST-AUDIT] FloodWatch Alert Lifecycle Verification"`
  - `description`: `"[TEST-AUDIT] Controlled Phase 2.7.5 integration verification alert. Non-operational synthetic record."`
  - `evidence`: `{"test_audit": true, "phase": "2.7.5", "risk_category": "HIGH", "recent_observed_mm": 0.0, "forecast_24h_mm": 0.0, "observation_freshness": "fresh", "trigger_reason": "[TEST-AUDIT] Controlled lifecycle verification"}`
  - `fingerprint`: `"3b493f43-58a7-4786-bedf-39d82b9bc260:HIGH_RAINFALL_RISK"`

### 4.3 Exact API / Service Operations & Lifecycle Transitions
1. **Creation (`NEW`):** Trigger `AlertService.evaluate_location_assessment()` with a synthetic `RainfallRiskAssessmentResponse` for Paradeep (`HIGH` risk, `title` overridden/tagged `[TEST-AUDIT]`) $\rightarrow$ inserts 1 row into `public.risk_alerts` (`status = 'NEW'`). Verify via `GET /api/v1/alerts` and `GET /api/v1/alerts/{test_id}`.
2. **Duplicate Prevention (`NEW`):** Re-evaluate the identical `RainfallRiskAssessmentResponse` $\rightarrow$ verifies `_get_open_alerts_for_location()` finds the live DB row by `fingerprint` and calls `_touch_alert()`, updating `updated_at` and `evidence` with **total row count remaining `1`**.
3. **Acknowledgement (`NEW` $\rightarrow$ `ACKNOWLEDGED`):** Execute `POST /api/v1/alerts/{test_id}/acknowledge` with `X-Ingestion-Token` and `{"acknowledged_by": "phase_2_7_5_qa_operator"}`. Verify DB row updates `status = 'ACKNOWLEDGED'`, `acknowledged_at`, and `acknowledged_by`.
4. **Public Privacy Check (`READ-ONLY`):** Query `GET /api/v1/alerts`, `GET /api/v1/alerts/{test_id}`, and `GET /api/v1/alerts/summary`. Verify `"fingerprint"` is absent and `"acknowledged_by"` returns `"operator"`.
5. **Resolution (`ACKNOWLEDGED` $\rightarrow$ `RESOLVED`):** Execute `POST /api/v1/alerts/{test_id}/resolve` with `X-Ingestion-Token` and `{"resolved_reason": "[TEST-AUDIT] FloodWatch Phase 2.7.5 lifecycle verification complete.", "resolved_by": "phase_2_7_5_qa_operator"}`. Verify DB row updates `status = 'RESOLVED'`, `resolved_at`, and `resolved_reason`.
6. **Repeated Resolution Guard (`RESOLVED` $\rightarrow$ `HTTP 409`):** Execute `POST /api/v1/alerts/{test_id}/resolve` a second time on `{test_id}` $\rightarrow$ verify `HTTP 409 Conflict` (`invalid_lifecycle_transition`).
7. **Frontend Refresh Verification:** Load `/alerts` in Vite UI (`http://127.0.0.1:5173/alerts`) and confirm Navbar badge increments to `1` during active state, updates automatically via 60s/focus polling, and clears to `0` after resolution while displaying the record under **Resolved History**.

### 4.4 Expected Database Changes, Side Effects & Identifiability
- **Database Changes:** Exactly `1` row inserted into `public.risk_alerts` (`location_id = 3b493f43-58a7-4786-bedf-39d82b9bc260`) and transitioned through `NEW` $\rightarrow$ `ACKNOWLEDGED` $\rightarrow$ `RESOLVED`. No other tables (`observations`, `ingestion_runs`, `monitoring_locations`) are modified.
- **Potential Side Effects:** During Steps 1–4 (typically < 1 minute), public endpoints (`GET /api/v1/alerts`, `/summary`) and the Navbar badge will show `1` active `[TEST-AUDIT]` alert. Once Step 5 completes, active count returns to `0`.
- **Identifiability:** The row remains permanently distinguishable in `public.risk_alerts` by its `[TEST-AUDIT]` prefix in `title`, `description`, `resolved_reason`, and `"test_audit": true, "phase": "2.7.5"` inside `evidence` JSONB. No automatic deletion will be performed.

---

## 5. Verification Distinction Summary

| Verification Category | Scope Covered | Execution Status |
| :--- | :--- | :--- |
| **Live Supabase Read-Only Checks** | Secret-key connectivity, `monitoring_locations` (3 rows), `risk_alerts` count (`0`), 15-column schema contract (`200`), unsupported column check (`400` / `42703`) | **EXECUTED & VERIFIED** |
| **Live Local API Read-Only Checks** | `/api/v1/health`, `/health/readiness`, `/sources/health`, `/monitoring/scheduler`, `/alerts/summary` on `127.0.0.1:8000` | **EXECUTED & VERIFIED** |
| **Mocked / Unit Regression Suite** | 245 backend tests (uses `_memory_alerts` / mocked HTTP for writes; did not detect `SCHEMA-01`) | **PASSED IN PHASE 2.7.4.3** |
| **Stage 4 Live Mutations (Tests 1–8)** | Live DB alert insert, deduplication, acknowledge, resolve, 409 conflict, public privacy, UI refresh | **BLOCKED (Awaiting `SCHEMA-01` Fix + Owner Approval)** |
