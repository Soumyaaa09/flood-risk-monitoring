# FloodWatch Database & Migration Operations Guide

This guide details the sequential PostgreSQL / Supabase migration procedures, post-execution verification queries, least-privilege security controls, and rollback/backup protocols for the FloodWatch hydrological research platform.

> [!WARNING]
> **Zero Remote Execution Policy:** Migrations must **never** be executed remotely by automated scripts without explicit authorization and a preceding database snapshot. Do not claim Migration 005 has been applied to remote environments without running the verification queries provided below.

---

## 1. Migration Sequence & Purpose

All database changes must be executed in strict sequential order via the Supabase SQL Editor or a controlled `psql` session:

| Order | Migration File | Primary Responsibility |
|:---:|:---|:---|
| **1** | `001_initial_schema.sql` | Enables `uuid-ossp` and `postgis` extensions in schema `extensions`. Creates core tables: `basins`, `districts`, `data_sources`, `monitoring_locations`, and `observations`. Establishes spatial point geometry (`centroid`, `geom`) and initial indices. |
| **2** | `002_ingestion_extensions.sql` | Introduces `ingestion_runs`, unique index constraints (`uq_observations_analysis`, `uq_observations_forecast_active`), revision tracking (`forecast_observation_revisions`), and ingestion audit logging. |
| **3** | `003_weather_ingestion_rpc.sql` | Implements the transactional RPC function `upsert_weather_observations(jsonb, uuid)`, enabling atomic ingestion of weather telemetry with idempotency guards and rollback guarantees. |
| **4** | `004_least_privilege_permissions.sql` | Implements Row-Level Security (RLS) policies and least-privilege role permissions for `anon`, `authenticated`, and `service_role`. Restricts private tables; grants execution on `upsert_weather_observations` exclusively to `service_role`. |
| **5** | `005_risk_alerts_and_notifications.sql` | Creates `risk_alerts` table and `v_public_risk_alerts` view. Establishes deduplication constraints, lifecycle status transitions, and column-level security protecting operator identity fields (`acknowledged_by`). |

---

## 2. Pre-Migration Verification & Backup

Before applying any migration to a staging or production Supabase environment:

1. **Perform Backup / Snapshot:**
   ```bash
   # Using Supabase CLI:
   supabase db dump -f backup_pre_migration_$(date +%Y%m%d_%H%M%S).sql
   
   # Or using standard pg_dump:
   pg_dump "$DATABASE_URL" --format=custom --file="floodwatch_backup_$(date +%Y%m%d_%H%M%S).dump"
   ```

2. **Verify Connected Database:**
   ```sql
   SELECT current_database(), current_user, version();
   ```

---

## 3. Post-Migration Verification Queries

Execute these verification queries to confirm proper application of all schema entities:

### 3.1 Verify Core Tables and Views Exist
```sql
SELECT 
    table_name,
    table_type
FROM information_schema.tables 
WHERE table_schema = 'public' 
  AND table_name IN (
    'basins',
    'districts',
    'data_sources',
    'monitoring_locations',
    'observations',
    'ingestion_runs',
    'forecast_observation_revisions',
    'risk_alerts',
    'v_public_risk_alerts'
  )
ORDER BY table_name;
-- Expected Result: 8 BASE TABLE rows and 1 VIEW row returned.
```

### 3.2 Verify RPC Functions Exist
```sql
SELECT 
    routine_name, 
    routine_type,
    security_type
FROM information_schema.routines 
WHERE routine_schema = 'public' 
  AND routine_name IN ('upsert_weather_observations', 'trg_archive_forecast_revision');
-- Expected Result: 2 rows returned with routine_type = 'FUNCTION'.
```

### 3.3 Verify Row-Level Security (RLS) is Active
```sql
SELECT 
    relname AS table_name, 
    relrowsecurity AS rls_enabled 
FROM pg_class 
WHERE relnamespace = 'public'::regnamespace 
  AND relname IN (
    'monitoring_locations',
    'observations',
    'ingestion_runs',
    'forecast_observation_revisions',
    'risk_alerts'
  );
-- Expected Result: rls_enabled = true for all tables.
```

### 3.4 Verify Least-Privilege Grants (Migration 004 & 005)
```sql
-- Verify table-level permissions: service_role has DML, public roles have no full-table write/read on private tables
SELECT 
    grantee, 
    table_name,
    privilege_type 
FROM information_schema.role_table_grants 
WHERE table_schema = 'public'
  AND table_name = 'risk_alerts' 
  AND grantee IN ('anon', 'authenticated', 'service_role')
ORDER BY grantee, privilege_type;

-- Verify column-level privileges on risk_alerts (SEC-01):
-- Confirm 'acknowledged_by' is NOT granted to 'anon' or 'authenticated'
SELECT 
    grantee,
    column_name,
    privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name = 'risk_alerts'
  AND column_name = 'acknowledged_by';
-- Expected Result: Only service_role (and postgres/admin) returned. No rows for anon or authenticated.
```

---

## 4. Migration 005 Runbook (Risk Alerts & Notification Hardening)

### 4.1 Security Architecture & Upgrade Strategy (PostgreSQL 15+ & Supabase)
1. **Explicit Severity Check Constraint Upgrade:**
   Supports `'INFO'`, `'DATA_WARNING'`, `'WARNING'`, `'HIGH'`, and `'CRITICAL'`.
   If `risk_alerts` already exists, standard `CREATE TABLE IF NOT EXISTS` check constraints are not re-evaluated. Migration 005 executes an idempotent `DO $$ ... $$` block that drops `risk_alerts_severity_check` and re-adds it with `DATA_WARNING`.
2. **PostgreSQL 15+ `security_invoker = true` View:**
   `v_public_risk_alerts` is created with `WITH (security_invoker = true)`. In PostgreSQL 15+, this guarantees that queries against the view evaluate Row-Level Security (RLS) policies and column-level privileges of the **calling role** (`anon` or `authenticated`), rather than the view owner.
3. **Underlying Column-Level Grants & RLS Coordination:**
   - On the base table `risk_alerts`, RLS policy `p_risk_alerts_read_public` allows `SELECT` to `anon` and `authenticated`.
   - Column privileges on `risk_alerts` grant `SELECT` only for public fields (`id`, `location_id`, `alert_type`, `severity`, `status`, `title`, `description`, `evidence`, `created_at`, `updated_at`, `acknowledged_at`, `resolved_at`, `resolved_reason`).
   - `acknowledged_by` (operator identity) and `fingerprint` (internal hash) are **strictly omitted** from column grants.
   - Consequently, any direct `SELECT * FROM risk_alerts` by public roles fails with permission denied, whereas `SELECT * FROM v_public_risk_alerts` succeeds safely.
4. **Obsolete Column-Level Privilege Cleanup:**
   Before granting column privileges, an idempotent PL/pgSQL block inspects `information_schema.column_privileges` and revokes any legacy or accidental column-level grants from `anon`, `authenticated`, and `public`.
5. **Active Alert Deduplication:**
   Partial unique index `uq_risk_alerts_active_fingerprint` guarantees at most one active or acknowledged alert per location and fingerprint.

### 4.2 Manual Execution Steps in Supabase
1. Open the **Supabase Dashboard** -> **SQL Editor**.
2. Select your target project (Staging or Production).
3. Paste the contents of `database/migrations/005_risk_alerts_and_notifications.sql`.
4. Click **Run**.
5. Confirm transaction committed without errors.

### 4.3 Post-Execution Validation
```sql
-- 1. Check table and view
SELECT to_regclass('public.risk_alerts'), to_regclass('public.v_public_risk_alerts');

-- 2. Verify severity constraint includes DATA_WARNING
SELECT conname, pg_get_constraintdef(oid) 
FROM pg_constraint 
WHERE conrelid = 'public.risk_alerts'::regclass AND conname LIKE '%severity%';

-- 3. Verify deduplication index exists
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'risk_alerts' AND indexname = 'uq_risk_alerts_active_fingerprint';
```

---

## 5. Rollback Considerations & Caveats

- **Transactional Functions:** Function `upsert_weather_observations()` is self-contained. Dropping or recreating it does not affect stored historical telemetry.
- **Alert System Rollback (005):**
  ```sql
  -- Rollback Migration 005 if needed:
  DROP VIEW IF EXISTS public.v_public_risk_alerts CASCADE;
  DROP TABLE IF EXISTS public.risk_alerts CASCADE;
  ```
- **Data Preservation:** Never execute `DROP TABLE ... CASCADE` on `observations` or `monitoring_locations` without an offsite binary backup, as historical records cannot be reconstructed without reprocessing raw meteorological feeds.
