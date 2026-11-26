# FloodWatch Phase 2.3.2.7: Migration 004 Verification & Diagnostic Blueprint

**Target:** PostgreSQL 15+ / Supabase with PostGIS  
**Migration:** `database/migrations/004_least_privilege_permissions.sql`  
**Execution Context:** Manual execution in Supabase SQL Editor (Requires human administrator approval)  
**Security Boundary:** Local planning & verification only. No remote execution performed.

---

## Overview

Migration 004 establishes defense-in-depth table- and column-level access controls across all seven FloodWatch tables:
1. `basins` (Public Reference)
2. `districts` (Public Reference)
3. `data_sources` (Public Reference)
4. `monitoring_locations` (Private Operational)
5. `ingestion_runs` (Private Operational)
6. `observations` (Private Operational)
7. `forecast_observation_revisions` (Private Operational)

This document contains 11 diagnostic query blocks to run before and after applying Migration 004 to verify least-privilege compliance and inspect role permissions.

---

## Block 1: Pre-Migration Session Identity & Role Authority Check

Inspect the executing session's role identity, superuser privileges, and membership authority to modify default privileges for `postgres` and `supabase_admin`.

```sql
SELECT 
    current_user AS executing_current_user,
    session_user AS executing_session_user,
    (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser,
    pg_has_role(current_user, 'postgres', 'MEMBER') AS can_alter_postgres_defaults,
    pg_has_role(current_user, 'supabase_admin', 'MEMBER') AS can_alter_supabase_admin_defaults;
```

**Interpretation of Results in Supabase:**
- In Supabase SQL Editor, `executing_current_user` is typically `postgres`.
- `is_superuser` is `false`.
- `can_alter_postgres_defaults` is `true` (current user is `postgres`).
- `can_alter_supabase_admin_defaults` is `false` (`postgres` is not a member of `supabase_admin`).
- **Safety guarantee:** Migration 004 uses `pg_has_role` guards to configure defaults for `CURRENT_USER` and safely skips `supabase_admin` without raising `ERROR 42501`.

---

## Block 2: Pre-Migration Column-Level Privilege Check

Inspect whether any legacy or accidental column-level grants exist on any of the seven FloodWatch tables for `PUBLIC`, `anon`, `authenticated`, or `service_role`.

```sql
SELECT 
    grantee, 
    table_name, 
    column_name, 
    privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name IN (
      'basins', 
      'districts', 
      'data_sources', 
      'monitoring_locations', 
      'ingestion_runs', 
      'observations', 
      'forecast_observation_revisions'
  )
  AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role')
ORDER BY table_name, column_name, grantee;
```

**Expected Result Prior to Migration:**
- May return legacy column-level privileges if any previous role migrations were executed with column grants.
- Migration 004 Section 4 dynamically strips all matching column-level privileges.

---

## Block 3: Pre-Migration Global Default ACL Check

Verify whether any insecure global default table privileges exist across the cluster that would undermine the public schema lockdown.

```sql
SELECT 
    r.rolname AS owner_role,
    COALESCE(n.nspname, '(GLOBAL - ALL SCHEMAS)') AS schema_scope,
    d.defaclobjtype AS object_type,
    CASE 
        WHEN (a).grantee = 0 THEN 'PUBLIC' 
        ELSE (a).grantee::regrole::text 
    END AS grantee,
    (a).privilege_type AS privilege_type,
    (a).is_grantable
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
JOIN pg_roles r ON r.oid = d.defaclrole
CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
WHERE d.defaclnamespace = 0  -- Global defaults
  AND d.defaclobjtype = 'r'  -- Relations (tables/views)
ORDER BY r.rolname, grantee, privilege_type;
```

**Expected Result Prior to Migration:**
- If any rows exist granting table privileges to `PUBLIC`, `anon`, or `authenticated` for `postgres`, `supabase_admin`, or `CURRENT_USER`, Migration 004's fail-closed preflight check will abort the transaction.
- Remediation if found: Run `ALTER DEFAULT PRIVILEGES FOR ROLE <owner> REVOKE ALL ON TABLES FROM <grantee>;` globally prior to re-running Migration 004.

---

## Block 4: Pre-Migration Schema-Specific Default ACL Check

Inspect existing default privileges configured specifically inside schema `public`.

```sql
SELECT 
    r.rolname AS owner_role,
    n.nspname AS schema_scope,
    d.defaclobjtype AS object_type,
    CASE 
        WHEN (a).grantee = 0 THEN 'PUBLIC' 
        ELSE (a).grantee::regrole::text 
    END AS grantee,
    (a).privilege_type AS privilege_type,
    (a).is_grantable
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
JOIN pg_roles r ON r.oid = d.defaclrole
CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
WHERE n.nspname = 'public'
ORDER BY r.rolname, d.defaclobjtype, grantee;
```

---

## Block 5: Post-Migration Table-Level Grant Verification

Verify that table-level ACL privileges match the intended least-privilege matrix.

```sql
SELECT 
    grantee, 
    table_name, 
    string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN (
      'basins', 
      'districts', 
      'data_sources', 
      'monitoring_locations', 
      'ingestion_runs', 
      'observations', 
      'forecast_observation_revisions'
  )
  AND grantee IN ('anon', 'authenticated', 'service_role')
GROUP BY grantee, table_name
ORDER BY table_name, grantee;
```

**Expected Output:**

| Table | anon | authenticated | service_role |
|---|---|---|---|
| `basins` | `SELECT` | `SELECT` | `SELECT` |
| `districts` | `SELECT` | `SELECT` | `SELECT` |
| `data_sources` | `SELECT` | `SELECT` | `SELECT` |
| `monitoring_locations` | *(None / 0 rows)* | *(None / 0 rows)* | `SELECT` |
| `ingestion_runs` | *(None / 0 rows)* | *(None / 0 rows)* | `INSERT, SELECT, UPDATE` |
| `observations` | *(None / 0 rows)* | *(None / 0 rows)* | `INSERT, SELECT, UPDATE` |
| `forecast_observation_revisions` | *(None / 0 rows)* | *(None / 0 rows)* | `DELETE, INSERT, SELECT, UPDATE` |

---

## Block 6: Post-Migration Column-Level Grant Verification

Confirm that no custom column-level privileges remain for any application roles.

```sql
SELECT 
    grantee, 
    table_name, 
    column_name, 
    privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name IN (
      'basins', 
      'districts', 
      'data_sources', 
      'monitoring_locations', 
      'ingestion_runs', 
      'observations', 
      'forecast_observation_revisions'
  )
  AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role');
```

**Expected Output:**
- Exactly 0 rows returned.

---

## Block 7: Post-Migration Row Level Security (RLS) Status

Verify that RLS is strictly enabled across all seven tables.

```sql
SELECT 
    tablename, 
    rowsecurity AS rls_enabled
FROM pg_tables 
WHERE schemaname = 'public'
  AND tablename IN (
      'basins', 
      'districts', 
      'data_sources', 
      'monitoring_locations', 
      'ingestion_runs', 
      'observations', 
      'forecast_observation_revisions'
  )
ORDER BY tablename;
```

**Expected Output:**
- All 7 tables report `rls_enabled = true`.

---

## Block 8: Post-Migration RLS Read Policies Verification

Verify that public read policies exist only for reference tables (`basins`, `districts`, `data_sources`) and that no public policies permit access to private operational tables.

```sql
SELECT 
    tablename, 
    policyname, 
    cmd, 
    roles
FROM pg_policies 
WHERE schemaname = 'public'
  AND tablename IN (
      'basins', 
      'districts', 
      'data_sources', 
      'monitoring_locations', 
      'ingestion_runs', 
      'observations', 
      'forecast_observation_revisions'
  )
ORDER BY tablename, policyname;
```

**Expected Output:**
- `basins`: `Allow public read access to basins` (SELECT, `{public}`)
- `districts`: `Allow public read access to districts` (SELECT, `{public}`)
- `data_sources`: `Allow public read access to data_sources` (SELECT, `{public}`)
- `monitoring_locations`, `ingestion_runs`, `observations`, `forecast_observation_revisions`: 0 public policies.

---

## Block 9: Post-Migration Function Execution Grants

Verify that routine execution privileges are strictly locked down.

```sql
SELECT 
    grantee, 
    routine_name, 
    privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public' 
  AND routine_name IN ('upsert_weather_observations', 'trg_archive_forecast_revision')
ORDER BY routine_name, grantee;
```

**Expected Output:**
- `upsert_weather_observations`: `EXECUTE` granted strictly to `service_role` (plus standard `postgres`/superuser roles). No execution granted to `PUBLIC`, `anon`, or `authenticated`.
- `trg_archive_forecast_revision`: No execution granted to `PUBLIC`, `anon`, `authenticated`, or `service_role`. (Invoked only internally by PostgreSQL trigger engine during table DML).

---

## Block 10: Post-Migration Schema-Specific Default ACLs

Confirm that schema-specific default privileges in `public` revoke all table permissions from `PUBLIC`, `anon`, and `authenticated` for the executing role (`postgres` / `CURRENT_USER`).

```sql
SELECT 
    r.rolname AS owner_role,
    n.nspname AS schema_scope,
    d.defaclobjtype AS object_type,
    CASE 
        WHEN (a).grantee = 0 THEN 'PUBLIC' 
        ELSE (a).grantee::regrole::text 
    END AS grantee,
    (a).privilege_type AS privilege_type,
    (a).is_grantable
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
JOIN pg_roles r ON r.oid = d.defaclrole
CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
WHERE n.nspname = 'public'
  AND d.defaclobjtype = 'r'
ORDER BY r.rolname, grantee;
```

**Expected Output:**
- Shows revoked table privileges for `PUBLIC`, `anon`, and `authenticated` under `postgres` (or `CURRENT_USER`).

---

## Block 11: Destructive Rollback Blueprint (Emergency Rollback Only)

> [!CAUTION]
> **HIGH SECURITY RISK:** Re-granting broad privileges to `anon` or `authenticated` dismantles table-level defense-in-depth and relies entirely on Row Level Security. Execute only if a catastrophic operational regression requires immediate restoration of unhardened state.

```sql
BEGIN;

SET LOCAL search_path = public, extensions;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
    END IF;
END $$;

COMMIT;
```
