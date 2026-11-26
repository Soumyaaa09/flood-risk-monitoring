# FloodWatch — Final Security & Scheduler Corrections Report

**Date:** November 23, 2026  
**Role:** Senior PostgreSQL/Supabase Security Engineer and Python/FastAPI Architect  
**Task:** Targeted Final Security, Migration Upgrade & Scheduler Concurrency Corrections  
**Project:** FloodWatch — Odisha Rainfall Monitoring and Research-Based Flood Decision-Support Platform  
**Target Locations:** Paradeep, Cuttack, Kendrapara (Mahanadi River Delta, Odisha)  
**Remediation Status:** **ALL TARGETED REQUIREMENTS COMPLETED AND VERIFIED (100%)**

---

## 1. Executive Summary

Following an independent architectural review of the final audit remediation, targeted corrections were applied to resolve security, database upgrade safety, and process concurrency edge cases. 

All corrections have been implemented strictly within safe local-code boundaries, accompanied by automated regression test suites, zero remote SQL execution, and zero credential leakage.

### Key Outcomes
1. **Authoritative Worker Concurrency Safety:**
   - Established `Settings.WORKERS` as the single authoritative application worker configuration.
   - Wired the supported production launcher (`python -m app.main`) to pass `settings.WORKERS` directly to Uvicorn.
   - Implemented a best-effort defense-in-depth detection helper while explicitly disclaiming that environment variable or CLI parsing alone can detect every distributed container topology.
   - Corrected external ingestion trigger references in error messages and documentation from `/api/v1/weather/ingest` to the actual endpoint `/api/v1/ingestion/trigger`.
   - Documented that horizontal pod autoscaling and multi-replica clusters require external orchestration (cron / AWS EventBridge) or distributed coordination (Celery / Redis).
2. **Migration 005 Upgrade Safety & PostgreSQL 15+ PostgREST Security:**
   - Added an explicit constraint migration for `risk_alerts_severity_check` in `005_risk_alerts_and_notifications.sql` so `DATA_WARNING` is accepted even if the `risk_alerts` table already exists.
   - Defined `v_public_risk_alerts` explicitly `WITH (security_invoker = true)` (PostgreSQL 15+), ensuring calling roles evaluate RLS and column-level privileges.
   - Stripped obsolete column-level grants from `anon` and `authenticated` via an automated PL/pgSQL cleanup block.
   - Withheld `acknowledged_by` (operator identity) and `fingerprint` from public grants.
   - Configured `SupabaseClient` to read from `v_public_risk_alerts` for anonymous/authenticated access, avoiding `select *` privilege denial on restricted base-table columns.
3. **Production Token Hardening:**
   - Enforced strict validation rejecting empty strings, whitespace-only tokens, default development tokens, and weak tokens (< 16 characters).
   - Validation errors strictly omit token candidate values to prevent logging credentials.
4. **Automated Verification:**
   - **Backend Test Suite:** **200 passed, 1 skipped** (0 failures across all suites).
   - **Compilation:** Clean byte compilation across all Python backend modules.
   - **Frontend Build:** Vite v5.4.21 compiled cleanly (2,135 modules transformed, 0 errors, 0 secrets).

---

## 2. Exact Files Modified

| File | Primary Modification |
|:---|:---|
| `backend/app/core/config.py` | Added authoritative `WORKERS` field with `WEB_CONCURRENCY` fallback; updated docstrings to disclaim complete container topology detection; corrected trigger endpoint to `/api/v1/ingestion/trigger`; hardened production token validation (reject empty, default, weak tokens < 16 chars without echoing secrets). |
| `backend/app/services/ingestion_scheduler.py` | Updated `start()` fail-safe check to evaluate `max(self.settings.WORKERS, detect_configured_workers(self.settings))` and reference `/api/v1/ingestion/trigger`. |
| `backend/app/main.py` | Added supported programmatic launcher `run_app()` / `if __name__ == "__main__":` which passes `settings.WORKERS` directly to Uvicorn. |
| `backend/Dockerfile` | Updated container `CMD` to use the authoritative production launcher: `CMD ["python", "-m", "app.main"]`. |
| `backend/app/db/supabase_client.py` | Updated `get_alerts()` and `get_alert_by_id()` to target `v_public_risk_alerts` for anonymous/authenticated access, preventing column permission errors. |
| `backend/app/services/alert_service.py` | Sanitized `description`, `resolved_reason`, and string fields in `evidence` using `sanitize_error_message()`; handled missing `fingerprint` fallback gracefully. |
| `backend/app/api/v1/routes/alerts.py` | Masked operator identity on public `GET /api/v1/alerts` and `GET /api/v1/alerts/{id}` endpoints (`acknowledged_by` masked to `"operator"`). |
| `database/migrations/005_risk_alerts_and_notifications.sql` | Added explicit `risk_alerts_severity_check` upgrade block; added `WITH (security_invoker = true)` to `v_public_risk_alerts`; added obsolete column grant revocation block; ensured `acknowledged_by` and `fingerprint` are never granted to `anon` or `authenticated`. |
| `docs/DEPLOYMENT.md` | Documented authoritative `WORKERS` configuration, supported launcher, corrected endpoint to `/api/v1/ingestion/trigger`, and explained multi-replica distributed coordination requirement. |
| `docs/MIGRATIONS.md` | Documented PostgreSQL 15+ `security_invoker = true` architecture, column privilege and RLS coordination, and explicit severity constraint upgrade. |
| `backend/tests/test_alerts.py` | Added regression tests for constraint upgrade SQL, `DATA_WARNING` enum acceptance, public endpoint operator masking, evidence sanitization, and `SupabaseClient` targeting `v_public_risk_alerts`. |
| `backend/tests/test_deployment_config.py` | Added regression tests for empty tokens, whitespace tokens, weak/short tokens (< 16 chars), and ensuring secrets are never echoed in errors. |
| `backend/tests/test_scheduler.py` | Added regression tests for authoritative worker resolution from `WEB_CONCURRENCY`, programmatic launcher interface, and `/api/v1/ingestion/trigger` endpoint references in errors. |

---

## 3. Scheduler Safety Architecture (Part A)

### 3.1 Authoritative Worker Configuration
- `Settings.WORKERS` serves as the single source of truth for process concurrency.
- If not explicitly configured, it inspects `os.environ.get("WEB_CONCURRENCY")` before defaulting to `1`.
- The production entrypoint `app.main:run_app()` launches Uvicorn programmatically, passing `workers=app_settings.WORKERS`.

### 3.2 Realistic Concurrency Safeguards & Limitations
- **Best-Effort Layer:** `detect_configured_workers()` inspects `sys.argv` (CLI flags) and common WSGI/ASGI environment variables as a defense-in-depth check.
- **Architectural Reality:** The application explicitly documents that inspecting process args and env vars cannot detect horizontal container replication (e.g. multiple Kubernetes pods or AWS ECS tasks behind a load balancer).
- **Cluster Rule:** In distributed deployments, `ENABLE_INGESTION_SCHEDULER` must remain `false` on all web workers. Automated ingestion must be triggered externally via `POST /api/v1/ingestion/trigger` using AWS EventBridge or a Linux cron sidecar.

---

## 4. Migration 005 Upgrade Strategy & Security Architecture (Part B)

### 4.1 Constraint Upgrade for Pre-Existing Tables
In PostgreSQL, executing `CREATE TABLE IF NOT EXISTS risk_alerts (...)` against an existing table does **not** alter existing check constraints. If a deployment already created `risk_alerts` before `DATA_WARNING` was added, subsequent insertions of `DATA_WARNING` would fail.

Migration 005 includes an idempotent PL/pgSQL block:
```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        ALTER TABLE public.risk_alerts 
            DROP CONSTRAINT IF EXISTS risk_alerts_severity_check;
        
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_severity_check 
            CHECK (severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'));
    END IF;
END $$;
```

### 4.2 Public View Security (`security_invoker = true`)
PostgreSQL 15 introduced `WITH (security_invoker = true)` for views. How the security model works:

```
[ Public Client / Anon Role ]
           │
           ▼
[ SELECT on v_public_risk_alerts ] (security_invoker = true)
           │
           ├─► 1. Evaluates Invoker's RLS Policy on risk_alerts
           │      `p_risk_alerts_read_public` -> ALLOW (true)
           │
           └─► 2. Evaluates Invoker's Column Privileges on risk_alerts
                  Columns in view: id, location_id, alert_type, severity, status,
                  title, description, evidence, created_at, updated_at,
                  acknowledged_at, resolved_at, resolved_reason -> ALL GRANTED
                  
                  Columns NOT in view: acknowledged_by, fingerprint -> WITHHELD
```

1. **Physical Exposure Prevention:** `acknowledged_by` is not projected by the view, nor is it granted to `anon` or `authenticated` on the underlying table.
2. **Direct Base-Table Protection:** If an anonymous client attempts `SELECT * FROM risk_alerts`, PostgREST requests all columns including `acknowledged_by`. Because `anon` lacks column privileges on `acknowledged_by`, PostgreSQL aborts the query with `ERROR: 42501 (permission denied for column acknowledged_by)`.
3. **Safe View Querying:** When querying `v_public_risk_alerts`, all requested columns are granted, allowing the public frontend to receive sanitized telemetry without errors.

### 4.3 Telemetry & Narrative Sanitization
`AlertService._to_alert_response` passes `description`, `resolved_reason`, and string keys in `evidence` through `sanitize_error_message()`. This redacts database connection strings (`postgresql://...`), JWT bearer tokens, table names, and raw exception messages.

---

## 5. Production Token Validation (Part C)

In `backend/app/core/config.py`, `validate_production_configuration` enforces:
1. **Empty / Whitespace Rejection:** Empty strings or spaces are rejected:
   `"Production safety violation: INGESTION_TRIGGER_TOKEN must not be empty or whitespace in production."`
2. **Default Development Secret Rejection:** The literal string `floodwatch-dev-secret-token` is rejected:
   `"Production safety violation: Default development secret token cannot be used for INGESTION_TRIGGER_TOKEN in production. Provide a dedicated random secret."`
3. **Entropy / Strength Minimum:** Tokens must be at least 16 characters long:
   `"Production safety violation: INGESTION_TRIGGER_TOKEN must be at least 16 characters long in production."`
4. **Zero Secret Leakage:** The candidate secret value is **never** printed in validation errors, console logs, or HTTP responses.
5. **Development Usability:** In `ENVIRONMENT="development"`, the default token remains functional without manual setup.

---

## 6. Full Regression Testing & Verification

### 6.1 Backend Test Results
Command executed: `backend\.venv\Scripts\python.exe -m pytest backend/tests -v`
- **Total Tests Collected:** 201
- **Passed:** 200
- **Skipped:** 1 (`test_live_supabase_connectivity` — designed to skip when remote database credentials are not present)
- **Failures:** 0
- **Duration:** 17.52s

```
======================= 200 passed, 1 skipped in 17.52s =======================
```

### 6.2 Python Bytecode Compilation
Command executed: `backend\.venv\Scripts\python.exe -m compileall backend`
- **Status:** Successful. 0 syntax errors, 0 compilation warnings.

### 6.3 Frontend Production Build
Command executed: `npm run build --prefix frontend`
- **Tool:** Vite v5.4.21
- **Transformed Modules:** 2,135
- **Assets Generated:**
  - `dist/index.html` (0.55 kB)
  - `dist/assets/index-Be9DzaUF.css` (51.61 kB)
  - `dist/assets/index-CrlQ4J4M.js` (888.48 kB)
- **Result:** Built in 18.73s. Zero secret occurrences found in output assets.

---

## 7. Remaining Manual Supabase Actions & Unresolved Limitations

### 7.1 Manual Supabase Actions (For Database Administrator)
In strict accordance with the **Zero Remote Execution Policy**, Migration 005 has been prepared locally on disk and tested via schema simulation. The database administrator should execute it manually:
1. Open the **Supabase Dashboard** -> Select Project -> **SQL Editor**.
2. Paste the contents of `database/migrations/005_risk_alerts_and_notifications.sql`.
3. Click **Run**.
4. Confirm post-migration health using:
   ```sql
   -- Verify public view and underlying table
   SELECT table_name, table_type 
   FROM information_schema.tables 
   WHERE table_schema = 'public' AND table_name IN ('risk_alerts', 'v_public_risk_alerts');

   -- Verify severity constraint includes DATA_WARNING
   SELECT conname, pg_get_constraintdef(oid) 
   FROM pg_constraint 
   WHERE conrelid = 'public.risk_alerts'::regclass AND conname = 'risk_alerts_severity_check';

   -- Verify acknowledged_by is withheld from anon
   SELECT column_name, grantee, privilege_type 
   FROM information_schema.column_privileges 
   WHERE table_name = 'risk_alerts' AND column_name = 'acknowledged_by';
   -- (Must return 0 rows for anon or authenticated)
   ```

### 7.2 Remaining Operational Limitations
1. **Multi-Replica Scheduler:** The in-process `asyncio.Lock()` protects against concurrent cycles within a single OS process. Multiple horizontal container replicas cannot share this in-process lock. In multi-replica production environments, `ENABLE_INGESTION_SCHEDULER` must remain `false`, and an external orchestrator (e.g., AWS EventBridge or a Linux cron sidecar) must call `POST /api/v1/ingestion/trigger` with the `X-Ingestion-Token` header.
2. **Live Database Connectivity:** Automated tests mock Supabase PostgREST endpoints. Real database operations require applying Migrations 001 through 005 via the Supabase SQL Editor.

---

## 8. Conclusion

All requested corrections—scheduler process configuration, launcher alignment, endpoint corrections, constraint upgrades, PostgreSQL 15+ view security, operator identity protection, and production token validation—have been fully implemented, documented, and verified across 200 automated tests.
