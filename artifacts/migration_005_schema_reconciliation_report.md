# FloodWatch — Migration 005 Schema Reconciliation Report

**Role:** Senior PostgreSQL/Supabase Database Migration Engineer & Python/FastAPI Architect  
**Project:** FloodWatch (Odisha Rainfall Monitoring & Decision-Support Platform)  
**Date:** November 23, 2026  
**Scope:** Investigation and definitive reconciliation of Migration 005 against established migrations (001–004), backend Pydantic schemas, service layer, frontend contracts, and authentication protocols.

---

## 1. Executive Summary & Verdict

### Final Verification Verdict: **APPROVED FOR MANUAL MIGRATION**

A critical review of the previous verification report revealed that its report text contained synthetic/alien SQL snippets that deviated from the established FloodWatch repository architecture. Specifically, the prior report text introduced imaginary table names (`pilot_locations`), imaginary column names (`pilot_location_id`, `trigger_condition`, `triggered_at`), an alien severity enum (`LOW`, `ADVISORY`, `MODERATE`, `SEVERE`, `EXTREME`), and non-existent configuration/authentication conventions (`API_KEY`, `X-API-Key`).

An exhaustive, evidence-based audit was executed across the actual repository:
1. **Migrations 001–004:** Proves conclusively that the spatial stations table is `monitoring_locations` (created in Migration 001, line 63) with primary key `id UUID`. The table `pilot_locations` does not exist in any migration or model.
2. **Backend Schemas & Models (`app.schemas.alert`):** Proves the canonical alert statuses are `NEW`, `ACTIVE`, `ACKNOWLEDGED`, `RESOLVED`; the canonical severity values are `INFO`, `DATA_WARNING`, `WARNING`, `HIGH`, `CRITICAL`; and the canonical alert types are `MODERATE_RAINFALL_RISK`, `HIGH_RAINFALL_RISK`, `VERY_HIGH_RAINFALL_RISK`, `DATA_UNAVAILABLE`, `DATA_STALE`, `RISK_REDUCED`.
3. **Database Client (`app.db.supabase_client`):** Queries and inserts records using `location_id`, `created_at`, `status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')`, and targets `v_public_risk_alerts` for anonymous callers and `risk_alerts` for service-role callers.
4. **Authentication (`app.core.config`, `app.api.v1.routes.ingestion`):** The administrative authentication contract strictly uses the `X-Ingestion-Token` HTTP header bound to `Settings.INGESTION_TRIGGER_TOKEN`. No `API_KEY` or `X-API-Key` exists in the codebase.
5. **Frontend (`AlertsPage.jsx`):** Sends `X-Ingestion-Token` for alert acknowledgements, filters statuses by `'OPEN'` (`NEW`/`ACTIVE`), `'ACKNOWLEDGED'`, and `'RESOLVED'`, and expects attributes `location_id`, `location_name`, `alert_type`, `severity`, `title`, `description`, `evidence`, `created_at`, `updated_at`.

Migration 005 has been reconciled, hardened, and verified to match the repository's architecture with dynamic constraint drop-and-replace PL/pgSQL logic.

---

## 2. Comprehensive Discrepancy & Reconciliation Matrix

| Architectural Element | Alien Report Formulation | Actual Established FloodWatch Repository | Reconciliation & Resolution |
| :--- | :--- | :--- | :--- |
| **Foreign Table Name** | `public.pilot_locations` | `public.monitoring_locations` (Migration 001) | **Restored:** Foreign key references `monitoring_locations(id)`. Table `pilot_locations` completely removed. |
| **Foreign Key Column** | `pilot_location_id` | `location_id UUID` | **Restored:** Column named `location_id UUID` throughout migration, schemas, API, and client. |
| **Alert Severity Values** | `LOW`, `ADVISORY`, `MODERATE`, `SEVERE`, `EXTREME` | `INFO`, `DATA_WARNING`, `WARNING`, `HIGH`, `CRITICAL` | **Restored:** Canonical 5-tier operational severity enum enforced via check constraint. |
| **Alert Lifecycle Statuses** | `ACTIVE`, `ACKNOWLEDGED`, `RESOLVED` (omitted `NEW`) | `NEW`, `ACTIVE`, `ACKNOWLEDGED`, `RESOLVED` | **Restored:** `status` includes default `'NEW'`, plus `ACTIVE`, `ACKNOWLEDGED`, `RESOLVED`. |
| **Title / Condition Column** | `trigger_condition TEXT` | `title VARCHAR(255)` & `description TEXT` | **Restored:** Matches `AlertResponse` model schema (`title` and `description`). |
| **Timestamp Columns** | `triggered_at TIMESTAMPTZ` | `created_at TIMESTAMPTZ` & `updated_at TIMESTAMPTZ` | **Restored:** Matches PostgreSQL standard timestamping and client expectations. |
| **Alert Deduplication** | Non-unique index on `(pilot_location_id, status)` | Partial Unique Index on `(location_id, fingerprint)` for open alerts | **Restored:** Unique partial index `uq_risk_alerts_active_fingerprint` prevents duplicate open alerts. |
| **Authentication Header** | `X-API-Key` | `X-Ingestion-Token` | **Restored:** Verification confirmed `X-Ingestion-Token` is authoritative; docs synchronized. |
| **Configuration Token** | `API_KEY` | `INGESTION_TRIGGER_TOKEN` | **Restored:** Verified `Settings` has `INGESTION_TRIGGER_TOKEN`; no invented `API_KEY` setting. |

---

## 3. Database Schema & Privilege Architecture (Migration 005)

### 3.1 Idempotent Constraint Upgrade (PostgreSQL 15+)
To guarantee that `DATA_WARNING` is accepted regardless of whether `risk_alerts` is created fresh or already exists from an earlier migration attempt, Migration 005 includes an anonymous PL/pgSQL block that dynamically discovers and drops any existing check constraints on `severity`, `status`, or `alert_type`, and establishes authoritative constraints:

```sql
DO $$
DECLARE
    c RECORD;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        -- 1. Upgrade severity check constraint (supporting DATA_WARNING)
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'severity'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_severity_check;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS chk_alert_severity;
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_severity_check 
            CHECK (severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'));

        -- 2. Upgrade status check constraint (supporting NEW, ACTIVE, ACKNOWLEDGED, RESOLVED)
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'status'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_status_check;
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_status_check 
            CHECK (status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'));

        -- 3. Upgrade alert_type check constraint
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'alert_type'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_alert_type_check;
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
```

### 3.2 Least-Privilege & Column Security
- **Table-Level Denial:** `REVOKE ALL ON TABLE public.risk_alerts FROM anon, authenticated, PUBLIC;`
- **Clean Obsolete Column Grants:** Scans `information_schema.column_privileges` and strips any previous grants on `risk_alerts` for public roles.
- **Selective Column Grants:** Grants `SELECT` exclusively on non-sensitive columns (`id`, `location_id`, `alert_type`, `severity`, `status`, `title`, `description`, `evidence`, `created_at`, `updated_at`, `acknowledged_at`, `resolved_at`, `resolved_reason`).
- **Sensitive Field Protection:** Neither `acknowledged_by` (operator identity) nor `fingerprint` (idempotency hash) is granted to `anon` or `authenticated`.
- **Sanitized Public View:** `v_public_risk_alerts` is created `WITH (security_invoker = true)` so PostgreSQL evaluates caller RLS policies and column permissions.

---

## 4. Verification & Testing Evidence

### 4.1 Backend Pytest Suite
A dedicated regression test suite (`test_alerts.py:17`) was added covering:
1. Strict schema reconciliation (validating `monitoring_locations`, `location_id`, canonical severities, no `pilot_locations`).
2. Public view column mapping and exclusion of `acknowledged_by` and `fingerprint`.
3. Authentication contract enforcing `X-Ingestion-Token` and `INGESTION_TRIGGER_TOKEN`.
4. Lifecycle status transitions and partial unique index deduplication.

**Pytest Execution Output:**
```text
============================= test session starts =============================
platform win32 -- Python 3.11.7, pytest-8.4.2, pluggy-1.7.0
rootdir: C:\Users\rsoum\Desktop\FloodWatch\backend
configfile: pyproject.toml
plugins: anyio-4.16.0, asyncio-0.26.0
collected 205 items

backend\tests\test_alerts.py ........................                    [ 11%]
backend\tests\test_deployment_config.py .................                [ 20%]
backend\tests\test_geospatial.py ....                                    [ 21%]
backend\tests\test_health.py ....s                                       [ 24%]
backend\tests\test_historical_rainfall.py ...................            [ 33%]
backend\tests\test_ingestion.py ........................................ [ 53%]
..........                                                               [ 58%]
backend\tests\test_monitoring.py ..................                      [ 66%]
backend\tests\test_public_dashboard.py .....                             [ 69%]
backend\tests\test_reporting.py ..............                           [ 76%]
backend\tests\test_risk_engine.py ..................                     [ 84%]
backend\tests\test_risk_explanation.py ......                            [ 87%]
backend\tests\test_scheduler.py ...................                      [ 97%]
backend\tests\test_weather.py ......                                     [100%]

======================= 204 passed, 1 skipped in 25.88s =======================
```
*(Single skipped test is an optional live remote network test requiring live Supabase credentials).*

### 4.2 Python Compilation & Frontend Build
- **Python Compilation:** `python -m compileall backend` compiled all 16 test files and application packages with 0 errors.
- **Frontend Production Build:** `npm run build --prefix frontend` built cleanly with Vite v5.4.21 (2,135 modules transformed, 0 errors).
- **Live Local Probes:** FastAPI backend (`127.0.0.1:8000`) returned HTTP 200 on `/api/v1/health` and `/api/v1/alerts`.

---

## 5. Corrected Migration 005 SQL Script

The complete, authoritative SQL script located at `database/migrations/005_risk_alerts_and_notifications.sql`:

```sql
-- FloodWatch Phase 2.7: Risk Alerts & In-App Notification System Migration
-- Target: PostgreSQL 15+ / Supabase with PostGIS

BEGIN;

SET LOCAL search_path = public, extensions;

-- 1. Create risk_alerts Table
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

-- 1.1 Explicit Migration for Existing Constraints (PostgreSQL 15+)
DO $$
DECLARE
    c RECORD;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        -- 1. Upgrade severity check constraint (supporting DATA_WARNING)
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'severity'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_severity_check;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS chk_alert_severity;
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_severity_check 
            CHECK (severity IN ('INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'));

        -- 2. Upgrade status check constraint (supporting NEW, ACTIVE, ACKNOWLEDGED, RESOLVED)
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'status'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_status_check;
        ALTER TABLE public.risk_alerts 
            ADD CONSTRAINT risk_alerts_status_check 
            CHECK (status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'));

        -- 3. Upgrade alert_type check constraint
        FOR c IN (
            SELECT conname
            FROM pg_constraint con
            JOIN pg_attribute att 
              ON att.attrelid = con.conrelid 
             AND att.attnum = ANY(con.conkey)
            WHERE con.conrelid = 'public.risk_alerts'::regclass
              AND con.contype = 'c'
              AND att.attname = 'alert_type'
        ) LOOP
            EXECUTE format('ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS %I', c.conname);
        END LOOP;
        ALTER TABLE public.risk_alerts DROP CONSTRAINT IF EXISTS risk_alerts_alert_type_check;
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

-- 2. Performance & Lifecycle Indexes
CREATE INDEX IF NOT EXISTS idx_risk_alerts_location_status 
    ON risk_alerts (location_id, status);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_status_created 
    ON risk_alerts (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_severity 
    ON risk_alerts (severity);

CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_alerts_active_fingerprint 
    ON risk_alerts (location_id, fingerprint) 
    WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED');

-- 3. Row-Level Security (RLS) Policies
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

-- 4. Least-Privilege Role Grants & Obsolete Privilege Revocation
REVOKE ALL ON TABLE public.risk_alerts FROM PUBLIC;
REVOKE ALL ON TABLE public.risk_alerts FROM anon;
REVOKE ALL ON TABLE public.risk_alerts FROM authenticated;

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT column_name, grantee 
        FROM information_schema.column_privileges 
        WHERE table_schema = 'public' 
          AND table_name = 'risk_alerts' 
          AND grantee IN ('anon', 'authenticated', 'public')
    ) LOOP
        EXECUTE format('REVOKE SELECT (%I) ON TABLE public.risk_alerts FROM %I', r.column_name, r.grantee);
    END LOOP;
END $$;

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

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.risk_alerts TO service_role;

-- 5. Sanitized Public Alert View (PostgreSQL 15+ security_invoker = true)
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

Once executed by the database administrator in the Supabase SQL Editor, run these validation queries to confirm schema integrity:

```sql
-- 1. Verify table exists and RLS is enabled
SELECT relname AS table_name, relrowsecurity AS rls_enabled
FROM pg_class
WHERE relname = 'risk_alerts' AND relnamespace = 'public'::regnamespace;

-- 2. Verify foreign key to monitoring_locations
SELECT
    tc.table_name, 
    kcu.column_name, 
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name 
FROM information_schema.table_constraints AS tc 
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
  AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY' 
  AND tc.table_name = 'risk_alerts'
  AND tc.table_schema = 'public';

-- 3. Verify severity check constraint accepts DATA_WARNING
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.risk_alerts'::regclass
  AND conname = 'risk_alerts_severity_check';

-- 4. Verify unique deduplication index
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'risk_alerts'
  AND indexname = 'uq_risk_alerts_active_fingerprint';

-- 5. Verify security_invoker on v_public_risk_alerts
SELECT 
    viewname,
    (SELECT reloptions FROM pg_class WHERE relname = 'v_public_risk_alerts' AND relnamespace = 'public'::regnamespace) AS options
FROM pg_views
WHERE schemaname = 'public' AND viewname = 'v_public_risk_alerts';

-- 6. Verify column permissions (acknowledged_by & fingerprint must be absent)
SELECT 
    grantee,
    column_name,
    privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name = 'risk_alerts'
  AND grantee IN ('anon', 'authenticated')
ORDER BY grantee, column_name;
```

### Expected Post-Migration Verification Results:
- Foreign key points from `risk_alerts.location_id` to `monitoring_locations.id`.
- Severity check constraint includes all 5 values: `'INFO', 'DATA_WARNING', 'WARNING', 'HIGH', 'CRITICAL'`.
- Status check constraint includes all 4 values: `'NEW', 'ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'`.
- Deduplication partial index is active on `(location_id, fingerprint)` for `NEW`, `ACTIVE`, and `ACKNOWLEDGED` alerts.
- Column privileges for `anon` and `authenticated` list exactly 13 non-sensitive columns. Neither `acknowledged_by` nor `fingerprint` is granted.
- View options show `{security_invoker=true}`.

---

## 7. Conclusion

All discrepancies have been resolved. Migration 005 is fully reconciled with the established FloodWatch architecture, database migrations 001–004, backend schemas, frontend pages, and authentication contracts. It is approved for manual migration by the database administrator.
