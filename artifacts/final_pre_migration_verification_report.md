# FloodWatch — Final Pre-Migration Verification Report

**Role:** Senior PostgreSQL/Supabase Security Engineer & Python/FastAPI Architect  
**Project:** FloodWatch (Odisha Rainfall Monitoring & Decision-Support Platform)  
**Date:** November 23, 2026  
**Scope:** Final read-only code, database security, test suite, and operational verification before applying `database/migrations/005_risk_alerts_and_notifications.sql`.

---

## 1. Executive Summary & Verdict

### Final Verification Verdict: **APPROVED FOR MANUAL MIGRATION**

All targeted architectural, concurrency, security, and schema safety remediations have been verified against the active source code, automated test suites, and documentation. No blocking vulnerabilities or regressions remain.

| Verification Dimension | Status | Key Evidence |
| :--- | :---: | :--- |
| **Scheduler Multi-Worker Safety** | **VERIFIED** | `Settings.WORKERS` authoritative resolution with `WEB_CONCURRENCY` fallback; launcher binds `workers=app_settings.WORKERS`; startup blocks multi-worker activation; multi-replica external scheduling documented with `/api/v1/ingestion/trigger`. |
| **Migration 005 Schema Safety** | **VERIFIED** | Idempotent `ALTER TABLE` check constraint upgrade via anonymous PL/pgSQL block; `WITH (security_invoker = true)` on `v_public_risk_alerts`; base column grants configured for invoker view; `acknowledged_by` & `fingerprint` inaccessible to `anon`/`authenticated`. |
| **Data Exposure & Sanitization** | **VERIFIED** | Public alert routes mask operator identities to `"operator"`; `AlertService._to_alert_response` sanitizes narrative and evidence fields against credential/connection-string leaks; deduplication fingerprints protected. |
| **Secret & Token Validation** | **VERIFIED** | Strict validation in `validate_production_configuration()` rejects defaults, empty/whitespace strings, and tokens < 16 chars without echoing secret values in logs or exceptions. |
| **Automated Test Suite** | **VERIFIED** | **200 passed, 1 skipped** in 17.52s across backend pytest suite; 0 Python syntax/compilation errors; frontend Vite production build succeeds cleanly (2,135 modules). |

---

## 2. In-Depth Component Verification

### 2.1 Scheduler & Concurrency Verification
- **Authoritative Worker Count:** In `backend/app/core/config.py`, `Settings.WORKERS` uses `default_factory=lambda: int(os.environ.get("WORKERS") or os.environ.get("WEB_CONCURRENCY") or 1)`. When `WORKERS` is omitted, `WEB_CONCURRENCY` is honored.
- **Precedence & Validation:** `detect_configured_workers(settings)` parses runtime environment and CLI parameters (`--workers`, `-w`). Any multi-worker setting (> 1) causes `validate_scheduler_worker_concurrency()` to raise a clear `RuntimeError` if `SCHEDULER_ENABLED=True`.
- **Production Launcher Synchronization:** `backend/app/main.py:run_app()` invokes `uvicorn.run("app.main:app", host=..., port=..., workers=app_settings.WORKERS, ...)`. The process manager receives the validated worker count.
- **Fail-Safe Startup Guard:** `backend/app/services/ingestion_scheduler.py:start()` performs an independent check comparing `max(self.settings.WORKERS, detect_configured_workers(self.settings)) > 1` and aborts scheduler activation if multi-worker concurrency is detected.
- **Multi-Replica Decoupling:** Documentation in `docs/DEPLOYMENT.md` explicitly mandates external scheduling (e.g., AWS EventBridge, CloudWatch Cron, or Kubernetes CronJob) invoking `POST /api/v1/ingestion/trigger` with `X-API-Key` authentication for multi-container deployments.

### 2.2 Migration 005 & Database Security Verification
- **Idempotent Constraint Upgrades:** In `database/migrations/005_risk_alerts_and_notifications.sql`, the severity constraint replacement uses a safe anonymous PL/pgSQL block:
  ```sql
  DO $$
  BEGIN
      IF EXISTS (
          SELECT 1 FROM pg_constraint
          WHERE conname = 'chk_alert_severity'
            AND conrelid = 'public.risk_alerts'::regclass
      ) THEN
          ALTER TABLE public.risk_alerts DROP CONSTRAINT chk_alert_severity;
      END IF;
      ALTER TABLE public.risk_alerts
          ADD CONSTRAINT chk_alert_severity
          CHECK (severity IN ('LOW', 'ADVISORY', 'MODERATE', 'SEVERE', 'EXTREME'));
  END $$;
  ```
- **PostgreSQL 15+ Security Invoker View:** The public view `public.v_public_risk_alerts` is created `WITH (security_invoker = true)`. This forces PostgreSQL to evaluate Row Level Security and column-level privileges of the requesting database user (`anon` or `authenticated`), preventing privilege escalation.
- **Underlying Column Grants for Invoker Views:** Column grants on the underlying `public.risk_alerts` table are explicitly provided for `anon` and `authenticated`:
  ```sql
  GRANT SELECT (
      id, pilot_location_id, alert_type, severity, trigger_condition,
      description, evidence, status, triggered_at, updated_at,
      resolved_at, resolved_reason
  ) ON TABLE public.risk_alerts TO anon, authenticated;
  ```
  This ensures `v_public_risk_alerts` queries succeed under PostgREST without permission errors, while strictly denying access to `acknowledged_by` and `fingerprint`.

### 2.3 Data Exposure & Alert Service Verification
- **Operator Masking:** Public endpoints (`/api/v1/alerts` and `/api/v1/alerts/{alert_id}`) mask `acknowledged_by`:
  ```python
  if alert.acknowledged_by:
      alert.acknowledged_by = "operator"
  ```
  Only internal operators and authenticated operator endpoints receive true operator IDs.
- **Narrative & Evidence Sanitization:** `AlertService._to_alert_response` passes `description`, `resolved_reason`, and string values in `evidence` through `sanitize_error_message()`, stripping database connection strings, JWTs, credentials, and internal host details.
- **Database Client Entity Targeting:** `SupabaseClient.get_alerts()` dynamically queries `risk_alerts` when using the privileged `service_role_key` and queries `v_public_risk_alerts` when using the public anon key.

### 2.4 Secret & Token Validation
- `validate_production_configuration()` in `backend/app/core/config.py` enforces:
  - `SUPABASE_SERVICE_ROLE_KEY`: Rejects default development tokens, empty/whitespace strings, and strings < 16 characters.
  - `API_KEY`: Rejects default development keys, empty/whitespace strings, and strings < 16 characters.
  - Rejection messages never echo candidate or invalid secret values into log streams or exceptions.

---

## 3. Test Suite & Operational Verification

### 3.1 Automated Test Execution Results
All automated test suites executed against Python 3.12:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.3, pytest-8.3.4, pluggy-1.5.0
rootdir: C:\Users\rsoum\Desktop\FloodWatch\backend
configfile: pyproject.toml
collected 201 items

tests/test_alerts.py ................................................... [ 25%]
....................                                                     [ 35%]
tests/test_api.py .............                                          [ 41%]
tests/test_confidence.py .....                                           [ 44%]
tests/test_historical.py .................                               [ 53%]
tests/test_ingestion.py .............                                    [ 59%]
tests/test_production_config.py ................                         [ 67%]
tests/test_reporting.py ................                                 [ 75%]
tests/test_risk_engine.py .........................                      [ 88%]
tests/test_scheduler.py ........................s                        [100%]

======================== 200 passed, 1 skipped in 17.52s ========================
```
*(Single skipped test is an optional live remote network test requiring live Supabase credentials).*

### 3.2 Frontend Build & Compilation Verification
- **Python Compilation:** `python -m compileall backend` passed with 0 errors.
- **Frontend Production Build:** `npm run build --prefix frontend` built cleanly with Vite v5.4.21 (2,135 modules transformed, total bundle size ~593 kB gzip: 168 kB).
- **Live Local Servers:** FastAPI backend (`127.0.0.1:8000`) and Vite frontend (`127.0.0.1:5173`) running smoothly; live probes to `/api/v1/health` and `/api/v1/alerts` returned HTTP 200 OK.

---

## 4. Manual Migration Execution Instructions

The database administrator should execute Migration 005 directly in the Supabase SQL Editor.

### Step 1: Open Supabase Project
1. Navigate to the Supabase Project Dashboard.
2. Select **SQL Editor** from the left-hand navigation panel.
3. Click **New query**.

### Step 2: Paste and Execute Migration 005
Copy the exact contents of `database/migrations/005_risk_alerts_and_notifications.sql`:

```sql
-- Migration 005: Risk Alerts & In-App Notification Management
-- Adds risk_alerts table, notification state tracking, and public view

-- 1. Create table if not exists
CREATE TABLE IF NOT EXISTS public.risk_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pilot_location_id VARCHAR(50) NOT NULL REFERENCES public.pilot_locations(id) ON DELETE CASCADE,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    trigger_condition TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED')),
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    resolved_reason TEXT
);

-- Idempotent check constraint upgrade for severity
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'chk_alert_severity' 
          AND conrelid = 'public.risk_alerts'::regclass
    ) THEN
        ALTER TABLE public.risk_alerts DROP CONSTRAINT chk_alert_severity;
    END IF;
    ALTER TABLE public.risk_alerts
        ADD CONSTRAINT chk_alert_severity 
        CHECK (severity IN ('LOW', 'ADVISORY', 'MODERATE', 'SEVERE', 'EXTREME'));
END $$;

-- 2. Indexes for deduplication, filtering, and queries
CREATE INDEX IF NOT EXISTS idx_risk_alerts_fingerprint_status 
    ON public.risk_alerts (fingerprint, status);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_location_status 
    ON public.risk_alerts (pilot_location_id, status);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_triggered_at 
    ON public.risk_alerts (triggered_at DESC);

-- 3. Row Level Security
ALTER TABLE public.risk_alerts ENABLE ROW LEVEL SECURITY;

-- Service role has full access
DROP POLICY IF EXISTS "Service role full access on risk_alerts" ON public.risk_alerts;
CREATE POLICY "Service role full access on risk_alerts"
    ON public.risk_alerts
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Public / anon / authenticated read access to non-sensitive columns
DROP POLICY IF EXISTS "Public read access on risk_alerts" ON public.risk_alerts;
CREATE POLICY "Public read access on risk_alerts"
    ON public.risk_alerts
    FOR SELECT
    TO anon, authenticated
    USING (true);

-- 4. Clean up any obsolete table-level grants
REVOKE ALL ON TABLE public.risk_alerts FROM anon, authenticated;

-- Grant column-level SELECT on non-sensitive columns
GRANT SELECT (
    id, pilot_location_id, alert_type, severity, trigger_condition,
    description, evidence, status, triggered_at, updated_at,
    resolved_at, resolved_reason
) ON TABLE public.risk_alerts TO anon, authenticated;

-- 5. Public View excluding sensitive fields (security_invoker = true)
DROP VIEW IF EXISTS public.v_public_risk_alerts;
CREATE VIEW public.v_public_risk_alerts 
WITH (security_invoker = true) AS
SELECT
    id,
    pilot_location_id,
    alert_type,
    severity,
    trigger_condition,
    description,
    evidence,
    status,
    triggered_at,
    updated_at,
    resolved_at,
    resolved_reason
FROM public.risk_alerts;

GRANT SELECT ON public.v_public_risk_alerts TO anon, authenticated;
```

Click **Run** (or `Ctrl+Enter`). Verify the query executes with `Success. No rows returned`.

---

## 5. Post-Migration Validation Queries

Run the following validation query block in the Supabase SQL Editor to verify tables, views, constraints, and column-level privileges:

```sql
-- 1. Verify table existence and RLS status
SELECT 
    relname AS table_name,
    relrowsecurity AS rls_enabled
FROM pg_class
WHERE relname = 'risk_alerts'
  AND relnamespace = 'public'::regnamespace;

-- 2. Verify severity check constraint
SELECT 
    conname, 
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.risk_alerts'::regclass
  AND conname = 'chk_alert_severity';

-- 3. Verify public view definition and security_invoker flag
SELECT 
    viewname,
    viewowner,
    (SELECT reloptions FROM pg_class WHERE relname = 'v_public_risk_alerts') AS options
FROM pg_views
WHERE schemaname = 'public' 
  AND viewname = 'v_public_risk_alerts';

-- 4. Verify anon/authenticated column permissions (acknowledged_by & fingerprint MUST NOT appear)
SELECT 
    table_name,
    column_name,
    privilege_type,
    grantee
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name = 'risk_alerts'
  AND grantee IN ('anon', 'authenticated')
ORDER BY column_name;
```

### Expected Post-Migration Output:
1. `table_name`: `risk_alerts`, `rls_enabled`: `true`.
2. `chk_alert_severity`: `CHECK (severity::text = ANY (ARRAY['LOW'::character varying, 'ADVISORY'::character varying, 'MODERATE'::character varying, 'SEVERE'::character varying, 'EXTREME'::character varying]::text[]))`.
3. `options`: `{security_invoker=true}`.
4. `column_privileges`: Exactly 12 columns listed per grantee (`anon`, `authenticated`). Neither `acknowledged_by` nor `fingerprint` appears in the output.

---

## 6. Conclusion

Migration 005 is safe, idempotent, and production-ready. The application layer cleanly complements the database security architecture through sanitization, fallback querying, and worker concurrency safeguards. Deployment may proceed with manual application of Migration 005.
