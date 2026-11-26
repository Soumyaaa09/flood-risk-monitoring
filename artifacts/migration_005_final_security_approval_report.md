# FloodWatch — Migration 005 Final Security Approval Report

**Role:** Senior PostgreSQL 15+ and Supabase Migration Engineer  
**Project:** FloodWatch (Odisha Rainfall Monitoring & Decision-Support Platform)  
**Date:** November 24, 2026  
**Scope:** Final security hardening of `database/migrations/005_risk_alerts_and_notifications.sql` addressing complete column privilege revocation, NULL-aware preflight validation, and dependency-safe view compatibility.

---

## 1. Executive Summary & Verdict

### Final Verification Verdict: **APPROVED FOR MANUAL MIGRATION**

All four technical findings have been resolved in `database/migrations/005_risk_alerts_and_notifications.sql` and verified against the backend schemas, test doubles, and the 210-item automated test suite.

| Hardening Dimension | Status | Implementation Details |
| :--- | :---: | :--- |
| **1. Complete Column Privilege Revocation** | **RESOLVED** | Dynamic loop revokes `ALL` column-level privileges (not just `SELECT`) for `PUBLIC`, `anon`, and `authenticated`. Bare keyword `PUBLIC` is used unquoted; `anon` and `authenticated` use quoted identifiers (`%I`). Defense-in-depth static revokes applied for `acknowledged_by` and `fingerprint`. |
| **2. NULL-Aware Preflight Data Validation** | **RESOLVED** | Added explicit `IS NULL` filters and count aggregation across `severity`, `status`, and `alert_type`. Aborts with `RAISE EXCEPTION` detailing total invalid rows, NULL count, and distinct invalid values. No data is silently mutated or deleted. |
| **3. Existing View Compatibility & Dependencies** | **RESOLVED** | Pre-checks existing `v_public_risk_alerts` schema against canonical contract. If schemas differ, inspects `pg_depend` for external dependent objects before dropping. Replaces cleanly when compatible or when 0 dependencies exist. Preserves `WITH (security_invoker = true)`. |
| **4. Preservation of Established Contracts** | **RESOLVED** | All foundational FloodWatch contracts (`monitoring_locations`, `location_id`, canonical alert types, 5 severity levels, 4 lifecycle statuses, `uq_risk_alerts_active_fingerprint`, and `X-Ingestion-Token` authentication) remain untouched. |
| **5. Comprehensive Automated Verification** | **RESOLVED** | **209 passed, 1 skipped** in 27.45s across the backend test suite; 0 Python compile errors; Vite frontend production build built cleanly in 12.16s (2,135 modules). |

---

## 2. In-Depth Technical Hardening

### 2.1 Complete Column-Level Privilege Revocation
- **The Defect in Prior Implementation:** The previous dynamic cleanup loop executed `REVOKE SELECT (%I) ...`. If column-level `INSERT`, `UPDATE`, or `REFERENCES` privileges had been granted in earlier prototypes or default configurations, those non-SELECT column privileges would have survived the migration.
- **The Hardened Solution:** The dynamic loop now executes `REVOKE ALL (%I) ON TABLE public.risk_alerts`:
  ```sql
  DO $$
  DECLARE
      r RECORD;
  BEGIN
      FOR r IN (
          SELECT DISTINCT column_name, grantee 
          FROM information_schema.column_privileges 
          WHERE table_schema = 'public' 
            AND table_name = 'risk_alerts' 
            AND grantee IN ('anon', 'authenticated', 'PUBLIC')
      ) LOOP
          IF r.grantee = 'PUBLIC' THEN
              -- Bare keyword PUBLIC (unquoted) revoking ALL column-level privileges
              EXECUTE format('REVOKE ALL (%I) ON TABLE public.risk_alerts FROM PUBLIC', r.column_name);
          ELSE
              -- Quoted role identifier for specific database roles (anon, authenticated) revoking ALL
              EXECUTE format('REVOKE ALL (%I) ON TABLE public.risk_alerts FROM %I', r.column_name, r.grantee);
          END IF;
      END LOOP;
  END $$;
  ```
- **Defense-in-Depth Static Revocations:**
  ```sql
  REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM PUBLIC;
  REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM anon;
  REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM authenticated;
  ```
- **Approved Re-Grants:** `SELECT` is granted strictly on the 13 approved non-sensitive columns to `anon` and `authenticated`. `service_role` retains its full table-level DML privileges (`SELECT, INSERT, UPDATE, DELETE`).

### 2.2 NULL-Aware Preflight Data Validation
- **The SQL Three-Valued Logic Nuance:** In SQL, `WHERE col NOT IN (...)` evaluates to `UNKNOWN` (falsy) when `col IS NULL`. Therefore, standard `NOT IN` clauses fail to match and fail to reject NULL values!
- **The Hardened Solution:** All three preflight checks explicitly check for `IS NULL`:
  ```sql
  SELECT 
      count(*),
      count(*) FILTER (WHERE severity IS NULL),
      string_agg(DISTINCT quote_literal(severity), ', ') FILTER (WHERE severity IS NOT NULL)
  INTO invalid_sev_count, null_sev_count, invalid_severity_vals
  FROM public.risk_alerts
  WHERE severity IS NULL 
     OR severity NOT IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL');

  IF invalid_sev_count > 0 THEN
      RAISE EXCEPTION 'Migration 005 preflight check failed: Column public.risk_alerts.severity contains % incompatible row(s) (% NULL values, invalid non-null values: [%]). Canonical values are (INFO, DATA_WARNING, WARNING, HIGH, CRITICAL). Existing records were preserved. Reconcile invalid data before re-running migration.', 
          invalid_sev_count, null_sev_count, COALESCE(invalid_severity_vals, 'none');
  END IF;
  ```
  The exact same structure is enforced for `status` and `alert_type`. If any row contains a NULL or an out-of-set value, the migration aborts immediately with an actionable diagnostic. Existing records are never deleted or modified.

### 2.3 Existing View Compatibility & Dependencies
- **The View Upgrade Nuance:** In PostgreSQL, `CREATE OR REPLACE VIEW` cannot drop, rename, or reorder columns from an existing view definition. Furthermore, blindly executing `DROP VIEW ... CASCADE` could destroy dependent views or reporting routines.
- **The Hardened Solution:** An explicit pre-check compares the existing view's column schema in ordinal order against the canonical contract:
  ```sql
  DO $$
  DECLARE
      target_cols TEXT[] := ARRAY[
          'id', 'location_id', 'alert_type', 'severity', 'status',
          'title', 'description', 'evidence', 'created_at', 'updated_at',
          'acknowledged_at', 'resolved_at', 'resolved_reason'
      ];
      existing_cols TEXT[];
      dep_count INT := 0;
  BEGIN
      IF EXISTS (
          SELECT 1 FROM pg_views 
          WHERE schemaname = 'public' AND viewname = 'v_public_risk_alerts'
      ) THEN
          SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
          INTO existing_cols
          FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts';

          IF existing_cols IS DISTINCT FROM target_cols THEN
              -- Check for external dependent objects before dropping
              SELECT count(*)
              INTO dep_count
              FROM pg_depend d
              JOIN pg_rewrite r ON r.oid = d.objid
              JOIN pg_class c ON c.oid = r.ev_class
              WHERE d.refobjid = 'public.v_public_risk_alerts'::regclass
                AND c.oid != 'public.v_public_risk_alerts'::regclass;

              IF dep_count > 0 THEN
                  RAISE EXCEPTION 'Migration 005: Existing view public.v_public_risk_alerts column schema differs from canonical contract and has % dependent object(s). Drop or update dependent objects before re-running migration.', dep_count;
              ELSE
                  DROP VIEW public.v_public_risk_alerts;
              END IF;
          END IF;
      END IF;
  END $$;
  ```
  - When the column schema matches: `CREATE OR REPLACE VIEW` executes smoothly in place, preserving dependencies.
  - When columns differ and dependencies exist: Aborts with a clear error preventing accidental cascading drops.
  - When columns differ and 0 dependencies exist: Safely drops the obsolete view so the canonical view can be created.
  - Final view definition is created `WITH (security_invoker = true)` to enforce caller RLS and column permissions.

---

## 3. Final Role Privilege Matrix

| Database Role | Table Privileges (`risk_alerts`) | Column Privileges (`risk_alerts`) | View Privileges (`v_public_risk_alerts`) |
| :--- | :---: | :---: | :---: |
| **`PUBLIC`** | **NONE** (Revoked) | **NONE** (ALL column privileges revoked) | **NONE** |
| **`anon`** | **NONE** (Revoked) | `SELECT` on 13 approved columns only. `acknowledged_by` & `fingerprint` **DENIED**. | `SELECT` |
| **`authenticated`** | **NONE** (Revoked) | `SELECT` on 13 approved columns only. `acknowledged_by` & `fingerprint` **DENIED**. | `SELECT` |
| **`service_role`** | `SELECT, INSERT, UPDATE, DELETE` | Full column access | `SELECT` |

---

## 4. Verification & Testing Evidence

### 4.1 Testing Methodology & Environment Boundaries
- **Host Environment:** Local machine has no active PostgreSQL daemon or Docker engine.
- **Classification of Verification:**
  - **Static AST & SQL Structural Verification:** Validates that Migration 005 uses explicit constraint drops, handles `PUBLIC` unquoted, references `monitoring_locations`, revokes `ALL` column privileges, and enforces NULL checks.
  - **Simulated State & Lifecycle Testing:** Python unit and integration tests simulate constraint retention, NULL-aware preflight validation, dependency checking, and idempotency across fresh and repeated executions.
  - **Remote Execution Notice:** As per strict boundaries, live SQL execution was NOT performed against remote Supabase. The database administrator must execute Migration 005 manually using the verified script in Section 5.

### 4.2 Automated Test Suite Results
All 210 backend tests executed:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.7, pytest-8.4.2, pluggy-1.7.0
rootdir: C:\Users\rsoum\Desktop\FloodWatch\backend
configfile: pyproject.toml
plugins: anyio-4.16.0, asyncio-0.26.0
collected 210 items

backend\tests\test_alerts.py .............................               [ 13%]
backend\tests\test_deployment_config.py .................                [ 21%]
backend\tests\test_geospatial.py ....                                    [ 23%]
backend\tests\test_health.py ....s                                       [ 26%]
backend\tests\test_historical_rainfall.py ...................            [ 35%]
backend\tests\test_ingestion.py ........................................ [ 54%]
..........                                                               [ 59%]
backend\tests\test_monitoring.py ..................                      [ 67%]
backend\tests\test_public_dashboard.py .....                             [ 70%]
backend\tests\test_reporting.py ..............                           [ 76%]
backend\tests\test_risk_engine.py ..................                     [ 85%]
backend\tests\test_risk_explanation.py ......                            [ 88%]
backend\tests\test_scheduler.py ...................                      [ 97%]
backend\tests\test_weather.py ......                                     [100%]

======================= 209 passed, 1 skipped in 27.45s =======================
```
*(Single skipped test is an optional live remote network test requiring live Supabase credentials).*

### 4.3 Python Compilation & Frontend Build
- **Python Compilation:** `python -m compileall backend` passed with 0 errors.
- **Frontend Production Build:** `npm run build --prefix frontend` built cleanly with Vite v5.4.21 (2,135 modules in 12.16s).
- **Live Local Servers:** FastAPI backend (`127.0.0.1:8000`) and Vite frontend (`127.0.0.1:5173`) running smoothly; probes to `/api/v1/health` and `/api/v1/alerts` returned HTTP 200 OK.

---

## 5. Authoritative Migration 005 SQL Script

The complete, authoritative SQL script located at `database/migrations/005_risk_alerts_and_notifications.sql`:

```sql
-- FloodWatch Phase 2.7: Risk Alerts & In-App Notification System Migration
-- Target: PostgreSQL 15+ / Supabase with PostGIS

BEGIN;

SET LOCAL search_path = public, extensions;

-- ---------------------------------------------------------------------
-- 1. Create risk_alerts Table
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID REFERENCES monitoring_locations(id) ON DELETE CASCADE NOT NULL,
    alert_type VARCHAR(50) NOT NULL CONSTRAINT risk_alerts_alert_type_check CHECK (
        alert_type IN (
            'MODERATE_RAINFALL_RISK',
            'HIGH_RAINFALL_RISK',
            'VERY_HIGH_RAINFALL_RISK',
            'DATA_UNAVAILABLE',
            'DATA_STALE',
            'RISK_REDUCED'
        )
    ),
    severity VARCHAR(30) NOT NULL CONSTRAINT risk_alerts_severity_check CHECK (
        severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL')
    ),
    status VARCHAR(30) NOT NULL DEFAULT 'NEW' CONSTRAINT risk_alerts_status_check CHECK (
        status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED')
    ),
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    evidence JSONB DEFAULT '{}'::jsonb NOT NULL,
    fingerprint VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    resolved_reason TEXT
);

-- ---------------------------------------------------------------------
-- 1.1 Targeted Constraint Migration & NULL-Aware Preflight Validation
-- ---------------------------------------------------------------------
DO $$
DECLARE
    invalid_sev_count INT := 0;
    null_sev_count INT := 0;
    invalid_severity_vals TEXT;

    invalid_st_count INT := 0;
    null_st_count INT := 0;
    invalid_status_vals TEXT;

    invalid_tp_count INT := 0;
    null_tp_count INT := 0;
    invalid_type_vals TEXT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        -- Step A: NULL-aware inspection for severity column
        SELECT 
            count(*),
            count(*) FILTER (WHERE severity IS NULL),
            string_agg(DISTINCT quote_literal(severity), ', ') FILTER (WHERE severity IS NOT NULL)
        INTO invalid_sev_count, null_sev_count, invalid_severity_vals
        FROM public.risk_alerts
        WHERE severity IS NULL 
           OR severity NOT IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL');

        IF invalid_sev_count > 0 THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Column public.risk_alerts.severity contains % incompatible row(s) (% NULL values, invalid non-null values: [%]). Canonical values are (INFO, DATA_WARNING, WARNING, HIGH, CRITICAL). Existing records were preserved. Reconcile invalid data before re-running migration.', 
                invalid_sev_count, null_sev_count, COALESCE(invalid_severity_vals, 'none');
        END IF;

        -- Step B: NULL-aware inspection for status column
        SELECT 
            count(*),
            count(*) FILTER (WHERE status IS NULL),
            string_agg(DISTINCT quote_literal(status), ', ') FILTER (WHERE status IS NOT NULL)
        INTO invalid_st_count, null_st_count, invalid_status_vals
        FROM public.risk_alerts
        WHERE status IS NULL 
           OR status NOT IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED');

        IF invalid_st_count > 0 THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Column public.risk_alerts.status contains % incompatible row(s) (% NULL values, invalid non-null values: [%]). Canonical values are (NEW, ACTIVE, ACKNOWLEDGED, RESOLVED). Existing records were preserved. Reconcile invalid data before re-running migration.', 
                invalid_st_count, null_st_count, COALESCE(invalid_status_vals, 'none');
        END IF;

        -- Step C: NULL-aware inspection for alert_type column
        SELECT 
            count(*),
            count(*) FILTER (WHERE alert_type IS NULL),
            string_agg(DISTINCT quote_literal(alert_type), ', ') FILTER (WHERE alert_type IS NOT NULL)
        INTO invalid_tp_count, null_tp_count, invalid_type_vals
        FROM public.risk_alerts
        WHERE alert_type IS NULL 
           OR alert_type NOT IN (
               'MODERATE_RAINFALL_RISK',
               'HIGH_RAINFALL_RISK',
               'VERY_HIGH_RAINFALL_RISK',
               'DATA_UNAVAILABLE',
               'DATA_STALE',
               'RISK_REDUCED'
           );

        IF invalid_tp_count > 0 THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Column public.risk_alerts.alert_type contains % incompatible row(s) (% NULL values, invalid non-null values: [%]). Canonical values are (MODERATE_RAINFALL_RISK, HIGH_RAINFALL_RISK, VERY_HIGH_RAINFALL_RISK, DATA_UNAVAILABLE, DATA_STALE, RISK_REDUCED). Existing records were preserved. Reconcile invalid data before re-running migration.', 
                invalid_tp_count, null_tp_count, COALESCE(invalid_type_vals, 'none');
        END IF;

        -- Step D: Drop ONLY explicitly identified legacy and canonical constraint names.
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_severity_check;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS chk_alert_severity;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_status_check;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_alert_type_check;

        -- Step E: Re-add canonical check constraints
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_severity_check 
            CHECK (severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'));

        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_status_check 
            CHECK (status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'));

        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_alert_type_check 
            CHECK (alert_type IN (
                'MODERATE_RAINFALL_RISK',
                'HIGH_RAINFALL_RISK',
                'VERY_HIGH_RAINFALL_RISK',
                'DATA_UNAVAILABLE',
                'DATA_STALE',
                'RISK_REDUCED'
            ));
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2. Performance & Lifecycle Indexes
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_risk_alerts_location_status 
    ON risk_alerts (location_id, status);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_status_created 
    ON risk_alerts (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_severity 
    ON risk_alerts (severity);

CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_alerts_active_fingerprint 
    ON risk_alerts (location_id, fingerprint) 
    WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED');

-- ---------------------------------------------------------------------
-- 3. Row-Level Security (RLS) Policies
-- ---------------------------------------------------------------------
ALTER TABLE risk_alerts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_risk_alerts_read_public ON risk_alerts;
DROP POLICY IF EXISTS p_risk_alerts_service_role_all ON risk_alerts;

CREATE POLICY p_risk_alerts_read_public ON risk_alerts
    FOR SELECT
    TO anon, authenticated
    USING (true);

CREATE POLICY p_risk_alerts_service_role_all ON risk_alerts
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ---------------------------------------------------------------------
-- 4. Least-Privilege Role Grants & Complete Column Privilege Revocation
-- ---------------------------------------------------------------------
-- 4.1 Table-level revocation from public roles
REVOKE ALL ON TABLE public.risk_alerts FROM PUBLIC;
REVOKE ALL ON TABLE public.risk_alerts FROM anon;
REVOKE ALL ON TABLE public.risk_alerts FROM authenticated;

-- 4.2 Complete column-level cleanup: Strip ALL pre-existing column grants
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT DISTINCT column_name, grantee 
        FROM information_schema.column_privileges 
        WHERE table_schema = 'public' 
          AND table_name = 'risk_alerts' 
          AND grantee IN ('anon', 'authenticated', 'PUBLIC')
    ) LOOP
        IF r.grantee = 'PUBLIC' THEN
            -- Bare keyword PUBLIC (unquoted) revoking ALL column-level privileges
            EXECUTE format('REVOKE ALL (%I) ON TABLE public.risk_alerts FROM PUBLIC', r.column_name);
        ELSE
            -- Quoted role identifier for specific database roles (anon, authenticated) revoking ALL
            EXECUTE format('REVOKE ALL (%I) ON TABLE public.risk_alerts FROM %I', r.column_name, r.grantee);
        END IF;
    END LOOP;
END $$;

-- 4.3 Explicit static column-level cleanup for sensitive fields as defense-in-depth
REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM PUBLIC;
REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM anon;
REVOKE ALL (acknowledged_by, fingerprint) ON TABLE public.risk_alerts FROM authenticated;

-- 4.4 Column-level SELECT strictly for approved non-sensitive columns
GRANT SELECT (
    id,
    location_id,
    alert_type,
    severity,
    status,
    title,
    description,
    evidence,
    created_at,
    updated_at,
    acknowledged_at,
    resolved_at,
    resolved_reason
) ON TABLE public.risk_alerts TO anon;

GRANT SELECT (
    id,
    location_id,
    alert_type,
    severity,
    status,
    title,
    description,
    evidence,
    created_at,
    updated_at,
    acknowledged_at,
    resolved_at,
    resolved_reason
) ON TABLE public.risk_alerts TO authenticated;

-- Service role receives full operational table DML privileges
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.risk_alerts TO service_role;

-- ---------------------------------------------------------------------
-- 5. Sanitized Public Alert View (PostgreSQL 15+ security_invoker = true)
-- ---------------------------------------------------------------------
-- 5.1 Safe View Upgrade Check
DO $$
DECLARE
    target_cols TEXT[] := ARRAY[
        'id', 'location_id', 'alert_type', 'severity', 'status',
        'title', 'description', 'evidence', 'created_at', 'updated_at',
        'acknowledged_at', 'resolved_at', 'resolved_reason'
    ];
    existing_cols TEXT[];
    dep_count INT := 0;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_views 
        WHERE schemaname = 'public' AND viewname = 'v_public_risk_alerts'
    ) THEN
        SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
        INTO existing_cols
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts';

        IF existing_cols IS DISTINCT FROM target_cols THEN
            SELECT count(*)
            INTO dep_count
            FROM pg_depend d
            JOIN pg_rewrite r ON r.oid = d.objid
            JOIN pg_class c ON c.oid = r.ev_class
            WHERE d.refobjid = 'public.v_public_risk_alerts'::regclass
              AND c.oid != 'public.v_public_risk_alerts'::regclass;

            IF dep_count > 0 THEN
                RAISE EXCEPTION 'Migration 005: Existing view public.v_public_risk_alerts column schema differs from canonical contract and has % dependent object(s). Drop or update dependent objects before re-running migration.', dep_count;
            ELSE
                DROP VIEW public.v_public_risk_alerts;
            END IF;
        END IF;
    END IF;
END $$;

-- 5.2 Create canonical sanitized view
CREATE OR REPLACE VIEW public.v_public_risk_alerts 
WITH (security_invoker = true) AS
SELECT
    id,
    location_id,
    alert_type,
    severity,
    status,
    title,
    description,
    evidence,
    created_at,
    updated_at,
    acknowledged_at,
    resolved_at,
    resolved_reason
FROM public.risk_alerts;

GRANT SELECT ON public.v_public_risk_alerts TO anon;
GRANT SELECT ON public.v_public_risk_alerts TO authenticated;
GRANT SELECT ON public.v_public_risk_alerts TO service_role;

COMMIT;
```

---

## 6. Post-Migration DBA Validation Queries

Execute the following queries in the Supabase SQL Editor to verify complete security hardening:

```sql
-- 1. Verify table exists and RLS is active
SELECT relname AS table_name, relrowsecurity AS rls_enabled
FROM pg_class
WHERE relname = 'risk_alerts' AND relnamespace = 'public'::regnamespace;

-- 2. Verify all check constraints on risk_alerts
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.risk_alerts'::regclass AND contype = 'c'
ORDER BY conname;

-- 3. Verify deduplication partial unique index
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'risk_alerts'
  AND indexname = 'uq_risk_alerts_active_fingerprint';

-- 4. Verify security_invoker option on public view
SELECT 
    viewname,
    (SELECT reloptions FROM pg_class WHERE relname = 'v_public_risk_alerts' AND relnamespace = 'public'::regnamespace) AS options
FROM pg_views
WHERE schemaname = 'public' AND viewname = 'v_public_risk_alerts';

-- 5. Verify column permissions (NO non-SELECT privileges; acknowledged_by & fingerprint MUST NOT be present)
SELECT 
    grantee,
    column_name,
    privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name = 'risk_alerts'
  AND grantee IN ('anon', 'authenticated', 'PUBLIC')
ORDER BY grantee, column_name, privilege_type;
```

---

## 7. Remaining Limitations & Operating Notes

1. **Manual Supabase Execution:** In accordance with safety policies, this migration was not executed automatically over the network against remote Supabase. A database administrator must apply it manually in the Supabase SQL Editor.
2. **Pre-Existing Data Incompatibility:** If a remote test database already contains NULLs or invalid values in `severity`, `status`, or `alert_type`, the migration will halt cleanly via `RAISE EXCEPTION`. The operator must resolve or backfill the invalid records before re-running the migration.
3. **No Unrelated Schema Changes:** Basins, districts, monitoring locations, ingestion runs, and observations remain completely untouched.
