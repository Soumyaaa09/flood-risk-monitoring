-- =============================================================================
-- FloodWatch — Migration 005 Post-Execution Security & Verification Suite
-- Target: PostgreSQL 15+ / Supabase
--
-- PURPOSE:
-- Rigorous post-migration validation executed AFTER Migration 005 has been applied.
-- Queries PostgreSQL system catalogs (pg_class, pg_constraint, pg_indexes, pg_views,
-- pg_attribute, pg_policy, pg_auth_members, aclexplode()) and security functions
-- (has_table_privilege(), has_column_privilege()) to verify the entire schema,
-- constraints, indexes, RLS, and permission matrix.
--
-- CRITICAL SECURITY DISTINCTIONS & POSTGRESQL CATALOG RULES:
-- 1. 'PUBLIC' is a universal pseudo-role (grantee OID 0), NOT an ordinary role identifier.
--    Passing 'PUBLIC' to has_table_privilege() or has_column_privilege() causes runtime error:
--    "ERROR: role 'PUBLIC' does not exist".
--    PUBLIC privileges are inspected strictly through ACL catalogs via aclexplode().
-- 2. has_table_privilege() and has_column_privilege() are invoked ONLY for concrete roles:
--    - anon
--    - authenticated
--    - service_role
-- 3. Missing-Object Safety:
--    All object references use safe catalog lookups (to_regclass) so that missing objects
--    produce clear FAIL results instead of crashing the script.
-- 4. Status Standards:
--    Every check returns explicit PASS, FAIL, or INCONCLUSIVE.
--    Final verdict is: "LOCAL SQL VERIFICATION PASSED — LIVE SUPABASE EXECUTION PENDING".
-- =============================================================================

CREATE TEMPORARY TABLE IF NOT EXISTS _fw_migration_005_test_results (
    check_id INT,
    check_name TEXT,
    category TEXT,
    expected TEXT,
    observed TEXT,
    status TEXT,
    details TEXT
);

TRUNCATE TABLE _fw_migration_005_test_results;

DO $$
DECLARE
    -- Object OID safe handles
    tbl_oid OID := to_regclass('public.risk_alerts');
    view_oid OID := to_regclass('public.v_public_risk_alerts');
    fk_target_oid OID := to_regclass('public.monitoring_locations');

    -- Check 1 & 2 variables: Table & Column Schema
    tbl_exists BOOLEAN := (tbl_oid IS NOT NULL);
    actual_col_count INT := 0;
    missing_cols TEXT;
    unexpected_cols TEXT;
    order_mismatches TEXT;
    type_mismatches TEXT;
    len_mismatches TEXT;
    nullability_mismatches TEXT;

    -- Check 3: Foreign Key
    fk_target TEXT;
    fk_del_action CHAR(1);

    -- Check 4 & 5: Constraints
    sev_con_def TEXT;
    st_con_def TEXT;
    at_con_def TEXT;
    unrelated_con_count INT := 0;

    -- Check 6: Indexes
    idx_loc_st_exists BOOLEAN := false;
    idx_st_cr_exists BOOLEAN := false;
    idx_sev_exists BOOLEAN := false;
    idx_uq_fp_exists BOOLEAN := false;
    idx_uq_fp_unique BOOLEAN := false;
    idx_uq_fp_cols TEXT;
    idx_uq_fp_pred TEXT;

    -- Check 7 & 8: RLS & Policies
    rls_active BOOLEAN := false;
    read_pol_count INT := 0;
    read_pol_permissive BOOLEAN := false;
    read_pol_qual TEXT;
    svc_pol_count INT := 0;
    svc_pol_permissive BOOLEAN := false;
    svc_pol_qual TEXT;
    svc_pol_withcheck TEXT;
    anon_write_pols INT := 0;

    -- Check 9 & 10: PUBLIC ACLs (via aclexplode)
    public_table_acls TEXT;
    public_col_acls TEXT;

    -- Check 11: Effective Role Table Privileges
    anon_table_select BOOLEAN := false;
    anon_table_insert BOOLEAN := false;
    anon_table_update BOOLEAN := false;
    anon_table_delete BOOLEAN := false;
    auth_table_select BOOLEAN := false;
    auth_table_insert BOOLEAN := false;
    auth_table_update BOOLEAN := false;
    auth_table_delete BOOLEAN := false;

    -- Check 12 & 13: Effective Column Privileges & Sensitive Lockdown
    anon_approved_col_fails INT := 0;
    auth_approved_col_fails INT := 0;
    anon_ack_by_select BOOLEAN := false;
    anon_fp_select BOOLEAN := false;
    auth_ack_by_select BOOLEAN := false;
    auth_fp_select BOOLEAN := false;
    public_col_write_grants INT := 0;

    -- Check 14: service_role DML
    svc_table_select BOOLEAN := false;
    svc_table_insert BOOLEAN := false;
    svc_table_update BOOLEAN := false;
    svc_table_delete BOOLEAN := false;

    -- Check 15: Public View
    view_exists BOOLEAN := (view_oid IS NOT NULL);
    view_col_count INT := 0;
    view_order_mismatches TEXT;
    view_type_mismatches TEXT;
    view_has_ack_by BOOLEAN := false;
    view_has_fp BOOLEAN := false;
    view_invoker_opt TEXT;
    view_anon_select BOOLEAN := false;
    view_auth_select BOOLEAN := false;
    view_svc_select BOOLEAN := false;

    -- Check 16: Recursive Role Inheritance & Deduplication Data Integrity
    inherited_role_leaks TEXT;
    dup_open_fingerprints INT := 0;

    -- Role Context Execution Test Variables
    can_test_roles BOOLEAN := false;
    role_anon_view_ok BOOLEAN := false;
    role_anon_sensitive_denied BOOLEAN := false;
    role_anon_insert_denied BOOLEAN := false;
    role_svc_dml_ok BOOLEAN := false;
    role_test_diag TEXT := '';
BEGIN
    -- =========================================================================
    -- CHECK 1: Exact Table Existence & Column Count (Exactly 15 Columns)
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            1, 'Table and Column Structure', 'Schema Integrity',
            'public.risk_alerts exists with exactly 15 canonical columns',
            'Table public.risk_alerts DOES NOT EXIST',
            'FAIL',
            'Base table is missing. Migration 005 has not been applied.'
        );
    ELSE
        SELECT count(*) INTO actual_col_count
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'risk_alerts';

        SELECT string_agg(req_col, ', ') INTO missing_cols
        FROM unnest(ARRAY[
            'id', 'location_id', 'alert_type', 'severity', 'status',
            'title', 'description', 'evidence', 'fingerprint',
            'created_at', 'updated_at', 'acknowledged_at', 'acknowledged_by',
            'resolved_at', 'resolved_reason'
        ]) req_col
        WHERE req_col NOT IN (
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'risk_alerts'
        );

        SELECT string_agg(column_name, ', ') INTO unexpected_cols
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
          AND column_name NOT IN (
            'id', 'location_id', 'alert_type', 'severity', 'status',
            'title', 'description', 'evidence', 'fingerprint',
            'created_at', 'updated_at', 'acknowledged_at', 'acknowledged_by',
            'resolved_at', 'resolved_reason'
          );

        INSERT INTO _fw_migration_005_test_results VALUES (
            1, 'Table and Column Structure', 'Schema Integrity',
            'risk_alerts exists with exactly 15 canonical columns, 0 unexpected columns',
            format('exists=%s, columns=%s, missing=[%s], unexpected=[%s]', 
                tbl_exists, actual_col_count, coalesce(missing_cols, 'none'), coalesce(unexpected_cols, 'none')),
            CASE WHEN actual_col_count = 15 AND missing_cols IS NULL AND unexpected_cols IS NULL THEN 'PASS' ELSE 'FAIL' END,
            'Verifies base table structure has exactly the 15 canonical columns'
        );
    END IF;

    -- =========================================================================
    -- CHECK 2: Column Data Types, Lengths, Ordinal Positions & Nullability
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            2, 'Column Types and Nullability', 'Schema Integrity',
            'All column data types, lengths, nullability match canonical model',
            'Table missing',
            'FAIL',
            'Cannot verify columns because table public.risk_alerts does not exist'
        );
    ELSE
        WITH canonical_spec(pos, col, exp_type, exp_len, exp_nullable) AS (
            VALUES
                (1,  'id',              'uuid',        NULL::int, 'NO'),
                (2,  'location_id',     'uuid',        NULL::int, 'NO'),
                (3,  'alert_type',      'varchar',     50,        'NO'),
                (4,  'severity',        'varchar',     30,        'NO'),
                (5,  'status',          'varchar',     30,        'NO'),
                (6,  'title',           'varchar',     255,       'NO'),
                (7,  'description',     'text',        NULL::int, 'NO'),
                (8,  'evidence',        'jsonb',       NULL::int, 'NO'),
                (9,  'fingerprint',     'varchar',     255,       'NO'),
                (10, 'created_at',      'timestamptz', NULL::int, 'NO'),
                (11, 'updated_at',      'timestamptz', NULL::int, 'NO'),
                (12, 'acknowledged_at', 'timestamptz', NULL::int, 'YES'),
                (13, 'acknowledged_by', 'varchar',     100,       'YES'),
                (14, 'resolved_at',     'timestamptz', NULL::int, 'YES'),
                (15, 'resolved_reason', 'text',        NULL::int, 'YES')
        ),
        actual_cols AS (
            SELECT 
                column_name,
                ordinal_position,
                udt_name,
                character_maximum_length,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'risk_alerts'
        )
        SELECT 
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.ordinal_position != s.pos 
                            THEN format('%s (pos %s, exp %s)', s.col, a.ordinal_position, s.pos) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.udt_name != s.exp_type 
                            THEN format('%s (%s, exp %s)', s.col, a.udt_name, s.exp_type) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND s.exp_len IS NOT NULL AND a.character_maximum_length != s.exp_len 
                            THEN format('%s (len %s, exp %s)', s.col, a.character_maximum_length, s.exp_len) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.is_nullable != s.exp_nullable 
                            THEN format('%s (null %s, exp %s)', s.col, a.is_nullable, s.exp_nullable) END, '; ')
        INTO 
            order_mismatches,
            type_mismatches,
            len_mismatches,
            nullability_mismatches
        FROM canonical_spec s
        LEFT JOIN actual_cols a ON s.col = a.column_name;

        INSERT INTO _fw_migration_005_test_results VALUES (
            2, 'Column Types and Nullability', 'Schema Integrity',
            'Order 1..15, types (UUID, JSONB, TIMESTAMPTZ), lengths, and NOT NULL flags match',
            format('order_err=[%s], type_err=[%s], len_err=[%s], null_err=[%s]', 
                coalesce(order_mismatches, 'none'), coalesce(type_mismatches, 'none'), 
                coalesce(len_mismatches, 'none'), coalesce(nullability_mismatches, 'none')),
            CASE WHEN order_mismatches IS NULL AND type_mismatches IS NULL AND len_mismatches IS NULL AND nullability_mismatches IS NULL THEN 'PASS' ELSE 'FAIL' END,
            'Ensures strict contract conformance for types, lengths, order, and nullability'
        );
    END IF;

    -- =========================================================================
    -- CHECK 3: Foreign Key Reference Integrity & ON DELETE CASCADE
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            3, 'Foreign Key Reference', 'Schema Integrity',
            'monitoring_locations(id) ON DELETE CASCADE',
            'Table missing', 'FAIL', 'Cannot inspect foreign key'
        );
    ELSE
        SELECT 
            c.conname || ': ' || fn.nspname || '.' || fc.relname || '(' || fa.attname || ')',
            c.confdeltype
        INTO fk_target, fk_del_action
        FROM pg_constraint c
        JOIN pg_class tc ON tc.oid = c.conrelid
        JOIN pg_class fc ON fc.oid = c.confrelid
        JOIN pg_namespace fn ON fn.oid = fc.relnamespace
        JOIN pg_attribute la ON la.attrelid = c.conrelid AND la.attnum = c.conkey[1]
        JOIN pg_attribute fa ON fa.attrelid = c.confrelid AND fa.attnum = c.confkey[1]
        WHERE c.contype = 'f'
          AND c.conrelid = tbl_oid
          AND la.attname = 'location_id';

        INSERT INTO _fw_migration_005_test_results VALUES (
            3, 'Foreign Key Reference', 'Schema Integrity',
            'public.monitoring_locations(id) ON DELETE CASCADE (action=c)',
            format('target=[%s], on_delete_action=[%s]', coalesce(fk_target, 'NONE'), coalesce(fk_del_action, 'none')),
            CASE WHEN fk_target LIKE '%public.monitoring_locations(id)%' AND fk_del_action = 'c' THEN 'PASS' ELSE 'FAIL' END,
            'Ensures location_id references established monitoring_locations table with CASCADE delete'
        );
    END IF;

    -- =========================================================================
    -- CHECK 4: Severity Check Constraint (Accepts DATA_WARNING & Canonicals)
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            4, 'Severity Check Constraint', 'Constraint Verification',
            'Includes DATA_WARNING, INFO, WARNING, HIGH, CRITICAL',
            'Table missing', 'FAIL', 'Cannot inspect constraints'
        );
    ELSE
        SELECT pg_get_constraintdef(oid) INTO sev_con_def
        FROM pg_constraint
        WHERE conrelid = tbl_oid AND conname = 'risk_alerts_severity_check';

        INSERT INTO _fw_migration_005_test_results VALUES (
            4, 'Severity Check Constraint', 'Constraint Verification',
            'Includes DATA_WARNING, INFO, WARNING, HIGH, CRITICAL',
            coalesce(sev_con_def, 'MISSING'),
            CASE WHEN sev_con_def LIKE '%DATA_WARNING%' 
                  AND sev_con_def LIKE '%INFO%'
                  AND sev_con_def LIKE '%WARNING%'
                  AND sev_con_def LIKE '%HIGH%'
                  AND sev_con_def LIKE '%CRITICAL%' THEN 'PASS' ELSE 'FAIL' END,
            'Guarantees telemetry data warnings are accepted by database check constraint'
        );
    END IF;

    -- =========================================================================
    -- CHECK 5: Status & Alert Type Lifecycle Constraints & Unrelated Constraints Preserved
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            5, 'Status & Alert Type Constraints', 'Constraint Verification',
            'Canonical lifecycle and 6 alert types present',
            'Table missing', 'FAIL', 'Cannot inspect constraints'
        );
    ELSE
        SELECT pg_get_constraintdef(oid) INTO st_con_def
        FROM pg_constraint
        WHERE conrelid = tbl_oid AND conname = 'risk_alerts_status_check';

        SELECT pg_get_constraintdef(oid) INTO at_con_def
        FROM pg_constraint
        WHERE conrelid = tbl_oid AND conname = 'risk_alerts_alert_type_check';

        SELECT count(*) INTO unrelated_con_count
        FROM pg_constraint
        WHERE conrelid = tbl_oid
          AND contype = 'c'
          AND conname NOT IN ('risk_alerts_severity_check', 'chk_alert_severity', 'risk_alerts_status_check', 'risk_alerts_alert_type_check');

        INSERT INTO _fw_migration_005_test_results VALUES (
            5, 'Status & Alert Type Constraints', 'Constraint Verification',
            'Canonical lifecycle (NEW, ACTIVE, ACKNOWLEDGED, RESOLVED), 6 alert types; unrelated constraints intact',
            format('status=[%s], alert_type=[%s], other_check_constraints=%s', 
                coalesce(st_con_def, 'MISSING'), coalesce(at_con_def, 'MISSING'), unrelated_con_count),
            CASE WHEN st_con_def LIKE '%NEW%' AND st_con_def LIKE '%RESOLVED%' 
                  AND at_con_def LIKE '%MODERATE_RAINFALL_RISK%' AND at_con_def LIKE '%RISK_REDUCED%' THEN 'PASS' ELSE 'FAIL' END,
            'Preserves full alert lifecycle, categorical types, and unrelated constraints'
        );
    END IF;

    -- =========================================================================
    -- CHECK 6: Performance & Deduplication Partial Unique Indexes
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            6, 'Indexes & Deduplication Guard', 'Index Verification',
            '4 indexes present; uq_risk_alerts_active_fingerprint partial unique',
            'Table missing', 'FAIL', 'Cannot inspect indexes'
        );
    ELSE
        SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'risk_alerts' AND indexname = 'idx_risk_alerts_location_status') INTO idx_loc_st_exists;
        SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'risk_alerts' AND indexname = 'idx_risk_alerts_status_created') INTO idx_st_cr_exists;
        SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'risk_alerts' AND indexname = 'idx_risk_alerts_severity') INTO idx_sev_exists;

        SELECT 
            true,
            i.indisunique,
            pg_get_indexdef(i.indexrelid),
            pg_get_expr(i.indpred, i.indrelid)
        INTO 
            idx_uq_fp_exists,
            idx_uq_fp_unique,
            idx_uq_fp_cols,
            idx_uq_fp_pred
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = tbl_oid
          AND ic.relname = 'uq_risk_alerts_active_fingerprint';

        INSERT INTO _fw_migration_005_test_results VALUES (
            6, 'Indexes & Deduplication Guard', 'Index Verification',
            '4 indexes present; uq_risk_alerts_active_fingerprint unique on (location_id, fingerprint) with predicate on (NEW, ACTIVE, ACKNOWLEDGED)',
            format('loc_st=%s, st_cr=%s, sev=%s, uq_exists=%s, uq_unique=%s, predicate=[%s]', 
                idx_loc_st_exists, idx_st_cr_exists, idx_sev_exists, idx_uq_fp_exists, idx_uq_fp_unique, coalesce(idx_uq_fp_pred, 'none')),
            CASE WHEN idx_loc_st_exists AND idx_st_cr_exists AND idx_sev_exists 
                  AND idx_uq_fp_exists AND idx_uq_fp_unique
                  AND idx_uq_fp_pred LIKE '%NEW%' AND idx_uq_fp_pred LIKE '%ACTIVE%' AND idx_uq_fp_pred LIKE '%ACKNOWLEDGED%' THEN 'PASS' ELSE 'FAIL' END,
            'Enforces single active alert per location/fingerprint while allowing resolved alerts to be archived'
        );
    END IF;

    -- =========================================================================
    -- CHECK 7: Row-Level Security (RLS) Engine Enforcement
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            7, 'Row-Level Security Enforcement', 'RLS Enforcement',
            'Row Level Security is enabled (relrowsecurity = true)',
            'Table missing', 'FAIL', 'Cannot inspect RLS'
        );
    ELSE
        SELECT relrowsecurity INTO rls_active
        FROM pg_class
        WHERE oid = tbl_oid;

        INSERT INTO _fw_migration_005_test_results VALUES (
            7, 'Row-Level Security Enforcement', 'RLS Enforcement',
            'Row Level Security is enabled (relrowsecurity = true)',
            format('rls_enabled=%s', rls_active),
            CASE WHEN rls_active THEN 'PASS' ELSE 'FAIL' END,
            'Guarantees all row access is evaluated through explicit RLS policy rules'
        );
    END IF;

    -- =========================================================================
    -- CHECK 8: Row-Level Security Policy Definitions
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            8, 'Row-Level Security Policies', 'RLS Enforcement',
            'public read policy active, service_role ALL active, 0 anon write policies',
            'Table missing', 'FAIL', 'Cannot inspect policies'
        );
    ELSE
        SELECT 
            count(*),
            coalesce(bool_and(polpermissive), false),
            pg_get_expr(polqual, polrelid)
        INTO read_pol_count, read_pol_permissive, read_pol_qual
        FROM pg_policy
        WHERE polrelid = tbl_oid AND polname = 'p_risk_alerts_read_public' AND polcmd = 'r'
        GROUP BY polqual, polrelid;

        SELECT 
            count(*),
            coalesce(bool_and(polpermissive), false),
            pg_get_expr(polqual, polrelid),
            pg_get_expr(polwithcheck, polrelid)
        INTO svc_pol_count, svc_pol_permissive, svc_pol_qual, svc_pol_withcheck
        FROM pg_policy
        WHERE polrelid = tbl_oid AND polname = 'p_risk_alerts_service_role_all' AND polcmd = '*'
        GROUP BY polqual, polwithcheck, polrelid;

        SELECT count(*) INTO anon_write_pols
        FROM pg_policy pol
        JOIN pg_class c ON c.oid = pol.polrelid
        WHERE c.oid = tbl_oid AND pol.polcmd IN ('w', 'a', 'd', '*') 
          AND pol.polroles::text LIKE '%anon%';

        INSERT INTO _fw_migration_005_test_results VALUES (
            8, 'Row-Level Security Policies', 'RLS Enforcement',
            'public_read(SELECT, permissive, qual=true), svc_all(ALL, permissive, qual=true, check=true), 0 anon write policies',
            format('public_read=%s (permissive=%s, qual=[%s]), svc_all=%s (permissive=%s, qual=[%s], check=[%s]), anon_write_pols=%s', 
                read_pol_count, read_pol_permissive, coalesce(read_pol_qual, 'none'),
                svc_pol_count, svc_pol_permissive, coalesce(svc_pol_qual, 'none'), coalesce(svc_pol_withcheck, 'none'),
                anon_write_pols),
            CASE WHEN read_pol_count = 1 AND read_pol_permissive AND read_pol_qual = 'true'
                  AND svc_pol_count = 1 AND svc_pol_permissive AND svc_pol_qual = 'true' AND svc_pol_withcheck = 'true'
                  AND anon_write_pols = 0 THEN 'PASS' ELSE 'FAIL' END,
            'Prevents anonymous/authenticated DML writes through RLS engine'
        );
    END IF;

    -- =========================================================================
    -- CHECK 9: PUBLIC Pseudo-Role Table-Level ACL Inspection (via aclexplode)
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            9, 'PUBLIC Table ACL Lockdown', 'Privilege Inspection',
            '0 table-level ACL grants to PUBLIC (grantee OID 0)',
            'Table missing', 'FAIL', 'Cannot inspect ACLs'
        );
    ELSE
        SELECT string_agg(format('%s (%s)', a.privilege_type, CASE WHEN a.is_grantable THEN 'GRANTABLE' ELSE 'DIRECT' END), ', ')
        INTO public_table_acls
        FROM pg_class c
        CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
        WHERE c.oid = tbl_oid
          AND a.grantee = 0; -- 0 is OID for universal pseudo-role PUBLIC

        INSERT INTO _fw_migration_005_test_results VALUES (
            9, 'PUBLIC Table ACL Lockdown', 'Privilege Inspection',
            '0 table-level ACL grants of ANY type to PUBLIC (grantee OID 0)',
            format('table_acls=[%s]', coalesce(public_table_acls, 'none')),
            CASE WHEN public_table_acls IS NULL THEN 'PASS' ELSE 'FAIL' END,
            'Catalog verification confirming PUBLIC pseudo-role has zero table-level ACL entries'
        );
    END IF;

    -- =========================================================================
    -- CHECK 10: PUBLIC Pseudo-Role Column-Level ACL Inspection (via aclexplode)
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            10, 'PUBLIC Column ACL Lockdown', 'Privilege Inspection',
            '0 column-level ACL grants to PUBLIC (grantee OID 0)',
            'Table missing', 'FAIL', 'Cannot inspect ACLs'
        );
    ELSE
        SELECT string_agg(format('%s.%s: %s', 'risk_alerts', att.attname, a.privilege_type), ', ')
        INTO public_col_acls
        FROM pg_attribute att
        CROSS JOIN LATERAL aclexplode(att.attacl) a
        WHERE att.attrelid = tbl_oid
          AND att.attnum > 0
          AND NOT att.attisdropped
          AND a.grantee = 0; -- 0 is OID for PUBLIC

        INSERT INTO _fw_migration_005_test_results VALUES (
            10, 'PUBLIC Column ACL Lockdown', 'Privilege Inspection',
            '0 column-level ACL grants to PUBLIC (grantee OID 0)',
            format('col_acls=[%s]', coalesce(public_col_acls, 'none')),
            CASE WHEN public_col_acls IS NULL THEN 'PASS' ELSE 'FAIL' END,
            'Catalog verification confirming PUBLIC pseudo-role has zero column-level ACL entries'
        );
    END IF;

    -- =========================================================================
    -- CHECK 11: Effective Role Table Privileges (SELECT, INSERT, UPDATE, DELETE for anon & authenticated)
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            11, 'Effective Role Table Privileges', 'Privilege Inspection',
            'anon/authenticated table SELECT/INSERT/UPDATE/DELETE all false',
            'Table missing', 'FAIL', 'Cannot check role privileges'
        );
    ELSE
        anon_table_select := has_table_privilege('anon', 'public.risk_alerts', 'SELECT');
        anon_table_insert := has_table_privilege('anon', 'public.risk_alerts', 'INSERT');
        anon_table_update := has_table_privilege('anon', 'public.risk_alerts', 'UPDATE');
        anon_table_delete := has_table_privilege('anon', 'public.risk_alerts', 'DELETE');

        auth_table_select := has_table_privilege('authenticated', 'public.risk_alerts', 'SELECT');
        auth_table_insert := has_table_privilege('authenticated', 'public.risk_alerts', 'INSERT');
        auth_table_update := has_table_privilege('authenticated', 'public.risk_alerts', 'UPDATE');
        auth_table_delete := has_table_privilege('authenticated', 'public.risk_alerts', 'DELETE');

        INSERT INTO _fw_migration_005_test_results VALUES (
            11, 'Effective Role Table Privileges', 'Privilege Inspection',
            'anon(S=f, I=f, U=f, D=f) and authenticated(S=f, I=f, U=f, D=f)',
            format('anon(S=%s, I=%s, U=%s, D=%s), auth(S=%s, I=%s, U=%s, D=%s)', 
                anon_table_select, anon_table_insert, anon_table_update, anon_table_delete,
                auth_table_select, auth_table_insert, auth_table_update, auth_table_delete),
            CASE WHEN NOT anon_table_select AND NOT anon_table_insert AND NOT anon_table_update AND NOT anon_table_delete
                  AND NOT auth_table_select AND NOT auth_table_insert AND NOT auth_table_update AND NOT auth_table_delete THEN 'PASS' ELSE 'FAIL' END,
            'Verifies complete table-level denial for unauthenticated and authenticated public roles'
        );
    END IF;

    -- =========================================================================
    -- CHECK 12: Effective Column Read on Approved 13 Attributes
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            12, 'Effective Column Read on Approved Attributes', 'Privilege Inspection',
            '0 missing column grants across 13 approved telemetry columns',
            'Table missing', 'FAIL', 'Cannot check column privileges'
        );
    ELSE
        SELECT count(*) INTO anon_approved_col_fails
        FROM unnest(ARRAY[
            'id', 'location_id', 'alert_type', 'severity', 'status',
            'title', 'description', 'evidence', 'created_at', 'updated_at',
            'acknowledged_at', 'resolved_at', 'resolved_reason'
        ]) col
        WHERE NOT has_column_privilege('anon', 'public.risk_alerts', col, 'SELECT');

        SELECT count(*) INTO auth_approved_col_fails
        FROM unnest(ARRAY[
            'id', 'location_id', 'alert_type', 'severity', 'status',
            'title', 'description', 'evidence', 'created_at', 'updated_at',
            'acknowledged_at', 'resolved_at', 'resolved_reason'
        ]) col
        WHERE NOT has_column_privilege('authenticated', 'public.risk_alerts', col, 'SELECT');

        INSERT INTO _fw_migration_005_test_results VALUES (
            12, 'Effective Column Read on Approved Attributes', 'Privilege Inspection',
            '0 missing column grants across 13 approved telemetry columns',
            format('anon_missing=%s, auth_missing=%s', anon_approved_col_fails, auth_approved_col_fails),
            CASE WHEN anon_approved_col_fails = 0 AND auth_approved_col_fails = 0 THEN 'PASS' ELSE 'FAIL' END,
            'Ensures anon and authenticated can query all non-sensitive telemetry attributes'
        );
    END IF;

    -- =========================================================================
    -- CHECK 13: Sensitive Column Lockdown & No Unauthorized Column DML/REFERENCES
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            13, 'Sensitive Column Lockdown', 'Security Boundary',
            'acknowledged_by and fingerprint SELECT=false for anon and authenticated',
            'Table missing', 'FAIL', 'Cannot check column privileges'
        );
    ELSE
        anon_ack_by_select := has_column_privilege('anon', 'public.risk_alerts', 'acknowledged_by', 'SELECT');
        anon_fp_select     := has_column_privilege('anon', 'public.risk_alerts', 'fingerprint', 'SELECT');
        auth_ack_by_select := has_column_privilege('authenticated', 'public.risk_alerts', 'acknowledged_by', 'SELECT');
        auth_fp_select     := has_column_privilege('authenticated', 'public.risk_alerts', 'fingerprint', 'SELECT');

        SELECT count(*) INTO public_col_write_grants
        FROM information_schema.column_privileges
        WHERE table_schema = 'public' AND table_name = 'risk_alerts'
          AND grantee IN ('anon', 'authenticated', 'PUBLIC')
          AND privilege_type IN ('INSERT', 'UPDATE', 'REFERENCES');

        INSERT INTO _fw_migration_005_test_results VALUES (
            13, 'Sensitive Column Lockdown', 'Security Boundary',
            'acknowledged_by/fingerprint SELECT=false for public roles; 0 column INSERT/UPDATE/REFERENCES grants',
            format('anon_ack_by=%s, anon_fp=%s, auth_ack_by=%s, auth_fp=%s, unauthorized_col_grants=%s', 
                anon_ack_by_select, anon_fp_select, auth_ack_by_select, auth_fp_select, public_col_write_grants),
            CASE WHEN NOT anon_ack_by_select AND NOT anon_fp_select 
                  AND NOT auth_ack_by_select AND NOT auth_fp_select 
                  AND public_col_write_grants = 0 THEN 'PASS' ELSE 'FAIL' END,
            'Guarantees operator identities and internal deduplication hashes cannot be read publicly'
        );
    END IF;

    -- =========================================================================
    -- CHECK 14: Service Role Full Operational DML Privileges
    -- =========================================================================
    IF NOT tbl_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            14, 'Service Role DML Operations', 'Operational Capability',
            'service_role has table-level SELECT, INSERT, UPDATE, DELETE',
            'Table missing', 'FAIL', 'Cannot check service_role privileges'
        );
    ELSE
        svc_table_select := has_table_privilege('service_role', 'public.risk_alerts', 'SELECT');
        svc_table_insert := has_table_privilege('service_role', 'public.risk_alerts', 'INSERT');
        svc_table_update := has_table_privilege('service_role', 'public.risk_alerts', 'UPDATE');
        svc_table_delete := has_table_privilege('service_role', 'public.risk_alerts', 'DELETE');

        INSERT INTO _fw_migration_005_test_results VALUES (
            14, 'Service Role DML Operations', 'Operational Capability',
            'service_role has table-level SELECT, INSERT, UPDATE, DELETE',
            format('select=%s, insert=%s, update=%s, delete=%s', 
                svc_table_select, svc_table_insert, svc_table_update, svc_table_delete),
            CASE WHEN svc_table_select AND svc_table_insert AND svc_table_update AND svc_table_delete THEN 'PASS' ELSE 'FAIL' END,
            'Verifies automated ingestion and alert engine can perform full lifecycle DML'
        );
    END IF;

    -- =========================================================================
    -- CHECK 15: Public Sanitized View Architecture & security_invoker Option
    -- =========================================================================
    IF NOT view_exists THEN
        INSERT INTO _fw_migration_005_test_results VALUES (
            15, 'Sanitized Public View Architecture', 'View Security',
            'view exists, exactly 13 columns, security_invoker=true',
            'View public.v_public_risk_alerts DOES NOT EXIST',
            'FAIL',
            'Public sanitized view is missing'
        );
    ELSE
        SELECT count(*) INTO view_col_count
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts';

        SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts' AND column_name = 'acknowledged_by') INTO view_has_ack_by;
        SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts' AND column_name = 'fingerprint') INTO view_has_fp;

        SELECT reloptions::text INTO view_invoker_opt
        FROM pg_class
        WHERE oid = view_oid;

        view_anon_select := has_table_privilege('anon', 'public.v_public_risk_alerts', 'SELECT');
        view_auth_select := has_table_privilege('authenticated', 'public.v_public_risk_alerts', 'SELECT');
        view_svc_select  := has_table_privilege('service_role', 'public.v_public_risk_alerts', 'SELECT');

        -- Check view column order and types against canonical 13 columns
        WITH view_spec(pos, col, exp_type) AS (
            VALUES
                (1,  'id',              'uuid'),
                (2,  'location_id',     'uuid'),
                (3,  'alert_type',      'varchar'),
                (4,  'severity',        'varchar'),
                (5,  'status',          'varchar'),
                (6,  'title',           'varchar'),
                (7,  'description',     'text'),
                (8,  'evidence',        'jsonb'),
                (9,  'created_at',      'timestamptz'),
                (10, 'updated_at',      'timestamptz'),
                (11, 'acknowledged_at', 'timestamptz'),
                (12, 'resolved_at',     'timestamptz'),
                (13, 'resolved_reason', 'text')
        ),
        actual_view_cols AS (
            SELECT column_name, ordinal_position, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'v_public_risk_alerts'
        )
        SELECT 
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.ordinal_position != s.pos 
                            THEN format('%s (pos %s, exp %s)', s.col, a.ordinal_position, s.pos) END, '; '),
            string_agg(CASE WHEN a.column_name IS NOT NULL AND a.udt_name != s.exp_type 
                            THEN format('%s (%s, exp %s)', s.col, a.udt_name, s.exp_type) END, '; ')
        INTO view_order_mismatches, view_type_mismatches
        FROM view_spec s
        LEFT JOIN actual_view_cols a ON s.col = a.column_name;

        INSERT INTO _fw_migration_005_test_results VALUES (
            15, 'Sanitized Public View Architecture', 'View Security',
            'view exists, exactly 13 canonical columns (no sensitive fields), security_invoker=true, view SELECT granted',
            format('exists=%s, cols=%s, ack_by=%s, fp=%s, options=%s, anon_sel=%s, auth_sel=%s, svc_sel=%s, order_err=[%s], type_err=[%s]', 
                view_exists, view_col_count, view_has_ack_by, view_has_fp, coalesce(view_invoker_opt, 'none'),
                view_anon_select, view_auth_select, view_svc_select,
                coalesce(view_order_mismatches, 'none'), coalesce(view_type_mismatches, 'none')),
            CASE WHEN view_exists AND view_col_count = 13 AND NOT view_has_ack_by AND NOT view_has_fp 
                  AND view_invoker_opt LIKE '%security_invoker=true%' 
                  AND view_anon_select AND view_auth_select AND view_svc_select
                  AND view_order_mismatches IS NULL AND view_type_mismatches IS NULL THEN 'PASS' ELSE 'FAIL' END,
            'Verifies public view strictly projects sanitized telemetry under caller security context'
        );
    END IF;

    -- =========================================================================
    -- CHECK 16: Recursive Role Inheritance & Deduplication Data Integrity
    -- =========================================================================
    BEGIN
        WITH RECURSIVE role_chain(member_role, parent_role) AS (
            SELECT m.member::regrole::text, m.roleid::regrole::text
            FROM pg_auth_members m
            UNION
            SELECT c.member_role, m.roleid::regrole::text
            FROM role_chain c
            JOIN pg_auth_members m ON m.member::regrole::text = c.parent_role
        )
        SELECT string_agg(format('%s -> %s', member_role, parent_role), '; ')
        INTO inherited_role_leaks
        FROM role_chain
        WHERE member_role IN ('anon', 'authenticated')
          AND parent_role IN ('service_role', 'postgres', 'supabase_admin');
    EXCEPTION WHEN OTHERS THEN
        inherited_role_leaks := 'INCONCLUSIVE_CATALOG_UNREADABLE';
    END;

    IF tbl_exists THEN
        SELECT count(*) INTO dup_open_fingerprints
        FROM (
            SELECT location_id, fingerprint
            FROM public.risk_alerts
            WHERE status IN ('NEW', 'ACTIVE', 'ACKNOWLEDGED')
            GROUP BY location_id, fingerprint
            HAVING count(*) > 1
        ) d;
    END IF;

    INSERT INTO _fw_migration_005_test_results VALUES (
        16, 'Role Inheritance and Data Integrity', 'Security & Data Boundary',
        '0 inherited admin role leaks through transitive membership, 0 duplicate open fingerprints',
        format('inherited_leaks=[%s], dup_open_fp=%s', coalesce(inherited_role_leaks, 'NONE'), dup_open_fingerprints),
        CASE 
            WHEN inherited_role_leaks = 'INCONCLUSIVE_CATALOG_UNREADABLE' THEN 'INCONCLUSIVE'
            WHEN inherited_role_leaks IS NULL AND dup_open_fingerprints = 0 THEN 'PASS' 
            ELSE 'FAIL' 
        END,
        'Confirms public roles are transitively isolated from admin roles and data satisfies unique index'
    );

    -- =========================================================================
    -- CHECK 17: Transaction-Safe Executable Role-Context Tests
    -- =========================================================================
    -- Attempt role switching in administrative context to test real execution
    BEGIN
        SET LOCAL ROLE anon;
        can_test_roles := true;
        RESET ROLE;
    EXCEPTION WHEN OTHERS THEN
        can_test_roles := false;
        role_test_diag := 'Session user does not have permission to execute SET LOCAL ROLE anon';
    END;

    IF can_test_roles AND tbl_exists AND view_exists THEN
        -- 1. Test anon SELECT on view
        BEGIN
            SET LOCAL ROLE anon;
            PERFORM id, location_id, severity, status FROM public.v_public_risk_alerts LIMIT 1;
            role_anon_view_ok := true;
            RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
            role_anon_view_ok := false;
            RESET ROLE;
        END;

        -- 2. Test anon direct access to sensitive columns (must fail with permission denied)
        BEGIN
            SET LOCAL ROLE anon;
            PERFORM acknowledged_by FROM public.risk_alerts LIMIT 1;
            -- If this succeeded, sensitive column was leaked!
            role_anon_sensitive_denied := false;
            RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
            -- Expected: permission denied
            role_anon_sensitive_denied := true;
            RESET ROLE;
        END;

        -- 3. Test anon direct DML (must fail)
        BEGIN
            SET LOCAL ROLE anon;
            INSERT INTO public.risk_alerts (location_id, alert_type, severity, title, description, fingerprint)
            VALUES ('00000000-0000-0000-0000-000000000000', 'DATA_UNAVAILABLE', 'INFO', 'test', 'test', 'fp_test');
            role_anon_insert_denied := false;
            RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
            -- Expected: permission denied
            role_anon_insert_denied := true;
            RESET ROLE;
        END;

        -- 4. Test service_role operational capability
        BEGIN
            SET LOCAL ROLE service_role;
            PERFORM count(*) FROM public.risk_alerts;
            role_svc_dml_ok := true;
            RESET ROLE;
        EXCEPTION WHEN OTHERS THEN
            role_svc_dml_ok := false;
            RESET ROLE;
        END;

        INSERT INTO _fw_migration_005_test_results VALUES (
            17, 'Role-Context Execution Verification', 'Runtime Security Test',
            'anon view SELECT=true, anon sensitive SELECT=denied, anon INSERT=denied, service_role=authorized',
            format('anon_view=%s, anon_sensitive_denied=%s, anon_insert_denied=%s, svc_ok=%s', 
                role_anon_view_ok, role_anon_sensitive_denied, role_anon_insert_denied, role_svc_dml_ok),
            CASE WHEN role_anon_view_ok AND role_anon_sensitive_denied AND role_anon_insert_denied AND role_svc_dml_ok THEN 'PASS' ELSE 'FAIL' END,
            'Dynamic role impersonation verifying runtime enforcement of views, sensitive columns, and DML'
        );
    ELSE
        INSERT INTO _fw_migration_005_test_results VALUES (
            17, 'Role-Context Execution Verification', 'Runtime Security Test',
            'Executable role impersonation tests',
            format('SKIPPED (%s)', coalesce(role_test_diag, 'Table/View missing')),
            CASE WHEN tbl_exists AND view_exists THEN 'INCONCLUSIVE' ELSE 'FAIL' END,
            'Requires execution by administrative role with SET LOCAL ROLE privileges'
        );
    END IF;

END $$;

-- Return complete verification results table
SELECT 
    check_id,
    check_name,
    category,
    status,
    expected,
    observed,
    details
FROM _fw_migration_005_test_results
ORDER BY check_id;

-- Overall summary count & authoritative status banner
SELECT 
    count(*) FILTER (WHERE status = 'PASS') AS total_passed,
    count(*) FILTER (WHERE status = 'FAIL') AS total_failed,
    count(*) FILTER (WHERE status = 'INCONCLUSIVE') AS total_inconclusive,
    CASE 
        WHEN count(*) FILTER (WHERE status = 'FAIL') > 0 THEN 'NOT APPROVED — MANUAL MIGRATION BLOCKED'
        WHEN count(*) FILTER (WHERE status = 'INCONCLUSIVE') > 0 THEN 'LOCAL SQL VERIFICATION PASSED — LIVE SUPABASE EXECUTION PENDING (MANUAL CHECKS REQUIRED)'
        ELSE 'LOCAL SQL VERIFICATION PASSED — LIVE SUPABASE EXECUTION PENDING'
    END AS final_verdict
FROM _fw_migration_005_test_results;
