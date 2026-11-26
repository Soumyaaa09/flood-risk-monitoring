-- FloodWatch Phase 2.7: Risk Alerts & In-App Notification System Migration
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- PURPOSE:
-- Establishes persistent storage, audit trails, and strict deduplication for
-- rainfall-based risk alerts, data-quality warnings, and risk-reduction events
-- across Mahanadi Delta pilot monitoring locations (Paradeep, Cuttack, Kendrapara).
--
-- ESTABLISHED REPOSITORY SCHEMA ALIGNMENT:
-- - Foreign Table: public.monitoring_locations (from Migration 001, id UUID PK)
-- - Foreign Key: location_id UUID REFERENCES public.monitoring_locations(id)
-- - Alert Types: MODERATE_RAINFALL_RISK, HIGH_RAINFALL_RISK, VERY_HIGH_RAINFALL_RISK,
--                DATA_UNAVAILABLE, DATA_STALE, RISK_REDUCED
-- - Severity Values: INFO, DATA_WARNING, WARNING, HIGH, CRITICAL
-- - Status Lifecycle: NEW, ACTIVE, ACKNOWLEDGED, RESOLVED
-- - Deduplication: (location_id, fingerprint) unique index for un-resolved alerts
--
-- SECURITY ARCHITECTURE & UPGRADE SAFETY (PostgreSQL 15+ & Supabase):
-- 1. Table-level Privileges:
--    - PUBLIC / anon / authenticated: NO direct table-level SELECT or DML.
--    - service_role: Full operational control (SELECT, INSERT, UPDATE, DELETE).
-- 2. Row Level Security (RLS):
--    - Enforced on risk_alerts.
--    - Public read policy for sanitized operational visibility.
--    - Service-role full policy for automated engine writes and administrative acknowledgements.
-- 3. Complete Column-Level Privilege Lockdown:
--    - Revokes ALL pre-existing column privileges (SELECT, INSERT, UPDATE, REFERENCES, etc.)
--      for PUBLIC, anon, and authenticated.
--    - Distinctly handles bare PostgreSQL keyword PUBLIC vs quoted role identifiers ("anon", "authenticated").
--    - Re-grants SELECT strictly on the 13 approved public columns.
--    - Sensitive columns 'acknowledged_by' (operator identity) and 'fingerprint' (internal hash)
--      are NEVER accessible to PUBLIC, anon, or authenticated.
-- 4. Sanitized Public View (v_public_risk_alerts) with Deep Compatibility Verification:
--    - Compares column names, ordinal positions, PostgreSQL data types, and length modifiers.
--    - Pre-checks pg_depend for external dependent objects before replacement; NEVER uses CASCADE.
--    - Preserves existing dependencies when the view is compatible; aborts with diagnostic if
--      incompatible column types and dependent objects coexist.
--    - Uses WITH (security_invoker = true) (PostgreSQL 15+), ensuring caller RLS and column privileges apply.
-- 5. Targeted Constraint Migration & NULL-Aware Preflight Validation:
--    - Preflight checks verify existing table structure has all required columns.
--    - NULL-aware inspection scans existing records for NULLs or values outside canonical sets;
--      aborts with diagnostic exception before altering any constraints.
--    - Preflight duplicate fingerprint check prevents failures when creating the unique index.
--    - Never silently deletes or modifies existing alert records.
--    - Drops ONLY explicitly identified legacy/canonical constraint names:
--        * risk_alerts_severity_check
--        * chk_alert_severity
--        * risk_alerts_status_check
--        * risk_alerts_alert_type_check
--    - Preserves all unrelated check constraints (e.g. multi-column business rules).
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this migration against Supabase without explicit approval.
-- This file is prepared locally for review and manual execution by the database administrator.

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
-- 1.1 Structural Preflight Verification for Pre-Existing Tables
-- ---------------------------------------------------------------------
-- If table already existed, verify that all 15 required canonical columns exist,
-- that column count is exactly 15, and that types, lengths, nullability, ordinal
-- positions, and canonical defaults match the established architectural contract.
DO $$
DECLARE
    actual_col_count INT;
    unexpected_cols TEXT;
    missing_cols TEXT;
    order_mismatches TEXT;
    type_mismatches TEXT;
    len_mismatches TEXT;
    nullability_mismatches TEXT;
    default_mismatches TEXT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        -- Step A: Verify exact column count (must be exactly 15 columns)
        SELECT count(*) INTO actual_col_count
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'risk_alerts';

        IF actual_col_count != 15 THEN
            SELECT string_agg(column_name, ', ')
            INTO unexpected_cols
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'risk_alerts'
              AND column_name NOT IN (
                'id', 'location_id', 'alert_type', 'severity', 'status',
                'title', 'description', 'evidence', 'fingerprint',
                'created_at', 'updated_at', 'acknowledged_at', 'acknowledged_by',
                'resolved_at', 'resolved_reason'
              );
            RAISE EXCEPTION 'Migration 005 preflight check failed: Pre-existing table public.risk_alerts has % columns (expected exactly 15). Unexpected column(s): [%]. Reconcile base table structure before re-running migration.', 
                actual_col_count, COALESCE(unexpected_cols, 'none');
        END IF;

        -- Step B: Deep column-by-column contract inspection
        WITH canonical_spec(pos, col, exp_type, exp_len, exp_nullable, default_pattern) AS (
            VALUES
                (1,  'id',              'uuid',        NULL::int, 'NO',  'gen_random_uuid()'),
                (2,  'location_id',     'uuid',        NULL::int, 'NO',  NULL),
                (3,  'alert_type',      'varchar',     50,        'NO',  NULL),
                (4,  'severity',        'varchar',     30,        'NO',  NULL),
                (5,  'status',          'varchar',     30,        'NO',  '''NEW'''),
                (6,  'title',           'varchar',     255,       'NO',  NULL),
                (7,  'description',     'text',        NULL::int, 'NO',  NULL),
                (8,  'evidence',        'jsonb',       NULL::int, 'NO',  '''{}'''),
                (9,  'fingerprint',     'varchar',     255,       'NO',  NULL),
                (10, 'created_at',      'timestamptz', NULL::int, 'NO',  'clock_timestamp()'),
                (11, 'updated_at',      'timestamptz', NULL::int, 'NO',  'clock_timestamp()'),
                (12, 'acknowledged_at', 'timestamptz', NULL::int, 'YES', NULL),
                (13, 'acknowledged_by', 'varchar',     100,       'YES', NULL),
                (14, 'resolved_at',     'timestamptz', NULL::int, 'YES', NULL),
                (15, 'resolved_reason', 'text',        NULL::int, 'YES', NULL)
        ),
        actual_cols AS (
            SELECT 
                column_name,
                ordinal_position,
                udt_name,
                character_maximum_length,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'risk_alerts'
        )
        SELECT 
            string_agg(CASE WHEN a.column_name IS NULL THEN s.col END, ', '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.ordinal_position != s.pos 
                            THEN format('%s (pos %s, expected %s)', s.col, a.ordinal_position, s.pos) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.udt_name != s.exp_type 
                            THEN format('%s (%s, expected %s)', s.col, a.udt_name, s.exp_type) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND s.exp_len IS NOT NULL AND a.character_maximum_length != s.exp_len 
                            THEN format('%s (len %s, expected %s)', s.col, a.character_maximum_length, s.exp_len) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.is_nullable != s.exp_nullable 
                            THEN format('%s (nullable %s, expected %s)', s.col, a.is_nullable, s.exp_nullable) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND s.default_pattern IS NOT NULL 
                                 AND (a.column_default IS NULL OR a.column_default NOT LIKE '%' || s.default_pattern || '%')
                            THEN format('%s (default [%s], expected pattern [%s])', s.col, coalesce(a.column_default, 'NULL'), s.default_pattern) END, '; ')
        INTO 
            missing_cols,
            order_mismatches,
            type_mismatches,
            len_mismatches,
            nullability_mismatches,
            default_mismatches
        FROM canonical_spec s
        LEFT JOIN actual_cols a ON s.col = a.column_name;

        IF missing_cols IS NOT NULL THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Pre-existing table public.risk_alerts is missing required canonical column(s): [%]. Reconcile base table structure before re-running migration.', missing_cols;
        END IF;

        IF order_mismatches IS NOT NULL OR type_mismatches IS NOT NULL OR len_mismatches IS NOT NULL OR nullability_mismatches IS NOT NULL OR default_mismatches IS NOT NULL THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Incompatible schema in pre-existing public.risk_alerts. Mismatches -> Order: [%], Types: [%], Lengths: [%], Nullability: [%], Defaults: [%]. Reconcile table structure before re-running migration.',
                coalesce(order_mismatches, 'none'),
                coalesce(type_mismatches, 'none'),
                coalesce(len_mismatches, 'none'),
                coalesce(nullability_mismatches, 'none'),
                coalesce(default_mismatches, 'none');
        END IF;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 1.2 Targeted Constraint Migration & NULL-Aware Preflight Validation
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
-- 1.3 Preflight Duplicate Fingerprint Check for Deduplication Index
-- ---------------------------------------------------------------------
-- Before creating the partial unique index, verify there are no pre-existing
-- duplicate active/open alerts with identical (location_id, fingerprint).
DO $$
DECLARE
    dup_group_count INT := 0;
    dup_details TEXT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
    ) THEN
        SELECT 
            count(*),
            string_agg(format('(location: %s, fingerprint: %s, occurrences: %s)', location_id, fingerprint, occurrence_count), '; ')
        INTO dup_group_count, dup_details
        FROM (
            SELECT location_id, fingerprint, count(*) AS occurrence_count
            FROM public.risk_alerts
            WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')
            GROUP BY location_id, fingerprint
            HAVING count(*) > 1
        ) dups;

        IF dup_group_count > 0 THEN
            RAISE EXCEPTION 'Migration 005 preflight check failed: Found % duplicate open alert fingerprint group(s): [%]. Deduplication index uq_risk_alerts_active_fingerprint cannot be created. Existing records were preserved. Reconcile duplicates before re-running migration.', 
                dup_group_count, dup_details;
        END IF;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2. Performance & Lifecycle Indexes
-- ---------------------------------------------------------------------
-- Fast lookup by location and lifecycle state
CREATE INDEX IF NOT EXISTS idx_risk_alerts_location_status 
    ON risk_alerts (location_id, status);

-- Fast lookup for open alerts across the pipeline
CREATE INDEX IF NOT EXISTS idx_risk_alerts_status_created 
    ON risk_alerts (status, created_at DESC);

-- Fast lookup by severity
CREATE INDEX IF NOT EXISTS idx_risk_alerts_severity 
    ON risk_alerts (severity);

-- Deduplication guard: At most one un-resolved alert per location & fingerprint
CREATE UNIQUE INDEX IF NOT EXISTS uq_risk_alerts_active_fingerprint 
    ON risk_alerts (location_id, fingerprint) 
    WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED');

-- ---------------------------------------------------------------------
-- 3. Row-Level Security (RLS) Policies
-- ---------------------------------------------------------------------
ALTER TABLE risk_alerts ENABLE ROW LEVEL SECURITY;

-- Drop prior policies if re-running
DROP POLICY IF EXISTS p_risk_alerts_read_public ON risk_alerts;
DROP POLICY IF EXISTS p_risk_alerts_service_role_all ON risk_alerts;

-- Public / Anonymous / Authenticated users can view alerts
CREATE POLICY p_risk_alerts_read_public ON risk_alerts
    FOR SELECT
    TO anon, authenticated
    USING (true);

-- Backend service role has complete operational control
CREATE POLICY p_risk_alerts_service_role_all ON risk_alerts
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ---------------------------------------------------------------------
-- 4. Least-Privilege Role Grants & Complete Column Privilege Revocation
-- ---------------------------------------------------------------------
-- 4.1 Table-level revocation: Deny all direct table-level privileges from public roles
REVOKE ALL ON TABLE public.risk_alerts FROM PUBLIC;
REVOKE ALL ON TABLE public.risk_alerts FROM anon;
REVOKE ALL ON TABLE public.risk_alerts FROM authenticated;

-- 4.2 Complete column-level cleanup: Strip ALL pre-existing column grants (SELECT, INSERT, UPDATE, REFERENCES, etc.)
-- Note: In PostgreSQL, PUBLIC is a keyword and universal pseudo-role, not an ordinary quoted identifier.
-- It must be revoked with bare keyword PUBLIC, while anon and authenticated use identifier quoting (%I).
-- Both pg_attribute.attacl (raw catalog ACLs) and information_schema.column_privileges are inspected.
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT DISTINCT att.attname AS column_name,
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE ro.rolname END AS grantee
        FROM pg_attribute att
        JOIN pg_class c ON c.oid = att.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(att.attacl) a
        LEFT JOIN pg_roles ro ON ro.oid = a.grantee
        WHERE n.nspname = 'public' 
          AND c.relname = 'risk_alerts'
          AND att.attnum > 0 
          AND NOT att.attisdropped
          AND (a.grantee = 0 OR ro.rolname IN ('anon', 'authenticated'))
        UNION
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
-- 5.1 Safe View Upgrade & Deep Compatibility Pre-Check:
-- Inspects existing v_public_risk_alerts column definition before replacement.
-- Compares:
--   1. Column names
--   2. Ordinal positions
--   3. Data types (udt_name)
--   4. Type modifiers (character_maximum_length)
-- against the canonical SELECT projection from public.risk_alerts.
--
-- If the existing view's column schema differs, it queries pg_depend for external
-- dependent objects (other views, materialized views, functions, policies).
--   - If dependencies exist: Aborts safely via RAISE EXCEPTION listing dependent names and types.
--     NEVER uses DROP VIEW CASCADE.
--   - If 0 dependencies exist: Safely drops the incompatible view with un-cascaded DROP VIEW.
-- If the existing view is compatible: Preserves existing dependencies and applies
-- CREATE OR REPLACE VIEW with security_invoker = true.
DO $$
DECLARE
    diff_count INT := 0;
    dep_count INT := 0;
    dep_details TEXT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_views 
        WHERE schemaname = 'public' AND viewname = 'v_public_risk_alerts'
    ) THEN
        -- Compare existing view columns against canonical projection from public.risk_alerts
        WITH canonical_proj AS (
            SELECT 
                column_name,
                row_number() OVER (ORDER BY 
                    CASE column_name
                        WHEN 'id' THEN 1
                        WHEN 'location_id' THEN 2
                        WHEN 'alert_type' THEN 3
                        WHEN 'severity' THEN 4
                        WHEN 'status' THEN 5
                        WHEN 'title' THEN 6
                        WHEN 'description' THEN 7
                        WHEN 'evidence' THEN 8
                        WHEN 'created_at' THEN 9
                        WHEN 'updated_at' THEN 10
                        WHEN 'acknowledged_at' THEN 11
                        WHEN 'resolved_at' THEN 12
                        WHEN 'resolved_reason' THEN 13
                    END
                ) AS expected_pos,
                udt_name AS expected_type,
                character_maximum_length AS expected_len
            FROM information_schema.columns
            WHERE table_schema = 'public' 
              AND table_name = 'risk_alerts'
              AND column_name IN (
                'id', 'location_id', 'alert_type', 'severity', 'status',
                'title', 'description', 'evidence', 'created_at', 'updated_at',
                'acknowledged_at', 'resolved_at', 'resolved_reason'
              )
        ),
        existing_view_cols AS (
            SELECT 
                column_name,
                ordinal_position,
                udt_name,
                character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts'
        )
        SELECT count(*)
        INTO diff_count
        FROM canonical_proj c
        FULL OUTER JOIN existing_view_cols v
          ON c.expected_pos = v.ordinal_position
        WHERE c.column_name IS DISTINCT FROM v.column_name
           OR c.expected_type IS DISTINCT FROM v.udt_name
           OR c.expected_len IS DISTINCT FROM v.character_maximum_length;

        IF diff_count > 0 THEN
            -- Columns, positions, types, or length modifiers differ.
            -- Scan pg_depend for external dependencies before taking action.
            SELECT 
                count(*),
                string_agg(
                    format('%s (%s)', 
                        coalesce(c.relname, p.proname, pol.polname, 'obj_' || d.objid),
                        CASE 
                            WHEN c.relkind = 'v' THEN 'view'
                            WHEN c.relkind = 'm' THEN 'materialized view'
                            WHEN c.relkind = 'r' THEN 'table'
                            WHEN p.proname IS NOT NULL THEN 'function'
                            WHEN pol.polname IS NOT NULL THEN 'policy'
                            ELSE 'dependent_object'
                        END
                    ), 
                    ', '
                )
            INTO dep_count, dep_details
            FROM pg_depend d
            LEFT JOIN pg_rewrite rw ON d.objid = rw.oid AND d.classid = 'pg_rewrite'::regclass
            LEFT JOIN pg_class c ON (c.oid = rw.ev_class OR c.oid = d.objid)
            LEFT JOIN pg_proc p ON d.objid = p.oid AND d.classid = 'pg_proc'::regclass
            LEFT JOIN pg_policy pol ON d.objid = pol.oid AND d.classid = 'pg_policy'::regclass
            WHERE d.refobjid = 'public.v_public_risk_alerts'::regclass
              AND d.deptype IN ('n', 'a')
              AND (c.oid IS NULL OR c.oid != 'public.v_public_risk_alerts'::regclass)
              AND (d.classid != 'pg_type'::regclass);

            IF dep_count > 0 THEN
                RAISE EXCEPTION 'Migration 005 view compatibility check failed: Existing view public.v_public_risk_alerts has % incompatible column definition(s) with canonical contract and % external dependent object(s): [%]. Cannot replace without breaking dependencies. DROP VIEW CASCADE is strictly disallowed. Please reconcile dependencies before re-running migration.', 
                    diff_count, dep_count, dep_details;
            ELSE
                -- No dependent objects: safely drop without cascade
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
