-- FloodWatch Phase 2.3.1: Ingestion Extensions Schema
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this migration or apply it to live environments without explicit approval.
-- This file defines transactional schema extensions for real weather ingestion:
-- 1. District-scoped location uniqueness (scoped pg_constraint checks with regclass)
-- 2. Scientific observation taxonomy expansion ('observed', 'analysis', 'forecast', 'reanalysis', 'simulation')
-- 3. Ingestion runs operational audit columns, non-negative checks, and idempotency key
-- 4. Deduplication preflight checks and partial unique indexes for active forecasts and nowcasts
-- 5. Hardened forecast revision history table with non-destructive SET NULL retention
-- 6. Hardened, schema-qualified trigger function with search_path protection

BEGIN;

-- Apply local search path strictly for the duration of this transaction
SET LOCAL search_path = public, extensions;

-- ---------------------------------------------------------------------
-- 1. Monitoring Locations: District-Scoped Name Uniqueness
-- ---------------------------------------------------------------------
-- Preflight: Defensively verify that no duplicate (district_id, name) tuples exist
DO $$
DECLARE
    v_dup_locations_count INTEGER;
    v_cdef TEXT;
    v_has_constraint BOOLEAN := FALSE;
BEGIN
    SELECT COUNT(*) INTO v_dup_locations_count
    FROM (
        SELECT district_id, name 
        FROM public.monitoring_locations 
        GROUP BY district_id, name 
        HAVING COUNT(*) > 1
    ) sub;

    IF v_dup_locations_count > 0 THEN
        RAISE EXCEPTION 'Migration 002 aborted: found % duplicate (district_id, name) groups in public.monitoring_locations. Resolve duplicates before applying uq_monitoring_locations_district_name.', v_dup_locations_count;
    END IF;

    -- Check if uq_monitoring_locations_district_name exists
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.monitoring_locations'::regclass 
          AND conname = 'uq_monitoring_locations_district_name'
    ) THEN
        SELECT pg_get_constraintdef(oid) INTO v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.monitoring_locations'::regclass 
          AND conname = 'uq_monitoring_locations_district_name';

        IF v_cdef NOT LIKE '%(district_id, name)%' THEN
            RAISE EXCEPTION 'Constraint uq_monitoring_locations_district_name exists but has incompatible definition: %', v_cdef;
        END IF;
        v_has_constraint := TRUE;
    END IF;

    -- Check if another unique constraint already covers (district_id, name)
    IF NOT v_has_constraint THEN
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.monitoring_locations'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) LIKE '%(district_id, name)%'
        ) THEN
            v_has_constraint := TRUE;
        END IF;
    END IF;

    IF NOT v_has_constraint THEN
        ALTER TABLE public.monitoring_locations 
            ADD CONSTRAINT uq_monitoring_locations_district_name UNIQUE (district_id, name);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2. Observations: Observation Type Scientific Taxonomy Expansion
-- ---------------------------------------------------------------------
-- Replaces rigid check constraint with formal 5-way scientific taxonomy:
--   'observed': In-situ physical ground instrumentation (e.g. CWC, IMD)
--   'analysis': Model-derived nowcasts / real-time grid interpolations (e.g. Open-Meteo current)
--   'forecast': Forward-looking predictive numerical weather/flood outputs
--   'reanalysis': Historical gridded climate reanalysis (e.g. ERA5)
--   'simulation': Scenario-based hydrodynamic / flood routing runs
DO $$
DECLARE
    v_invalid_obs_count INTEGER;
    v_cdef TEXT;
    v_needs_update BOOLEAN := FALSE;
BEGIN
    -- Preflight: verify no existing records violate the 5-way taxonomy before constraint change
    SELECT COUNT(*) INTO v_invalid_obs_count
    FROM public.observations
    WHERE observation_type NOT IN ('observed', 'analysis', 'forecast', 'reanalysis', 'simulation');

    IF v_invalid_obs_count > 0 THEN
        RAISE EXCEPTION 'Migration 002 aborted: found % records in public.observations with unrecognized observation_type values.', v_invalid_obs_count;
    END IF;

    -- Drop legacy constraint names if present on public.observations
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.observations'::regclass 
          AND conname = 'observations_observation_type_check'
    ) THEN
        ALTER TABLE public.observations DROP CONSTRAINT observations_observation_type_check;
        v_needs_update := TRUE;
    END IF;

    -- Check if chk_observation_type exists and inspect its definition
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.observations'::regclass 
          AND conname = 'chk_observation_type'
    ) THEN
        SELECT pg_get_constraintdef(oid) INTO v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.observations'::regclass 
          AND conname = 'chk_observation_type';

        IF v_cdef LIKE '%''observed''%' 
           AND v_cdef LIKE '%''analysis''%' 
           AND v_cdef LIKE '%''forecast''%' 
           AND v_cdef LIKE '%''reanalysis''%' 
           AND v_cdef LIKE '%''simulation''%' THEN
            v_needs_update := FALSE;
        ELSE
            ALTER TABLE public.observations DROP CONSTRAINT chk_observation_type;
            v_needs_update := TRUE;
        END IF;
    ELSE
        v_needs_update := TRUE;
    END IF;

    -- Apply scoped check constraint if missing or outdated
    IF v_needs_update THEN
        ALTER TABLE public.observations ADD CONSTRAINT chk_observation_type CHECK (
            observation_type IN ('observed', 'analysis', 'forecast', 'reanalysis', 'simulation')
        );
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 3. Ingestion Runs: Audit Tracking, Non-Negative Checks & Idempotency Key
-- ---------------------------------------------------------------------
ALTER TABLE public.ingestion_runs 
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128),
    ADD COLUMN IF NOT EXISTS location_id UUID,
    ADD COLUMN IF NOT EXISTS records_fetched INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS records_inserted INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS records_updated INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS data_freshness_timestamp TIMESTAMPTZ;

-- Foreign key catalog inspection and enforcement for location_id
DO $$
DECLARE
    v_cdef TEXT;
    v_confrelid OID;
    v_confdeltype CHAR;
    v_has_fk BOOLEAN := FALSE;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'fk_ingestion_runs_location'
    ) THEN
        SELECT confrelid, confdeltype, pg_get_constraintdef(oid) 
        INTO v_confrelid, v_confdeltype, v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'fk_ingestion_runs_location';

        IF v_confrelid <> 'public.monitoring_locations'::regclass OR v_confdeltype <> 'n' THEN
            RAISE EXCEPTION 'Constraint fk_ingestion_runs_location exists but has incompatible definition: %', v_cdef;
        END IF;
        v_has_fk := TRUE;
    ELSIF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'ingestion_runs_location_id_fkey'
    ) THEN
        SELECT confrelid, confdeltype, pg_get_constraintdef(oid) 
        INTO v_confrelid, v_confdeltype, v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'ingestion_runs_location_id_fkey';

        IF v_confrelid <> 'public.monitoring_locations'::regclass OR v_confdeltype <> 'n' THEN
            RAISE EXCEPTION 'Constraint ingestion_runs_location_id_fkey exists but has incompatible definition: %', v_cdef;
        END IF;
        v_has_fk := TRUE;
    END IF;

    IF NOT v_has_fk THEN
        ALTER TABLE public.ingestion_runs 
            ADD CONSTRAINT fk_ingestion_runs_location 
            FOREIGN KEY (location_id) REFERENCES public.monitoring_locations(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Safe backfill: eliminate any NULL values in audit counter columns before enforcing NOT NULL
UPDATE public.ingestion_runs 
SET records_fetched = 0 
WHERE records_fetched IS NULL;

UPDATE public.ingestion_runs 
SET records_inserted = 0 
WHERE records_inserted IS NULL;

UPDATE public.ingestion_runs 
SET records_updated = 0 
WHERE records_updated IS NULL;

-- Enforce DEFAULT 0 and NOT NULL constraints on audit counters (idempotent in PostgreSQL)
ALTER TABLE public.ingestion_runs 
    ALTER COLUMN records_fetched SET DEFAULT 0,
    ALTER COLUMN records_fetched SET NOT NULL,
    ALTER COLUMN records_inserted SET DEFAULT 0,
    ALTER COLUMN records_inserted SET NOT NULL,
    ALTER COLUMN records_updated SET DEFAULT 0,
    ALTER COLUMN records_updated SET NOT NULL;

-- Scoped uniqueness on idempotency_key & non-negative counter checks with definition verification
DO $$
DECLARE
    v_dup_idempotency_count INTEGER;
    v_cdef TEXT;
    v_has_uq BOOLEAN := FALSE;
BEGIN
    -- Preflight check on idempotency keys
    SELECT COUNT(*) INTO v_dup_idempotency_count
    FROM (
        SELECT idempotency_key 
        FROM public.ingestion_runs 
        WHERE idempotency_key IS NOT NULL
        GROUP BY idempotency_key 
        HAVING COUNT(*) > 1
    ) sub;

    IF v_dup_idempotency_count > 0 THEN
        RAISE EXCEPTION 'Migration 002 aborted: found % duplicate idempotency_key groups in public.ingestion_runs.', v_dup_idempotency_count;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'uq_ingestion_runs_idempotency_key'
    ) THEN
        SELECT pg_get_constraintdef(oid) INTO v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'uq_ingestion_runs_idempotency_key';

        IF v_cdef NOT LIKE '%(idempotency_key)%' THEN
            RAISE EXCEPTION 'Constraint uq_ingestion_runs_idempotency_key exists but has incompatible definition: %', v_cdef;
        END IF;
        v_has_uq := TRUE;
    ELSIF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.ingestion_runs'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) LIKE '%(idempotency_key)%'
    ) THEN
        v_has_uq := TRUE;
    END IF;

    IF NOT v_has_uq THEN
        ALTER TABLE public.ingestion_runs 
            ADD CONSTRAINT uq_ingestion_runs_idempotency_key UNIQUE (idempotency_key);
    END IF;

    -- Scoped check constraint for non-negative audit counters
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'chk_ingestion_runs_counters_non_negative'
    ) THEN
        ALTER TABLE public.ingestion_runs 
            ADD CONSTRAINT chk_ingestion_runs_counters_non_negative CHECK (
                records_fetched >= 0 AND 
                records_inserted >= 0 AND 
                records_updated >= 0
            );
    ELSE
        SELECT pg_get_constraintdef(oid) INTO v_cdef
        FROM pg_constraint 
        WHERE conrelid = 'public.ingestion_runs'::regclass 
          AND conname = 'chk_ingestion_runs_counters_non_negative';

        IF v_cdef NOT LIKE '%records_fetched >= 0%' OR v_cdef NOT LIKE '%records_inserted >= 0%' OR v_cdef NOT LIKE '%records_updated >= 0%' THEN
            RAISE EXCEPTION 'Constraint chk_ingestion_runs_counters_non_negative exists but has incompatible definition: %', v_cdef;
        END IF;
    END IF;
END $$;

-- Composite lookup index for operational runs by location and status
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_location_status 
ON public.ingestion_runs(location_id, status);

-- Cleanly drop redundant index if it was previously created (uq_ingestion_runs_idempotency_key already indexes idempotency_key)
DROP INDEX IF EXISTS public.idx_ingestion_runs_idempotency;

-- ---------------------------------------------------------------------
-- 4. Deduplication Preflight Checks & Partial Unique Indexes
-- ---------------------------------------------------------------------
-- Preflight: Abort migration if existing records violate intended partial indexes
DO $$
DECLARE
    v_dup_forecast_count INTEGER;
    v_dup_analysis_count INTEGER;
BEGIN
    -- Check for duplicate active forecasts without model reference time
    SELECT COUNT(*) INTO v_dup_forecast_count
    FROM (
        SELECT location_id, data_source_id, measurement_type, valid_at
        FROM public.observations
        WHERE observation_type = 'forecast' AND forecast_reference_time IS NULL
        GROUP BY location_id, data_source_id, measurement_type, valid_at
        HAVING COUNT(*) > 1
    ) sub;

    IF v_dup_forecast_count > 0 THEN
        RAISE EXCEPTION 'Preflight check failed for index uq_observations_forecast_active: found % duplicate key groups in public.observations where observation_type = ''forecast'' AND forecast_reference_time IS NULL. Query to inspect: SELECT location_id, data_source_id, measurement_type, valid_at, count(*) FROM public.observations WHERE observation_type = ''forecast'' AND forecast_reference_time IS NULL GROUP BY 1,2,3,4 HAVING count(*) > 1;', v_dup_forecast_count;
    END IF;

    -- Check for duplicate analysis records
    SELECT COUNT(*) INTO v_dup_analysis_count
    FROM (
        SELECT location_id, data_source_id, measurement_type, valid_at
        FROM public.observations
        WHERE observation_type = 'analysis'
        GROUP BY location_id, data_source_id, measurement_type, valid_at
        HAVING COUNT(*) > 1
    ) sub;

    IF v_dup_analysis_count > 0 THEN
        RAISE EXCEPTION 'Preflight check failed for index uq_observations_analysis: found % duplicate key groups in public.observations where observation_type = ''analysis''. Query to inspect: SELECT location_id, data_source_id, measurement_type, valid_at, count(*) FROM public.observations WHERE observation_type = ''analysis'' GROUP BY 1,2,3,4 HAVING count(*) > 1;', v_dup_analysis_count;
    END IF;
END $$;

-- Deduplication definition verification if indexes already exist
DO $$
DECLARE
    v_idxdef TEXT;
BEGIN
    SELECT indexdef INTO v_idxdef
    FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_observations_forecast_active';

    IF v_idxdef IS NOT NULL THEN
        IF v_idxdef NOT LIKE '%UNIQUE INDEX%' 
           OR v_idxdef NOT LIKE '%(location_id, data_source_id, measurement_type, valid_at)%' 
           OR v_idxdef NOT LIKE '%observation_type = ''forecast''%'
           OR v_idxdef NOT LIKE '%forecast_reference_time IS NULL%' THEN
            RAISE EXCEPTION 'Index uq_observations_forecast_active exists but has incompatible definition: %', v_idxdef;
        END IF;
    END IF;

    SELECT indexdef INTO v_idxdef
    FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_observations_analysis';

    IF v_idxdef IS NOT NULL THEN
        IF v_idxdef NOT LIKE '%UNIQUE INDEX%' 
           OR v_idxdef NOT LIKE '%(location_id, data_source_id, measurement_type, valid_at)%' 
           OR v_idxdef NOT LIKE '%observation_type = ''analysis''%' THEN
            RAISE EXCEPTION 'Index uq_observations_analysis exists but has incompatible definition: %', v_idxdef;
        END IF;
    END IF;
END $$;

-- A. Active forecast horizon deduplication (for providers omitting model cycle timestamp)
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_forecast_active 
ON public.observations(location_id, data_source_id, measurement_type, valid_at) 
WHERE observation_type = 'forecast' AND forecast_reference_time IS NULL;

-- B. Model analysis / nowcast deduplication
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_analysis 
ON public.observations(location_id, data_source_id, measurement_type, valid_at) 
WHERE observation_type = 'analysis';

-- ---------------------------------------------------------------------
-- 5. Forecast Observation Revisions Table (Protected Non-Destructive Retention)
-- ---------------------------------------------------------------------
-- Preserves prior forecast values when an active forecast is updated.
-- Uses ON DELETE SET NULL on observation_id and location_id so that pruning or deleting
-- active forecast rows or locations NEVER destroys historical forecast audit revisions.
CREATE TABLE IF NOT EXISTS public.forecast_observation_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id UUID REFERENCES public.observations(id) ON DELETE SET NULL,
    location_id UUID REFERENCES public.monitoring_locations(id) ON DELETE SET NULL,
    data_source_id UUID REFERENCES public.data_sources(id) ON DELETE RESTRICT NOT NULL,
    measurement_type VARCHAR(30) NOT NULL,
    value NUMERIC(12,4) NOT NULL,
    unit VARCHAR(20) NOT NULL,
    valid_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    ingestion_run_id UUID REFERENCES public.ingestion_runs(id) ON DELETE SET NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    revised_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,

    -- Physical validity matching observations table
    CONSTRAINT chk_revision_measurement_physics CHECK (
        (measurement_type = 'rainfall' AND unit = 'mm' AND value >= 0) OR
        (measurement_type = 'water_level' AND unit = 'm') OR
        (measurement_type = 'discharge' AND unit = 'm3/s' AND value >= 0)
    )
);

-- Ensure physical measurement constraint is enforced even if table pre-existed
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.forecast_observation_revisions'::regclass
          AND conname = 'chk_revision_measurement_physics'
    ) THEN
        ALTER TABLE public.forecast_observation_revisions
            ADD CONSTRAINT chk_revision_measurement_physics CHECK (
                (measurement_type = 'rainfall' AND unit = 'mm' AND value >= 0) OR
                (measurement_type = 'water_level' AND unit = 'm') OR
                (measurement_type = 'discharge' AND unit = 'm3/s' AND value >= 0)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_forecast_revisions_observation 
ON public.forecast_observation_revisions(observation_id, revised_at DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_revisions_location_valid 
ON public.forecast_observation_revisions(location_id, valid_at, revised_at DESC);

-- Enable Row Level Security on revisions table
ALTER TABLE public.forecast_observation_revisions ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------
-- 6. Hardened Forecast Revision Archival Trigger
-- ---------------------------------------------------------------------
-- Automatically captures prior forecast state upon active forecast value or unit update.
-- Schema-qualified, fixed search_path, explicit SECURITY INVOKER, and strict change filtering.
CREATE OR REPLACE FUNCTION public.trg_archive_forecast_revision()
RETURNS TRIGGER 
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, extensions, pg_temp
AS $$
BEGIN
    -- Only archive if the active forecast record has an actual change in physical value or unit
    IF OLD.observation_type = 'forecast' AND (OLD.value IS DISTINCT FROM NEW.value OR OLD.unit IS DISTINCT FROM NEW.unit) THEN
        INSERT INTO public.forecast_observation_revisions (
            observation_id,
            location_id,
            data_source_id,
            measurement_type,
            value,
            unit,
            valid_at,
            fetched_at,
            ingestion_run_id,
            metadata,
            revised_at
        ) VALUES (
            OLD.id,
            OLD.location_id,
            OLD.data_source_id,
            OLD.measurement_type,
            OLD.value,
            OLD.unit,
            OLD.valid_at,
            OLD.fetched_at,
            OLD.ingestion_run_id,
            OLD.metadata,
            clock_timestamp()
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_observations_forecast_revision ON public.observations;
CREATE TRIGGER trg_observations_forecast_revision
BEFORE UPDATE ON public.observations
FOR EACH ROW
EXECUTE FUNCTION public.trg_archive_forecast_revision();

-- ---------------------------------------------------------------------
-- 7. Supabase Service-Role Explicit Grants (Conditional on Role Existence)
-- ---------------------------------------------------------------------
-- Ensures backend service-role operations possess explicit table privileges
-- even in environments where default schema privileges were modified.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.forecast_observation_revisions TO service_role;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.ingestion_runs TO service_role;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.observations TO service_role;
        GRANT SELECT ON TABLE public.monitoring_locations TO service_role;
        GRANT SELECT ON TABLE public.data_sources TO service_role;
    END IF;
END $$;

COMMIT;
