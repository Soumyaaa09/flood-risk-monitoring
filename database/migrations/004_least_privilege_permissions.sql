-- FloodWatch Phase 2.3.2.7: Least-Privilege Database Permissions Hardening
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- PURPOSE:
-- Establish strict defense-in-depth access controls across all seven FloodWatch tables.
-- In addition to Row Level Security (RLS), table-level ACL privileges (GRANT/REVOKE)
-- and column-level privileges are locked down to the minimum necessary for each database role:
--
-- 1. Fail-Closed Preflight Safety Check:
--    - Abort transaction if any global default table ACL (defaclnamespace = 0) grants privileges
--      to PUBLIC, anon, or authenticated for postgres, supabase_admin, or CURRENT_USER.
--
-- 2. Public Reference Tables (basins, districts, data_sources):
--    - anon: SELECT only (consistent with public read RLS policies)
--    - authenticated: SELECT only (consistent with public read RLS policies)
--    - service_role: SELECT only
--
-- 3. Private Operational Tables (monitoring_locations, ingestion_runs, observations, forecast_observation_revisions):
--    - anon: NO PRIVILEGES (REVOKE ALL on table and column levels)
--    - authenticated: NO PRIVILEGES (REVOKE ALL on table and column levels)
--    - service_role: Minimum operational privileges required:
--        * monitoring_locations: SELECT
--        * ingestion_runs: SELECT, INSERT, UPDATE
--        * observations: SELECT, INSERT, UPDATE
--        * forecast_observation_revisions: SELECT, INSERT, UPDATE, DELETE
--
-- 4. Column-Level Privilege Cleanup:
--    - Strips all legacy or accidental column-level grants from PUBLIC, anon, authenticated,
--      and service_role across all seven tables before final table grants are applied.
--
-- 5. Functions:
--    - upsert_weather_observations (RPC): Execution granted strictly to service_role; revoked from PUBLIC, anon, authenticated.
--    - trg_archive_forecast_revision (Trigger Function): Revoked from PUBLIC, anon, authenticated, and service_role.
--      (Direct EXECUTE is not granted to service_role as PostgreSQL triggers fire under table DML privileges).
--
-- 6. Default Privileges Hardening with Authorization Guards:
--    - Configures schema-scoped lockdown (IN SCHEMA public) for CURRENT_USER.
--    - Inspects other administrative roles (postgres, supabase_admin) and safely modifies
--      their defaults ONLY if CURRENT_USER is authorized (superuser or role member).
--    - If unauthorized, safely skips with an informative notice, preventing error 42501.
--    - Does not alter global default privileges or system schemas (auth, storage, extensions, vault).
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this migration against Supabase without explicit approval.
-- This file is prepared locally for review and manual execution.

BEGIN;

SET LOCAL search_path = public, extensions;

-- ---------------------------------------------------------------------
-- 1. Fail-Closed Preflight Check: Insecure Global Default ACLs
-- ---------------------------------------------------------------------
-- PostgreSQL evaluates both global default ACLs (defaclnamespace = 0) and
-- schema-specific default ACLs when new objects are created. If an administrator
-- or cloud setup previously configured global default table privileges for
-- PUBLIC, anon, or authenticated, future tables created in public would still
-- inherit those insecure permissions despite schema-level lockdown.
-- This preflight check inspects pg_default_acl and immediately aborts the transaction
-- with an actionable error message if any insecure global default table ACL is detected.
DO $$
DECLARE
    v_insecure_entry RECORD;
BEGIN
    FOR v_insecure_entry IN
        SELECT 
            r.rolname AS owner_role,
            d.defaclobjtype AS obj_type,
            CASE 
                WHEN (a).grantee = 0 THEN 'PUBLIC' 
                ELSE (a).grantee::regrole::text 
            END AS grantee_role,
            (a).privilege_type AS priv
        FROM pg_default_acl d
        JOIN pg_roles r ON r.oid = d.defaclrole
        CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
        WHERE d.defaclnamespace = 0  -- Global (all schemas)
          AND d.defaclobjtype = 'r'  -- Relations (tables / views)
          AND (
              CASE 
                  WHEN (a).grantee = 0 THEN 'PUBLIC' 
                  ELSE (a).grantee::regrole::text 
              END
          ) IN ('PUBLIC', 'anon', 'authenticated')
          AND r.rolname IN ('postgres', 'supabase_admin', CURRENT_USER)
    LOOP
        RAISE EXCEPTION 
            'Preflight check failed: Insecure global default table ACL detected! '
            'Role % has global default privilege % granted to % on tables (defaclnamespace=0). '
            'This would undermine public schema lockdown on future tables. '
            'Remediation: Run "ALTER DEFAULT PRIVILEGES FOR ROLE % REVOKE ALL ON TABLES FROM %;" globally '
            'prior to running Migration 004.',
            v_insecure_entry.owner_role,
            v_insecure_entry.priv,
            v_insecure_entry.grantee_role,
            v_insecure_entry.owner_role,
            v_insecure_entry.grantee_role;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 2. Ensure Row Level Security (RLS) is Active on All Seven Tables
-- ---------------------------------------------------------------------
ALTER TABLE public.basins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.districts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.data_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.monitoring_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forecast_observation_revisions ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------
-- 3. Revoke Broad Default Table Privileges from PUBLIC
-- ---------------------------------------------------------------------
REVOKE ALL ON TABLE public.basins FROM PUBLIC;
REVOKE ALL ON TABLE public.districts FROM PUBLIC;
REVOKE ALL ON TABLE public.data_sources FROM PUBLIC;
REVOKE ALL ON TABLE public.monitoring_locations FROM PUBLIC;
REVOKE ALL ON TABLE public.ingestion_runs FROM PUBLIC;
REVOKE ALL ON TABLE public.observations FROM PUBLIC;
REVOKE ALL ON TABLE public.forecast_observation_revisions FROM PUBLIC;

-- ---------------------------------------------------------------------
-- 4. Revoke Any Existing Column-Level Grants (PUBLIC, anon, authenticated, service_role)
-- ---------------------------------------------------------------------
-- In PostgreSQL, table-level REVOKE does not automatically clear custom
-- column-level grants (pg_attribute.attacl). This defensive block dynamically
-- strips any preexisting column grants for PUBLIC, anon, authenticated, and service_role
-- on all seven FloodWatch tables before applying clean table-level grants.
DO $$
DECLARE
    r RECORD;
    v_grantee_clause TEXT;
BEGIN
    FOR r IN 
        SELECT table_schema, table_name, column_name, grantee, privilege_type
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
    LOOP
        IF r.grantee = 'PUBLIC' THEN
            v_grantee_clause := 'PUBLIC';
        ELSE
            v_grantee_clause := quote_ident(r.grantee);
        END IF;

        EXECUTE format(
            'REVOKE %s (%I) ON TABLE %I.%I FROM %s',
            r.privilege_type, r.column_name, r.table_schema, r.table_name, v_grantee_clause
        );
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 5. Least-Privilege Role Configuration (anon, authenticated, service_role)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    -- =================================================================
    -- A. Role: 'anon' (Anonymous Public Web Clients)
    -- =================================================================
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        -- Revoke all table permissions across all seven tables first
        REVOKE ALL ON TABLE public.basins FROM anon;
        REVOKE ALL ON TABLE public.districts FROM anon;
        REVOKE ALL ON TABLE public.data_sources FROM anon;
        REVOKE ALL ON TABLE public.monitoring_locations FROM anon;
        REVOKE ALL ON TABLE public.ingestion_runs FROM anon;
        REVOKE ALL ON TABLE public.observations FROM anon;
        REVOKE ALL ON TABLE public.forecast_observation_revisions FROM anon;

        -- Grant SELECT exclusively on public reference tables
        GRANT SELECT ON TABLE public.basins TO anon;
        GRANT SELECT ON TABLE public.districts TO anon;
        GRANT SELECT ON TABLE public.data_sources TO anon;
    END IF;

    -- =================================================================
    -- B. Role: 'authenticated' (Standard Signed-In Application Users)
    -- =================================================================
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        -- Revoke all table permissions across all seven tables first
        REVOKE ALL ON TABLE public.basins FROM authenticated;
        REVOKE ALL ON TABLE public.districts FROM authenticated;
        REVOKE ALL ON TABLE public.data_sources FROM authenticated;
        REVOKE ALL ON TABLE public.monitoring_locations FROM authenticated;
        REVOKE ALL ON TABLE public.ingestion_runs FROM authenticated;
        REVOKE ALL ON TABLE public.observations FROM authenticated;
        REVOKE ALL ON TABLE public.forecast_observation_revisions FROM authenticated;

        -- Grant SELECT exclusively on public reference tables
        GRANT SELECT ON TABLE public.basins TO authenticated;
        GRANT SELECT ON TABLE public.districts TO authenticated;
        GRANT SELECT ON TABLE public.data_sources TO authenticated;
    END IF;

    -- =================================================================
    -- C. Role: 'service_role' (Trusted Backend Ingestion & Worker Service)
    -- =================================================================
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        -- Revoke all table permissions first to clear any overly broad grants (e.g. TRUNCATE, REFERENCES, TRIGGER)
        REVOKE ALL ON TABLE public.basins FROM service_role;
        REVOKE ALL ON TABLE public.districts FROM service_role;
        REVOKE ALL ON TABLE public.data_sources FROM service_role;
        REVOKE ALL ON TABLE public.monitoring_locations FROM service_role;
        REVOKE ALL ON TABLE public.ingestion_runs FROM service_role;
        REVOKE ALL ON TABLE public.observations FROM service_role;
        REVOKE ALL ON TABLE public.forecast_observation_revisions FROM service_role;

        -- 1. Reference tables: SELECT only
        GRANT SELECT ON TABLE public.basins TO service_role;
        GRANT SELECT ON TABLE public.districts TO service_role;
        GRANT SELECT ON TABLE public.data_sources TO service_role;
        GRANT SELECT ON TABLE public.monitoring_locations TO service_role;

        -- 2. Ingestion runs: SELECT, INSERT, UPDATE (no DELETE or TRUNCATE)
        GRANT SELECT, INSERT, UPDATE ON TABLE public.ingestion_runs TO service_role;

        -- 3. Observations: SELECT, INSERT, UPDATE (no DELETE or TRUNCATE)
        GRANT SELECT, INSERT, UPDATE ON TABLE public.observations TO service_role;

        -- 4. Forecast revisions: SELECT, INSERT, UPDATE, DELETE
        -- (DELETE reserved for controlled administrative pruning; INSERT required for revision trigger)
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.forecast_observation_revisions TO service_role;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 6. Secure Function Execution Permissions
-- ---------------------------------------------------------------------
DO $$
BEGIN
    -- Function: trg_archive_forecast_revision (Revision Trigger Function)
    -- Trigger functions are invoked by the PostgreSQL trigger engine during table DML,
    -- not through direct SQL execution. Direct EXECUTE is revoked from PUBLIC, anon,
    -- authenticated, and service_role.
    IF EXISTS (
        SELECT 1 FROM pg_proc p 
        JOIN pg_namespace n ON n.oid = p.pronamespace 
        WHERE n.nspname = 'public' AND p.proname = 'trg_archive_forecast_revision'
    ) THEN
        REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM PUBLIC;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM authenticated;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
            REVOKE ALL ON FUNCTION public.trg_archive_forecast_revision() FROM service_role;
        END IF;
    END IF;

    -- Function: upsert_weather_observations (Migration 003 Ingestion RPC)
    -- Directly invoked by backend via PostgREST RPC; requires explicit EXECUTE for service_role.
    IF EXISTS (
        SELECT 1 FROM pg_proc p 
        JOIN pg_namespace n ON n.oid = p.pronamespace 
        WHERE n.nspname = 'public' AND p.proname = 'upsert_weather_observations'
    ) THEN
        REVOKE ALL ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM PUBLIC;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM authenticated;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
            GRANT EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) TO service_role;
        END IF;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 7. Lock Down Schema-Specific Default Privileges
-- ---------------------------------------------------------------------
-- Scoped strictly to schema 'public' to avoid altering system schemas (pg_catalog, information_schema)
-- or internal Supabase schemas (auth, storage, extensions, vault).
--
-- In PostgreSQL:
-- 1. Any role can configure default privileges for objects created by ITSELF (CURRENT_USER).
-- 2. Altering default privileges FOR ANOTHER ROLE requires the executing user to be a member
--    of that role (pg_has_role(CURRENT_USER, target_role, 'MEMBER')) or a superuser.
-- In Supabase, 'postgres' is the standard migration executing role, but is NOT a member of 'supabase_admin'.
-- Blindly attempting 'ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin ...' as 'postgres' fails with:
--   ERROR: 42501: permission denied to change default privileges.
--
-- This section:
-- 1. Always configures default privileges for CURRENT_USER in schema 'public'.
-- 2. Inspects other known administrative roles (postgres, supabase_admin).
-- 3. Only executes ALTER DEFAULT PRIVILEGES FOR ROLE <role> if CURRENT_USER is authorized.
-- 4. Safely skips unauthorized roles with an informative RAISE NOTICE.

-- A. Always configure schema-specific defaults for CURRENT_USER in schema public
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated;
    END IF;
END $$;

-- B. Conditionally configure schema-specific defaults for other roles IF authorized
DO $$
DECLARE
    v_role TEXT;
    v_is_superuser BOOLEAN := FALSE;
    v_can_alter BOOLEAN := FALSE;
BEGIN
    SELECT rolsuper INTO v_is_superuser 
    FROM pg_roles 
    WHERE rolname = CURRENT_USER;

    FOR v_role IN 
        SELECT rolname FROM pg_roles 
        WHERE rolname IN ('postgres', 'supabase_admin') AND rolname != CURRENT_USER
    LOOP
        -- In PostgreSQL, ALTER DEFAULT PRIVILEGES FOR ROLE <target> requires superuser
        -- or role membership in <target>.
        v_can_alter := v_is_superuser OR pg_has_role(CURRENT_USER, v_role, 'MEMBER');

        IF v_can_alter THEN
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC', v_role);
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM anon', v_role);
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated', v_role);
            END IF;
            RAISE NOTICE 'Successfully configured default privileges in schema public for role %.', v_role;
        ELSE
            RAISE NOTICE 'Skipping default privileges configuration for role %: executing user % is not a member or superuser. '
                         'If % creates tables in schema public, an authorized administrator must configure its default privileges separately.',
                         v_role, CURRENT_USER, v_role;
        END IF;
    END LOOP;
END $$;

COMMIT;

-- =====================================================================
-- PRE-MIGRATION DIAGNOSTIC QUERIES (Run Before Applying Migration 004)
-- =====================================================================
--
-- 1. Inspect Session Identity & Role Authority (Detect if user can alter other roles' defaults):
--    SELECT 
--        current_user AS executing_current_user,
--        session_user AS executing_session_user,
--        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser,
--        pg_has_role(current_user, 'postgres', 'MEMBER') AS can_alter_postgres_defaults,
--        pg_has_role(current_user, 'supabase_admin', 'MEMBER') AS can_alter_supabase_admin_defaults;
--
-- 2. Inspect any preexisting column-level privileges on FloodWatch tables for PUBLIC, anon, authenticated, and service_role:
--    SELECT 
--        grantee, 
--        table_name, 
--        column_name, 
--        privilege_type
--    FROM information_schema.column_privileges
--    WHERE table_schema = 'public'
--      AND table_name IN ('basins', 'districts', 'data_sources', 'monitoring_locations', 'ingestion_runs', 'observations', 'forecast_observation_revisions')
--      AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role')
--    ORDER BY table_name, column_name, grantee;
--
-- 3. Inspect current default ACL entries across schemas (both global and schema-specific):
--    SELECT 
--        r.rolname AS target_role,
--        COALESCE(n.nspname, '(GLOBAL - ALL SCHEMAS)') AS schema_scope,
--        d.defaclobjtype AS object_type,
--        CASE 
--            WHEN (a).grantee = 0 THEN 'PUBLIC' 
--            ELSE (a).grantee::regrole::text 
--        END AS grantee,
--        (a).privilege_type AS privilege_type,
--        (a).is_grantable
--    FROM pg_default_acl d
--    LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
--    JOIN pg_roles r ON r.oid = d.defaclrole
--    CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
--    ORDER BY r.rolname, schema_scope, object_type, grantee;
--
-- =====================================================================
-- POST-MIGRATION VERIFICATION QUERIES (Manual Diagnostic Blueprint)
-- =====================================================================
--
-- 1. Verify table-level privileges across all seven tables:
--    SELECT 
--        grantee, 
--        table_name, 
--        string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
--    FROM information_schema.role_table_grants
--    WHERE table_schema = 'public'
--      AND table_name IN ('basins', 'districts', 'data_sources', 'monitoring_locations', 'ingestion_runs', 'observations', 'forecast_observation_revisions')
--      AND grantee IN ('anon', 'authenticated', 'service_role')
--    GROUP BY grantee, table_name
--    ORDER BY table_name, grantee;
--
--    Expected output:
--    - anon: SELECT on basins, data_sources, districts ONLY. (Zero rows for others).
--    - authenticated: SELECT on basins, data_sources, districts ONLY. (Zero rows for others).
--    - service_role:
--        * basins, districts, data_sources, monitoring_locations: SELECT
--        * ingestion_runs, observations: INSERT, SELECT, UPDATE
--        * forecast_observation_revisions: DELETE, INSERT, SELECT, UPDATE
--
-- 2. Verify zero column-level privileges remain for PUBLIC, anon, authenticated, or service_role:
--    SELECT 
--        grantee, 
--        table_name, 
--        column_name, 
--        privilege_type
--    FROM information_schema.column_privileges
--    WHERE table_schema = 'public'
--      AND table_name IN ('basins', 'districts', 'data_sources', 'monitoring_locations', 'ingestion_runs', 'observations', 'forecast_observation_revisions')
--      AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role');
--    (Expected: 0 rows)
--
-- 3. Verify public schema-specific default privileges configured:
--    SELECT 
--        r.rolname AS target_role,
--        n.nspname AS schema_scope,
--        d.defaclobjtype AS object_type,
--        CASE 
--            WHEN (a).grantee = 0 THEN 'PUBLIC' 
--            ELSE (a).grantee::regrole::text 
--        END AS grantee,
--        (a).privilege_type AS privilege_type,
--        (a).is_grantable
--    FROM pg_default_acl d
--    JOIN pg_namespace n ON n.oid = d.defaclnamespace
--    JOIN pg_roles r ON r.oid = d.defaclrole
--    CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
--    WHERE n.nspname = 'public' AND d.defaclobjtype = 'r'
--    ORDER BY r.rolname, grantee;
--
-- 4. Verify Row Level Security status:
--    SELECT 
--        tablename, 
--        rowsecurity AS rls_enabled
--    FROM pg_tables 
--    WHERE schemaname = 'public'
--      AND tablename IN ('basins', 'districts', 'data_sources', 'monitoring_locations', 'ingestion_runs', 'observations', 'forecast_observation_revisions')
--    ORDER BY tablename;
--
-- 5. Verify public RLS read policies remain intact:
--    SELECT 
--        tablename, 
--        policyname, 
--        cmd, 
--        roles
--    FROM pg_policies 
--    WHERE schemaname = 'public'
--    ORDER BY tablename, policyname;
--
-- 6. Verify function execution permissions:
--    SELECT 
--        grantee, 
--        routine_name, 
--        privilege_type
--    FROM information_schema.routine_privileges
--    WHERE routine_schema = 'public' 
--      AND routine_name IN ('upsert_weather_observations', 'trg_archive_forecast_revision');
--    (Expected: Only service_role and postgres/superuser hold EXECUTE on upsert_weather_observations;
--     trg_archive_forecast_revision has no direct application role grants)
--
-- =====================================================================
-- DESTRUCTIVE ROLLBACK BLUEPRINT (Requires Explicit Approval)
-- =====================================================================
-- WARNING: Re-granting ALL privileges to anon or authenticated dismantles
-- the defense-in-depth security model and relies entirely on RLS policies.
-- If any RLS policy is misconfigured or disabled, private operational data
-- could be exposed or mutated by unauthenticated public actors.
--
-- BEGIN;
-- SET LOCAL search_path = public, extensions;
-- DO $$
-- BEGIN
--     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
--         GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
--     END IF;
--     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
--         GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
--     END IF;
--     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
--         GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
--     END IF;
-- END $$;
-- COMMIT;
