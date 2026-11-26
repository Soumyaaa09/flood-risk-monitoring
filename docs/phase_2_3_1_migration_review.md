# FloodWatch — Phase 2.3.1: Migration 002 Technical Review (Final Safety Edition)

**Document Version:** 1.2.0 (Phase 2.3.1 Final Safety Edition)  
**Target Migration:** [`database/migrations/002_ingestion_extensions.sql`](file:///c:/Users/rsoum/Desktop/FloodWatch/database/migrations/002_ingestion_extensions.sql)  
**Target Seed:** [`database/seed/002_seed_pilot_weather_locations.sql`](file:///c:/Users/rsoum/Desktop/FloodWatch/database/seed/002_seed_pilot_weather_locations.sql)  
**Safety Protocol:** Prepared locally for human architectural review. **DO NOT APPLY OR EXECUTE ON LIVE SUPABASE WITHOUT EXPLICIT APPROVAL.**

---

## 1. Executive Summary & Purpose

Migration 001 established the foundational physical schema for FloodWatch. Phase 2.3 audits identified six critical requirements before live weather ingestion can begin:
1. **District-Scoped Location Uniqueness:** Prevent duplicate locations and enable idempotent seeding.
2. **Scientific Taxonomy Expansion:** Distinguish physical station telemetry (`observed`) from model-derived estimates (`analysis`), future predictions (`forecast`), historical gridded reanalysis (`reanalysis`), and scenarios (`simulation`).
3. **Ingestion Runs Auditability:** Add an `idempotency_key` and non-negative audit counters to track execution without duplicate runs.
4. **Active Forecast Deduplication:** Implement a partial unique index for active forecasts where Open-Meteo omits a model run timestamp (`forecast_reference_time IS NULL`), protected by preflight duplicate verification.
5. **Non-Destructive Forecast Revision History:** Create `forecast_observation_revisions` and a hardened database trigger to automatically preserve previous forecast predictions upon update. Retention is protected via `ON DELETE SET NULL`, ensuring active forecast pruning never purges audit history.
6. **Authoritative Elevation Integrity:** Remove unverified hardcoded elevation values, setting them explicitly to `NULL`.
7. **Migration Atomicity:** All DDL and DML operations are wrapped in explicit transactions (`BEGIN; ... COMMIT;`) using `SET LOCAL search_path = public, extensions;`.

---

## 2. Detailed Schema Changes

### Change 1: District-Scoped Location Uniqueness with Scoped Constraint Check
- **SQL Action:**
  ```sql
  ALTER TABLE public.monitoring_locations 
      ADD CONSTRAINT uq_monitoring_locations_district_name UNIQUE (district_id, name);
  ```
- **Preflight Check:** Runs a defensive query over `public.monitoring_locations`. If duplicate `(district_id, name)` entries exist, the migration immediately aborts with an exception detailing the number of duplicates.
- **Reliable Rerun Scoping:** Constraint existence checks use `conrelid = 'public.monitoring_locations'::regclass AND conname = 'uq_monitoring_locations_district_name'`, avoiding false matches against identically named constraints in other schemas.

### Change 2: Scientific Observation Taxonomy Expansion
- **SQL Action:**
  Replaces legacy 3-way constraint with:
  ```sql
  ALTER TABLE public.observations ADD CONSTRAINT chk_observation_type CHECK (
      observation_type IN ('observed', 'analysis', 'forecast', 'reanalysis', 'simulation')
  );
  ```
- **Rationale:** 
  - `observed`: Physical in-situ station observations (e.g., future IMD AWS or CWC gauges).
  - `analysis`: Near-real-time model nowcasts / grid interpolations (e.g., Open-Meteo `current` 15-minute intervals).
  - `forecast`: Forward-looking numerical weather/flood predictions.
  - `reanalysis`: Historical gridded climate reanalysis (e.g., ERA5 archive).
  - `simulation`: Hydrodynamic scenario runs (e.g., dam break, flood routing).
- **Integrity Rule:** Guarantees Open-Meteo numerical model interpolations are **never** falsely recorded as physical sensor telemetry.

### Change 3: Ingestion Runs Audit Tracking, Idempotency & Non-Negative Checks
- **SQL Action:**
  Adds:
  - `idempotency_key VARCHAR(128)` with a scoped unique constraint `uq_ingestion_runs_idempotency_key` (with duplicate preflight check and definition compatibility verification).
  - `location_id UUID` with explicit foreign key enforcement `fk_ingestion_runs_location REFERENCES public.monitoring_locations(id) ON DELETE SET NULL` (guaranteed even if the column pre-existed).
  - `records_fetched INTEGER DEFAULT 0 NOT NULL`.
  - `records_inserted INTEGER DEFAULT 0 NOT NULL`.
  - `records_updated INTEGER DEFAULT 0 NOT NULL`.
  - `data_freshness_timestamp TIMESTAMPTZ`.
  - Safe backfill:
    ```sql
    UPDATE public.ingestion_runs SET records_fetched = 0 WHERE records_fetched IS NULL;
    UPDATE public.ingestion_runs SET records_inserted = 0 WHERE records_inserted IS NULL;
    UPDATE public.ingestion_runs SET records_updated = 0 WHERE records_updated IS NULL;
    ```
  - Scoped check constraint with definition compatibility verification:
    ```sql
    ALTER TABLE public.ingestion_runs 
        ADD CONSTRAINT chk_ingestion_runs_counters_non_negative CHECK (
            records_fetched >= 0 AND records_inserted >= 0 AND records_updated >= 0
        );
    ```
- **Rationale:** Guarantees worker retries using deterministic idempotency keys while strictly forbidding NULL or negative record counters, safely handling re-runs and pre-existing columns.

### Change 4: Deduplication Preflight Checks & Partial Unique Indexes
- **Preflight Verification:**
  Before building indexes, the migration queries `public.observations` for duplicate groups on:
  - `(location_id, data_source_id, measurement_type, valid_at)` for `observation_type = 'forecast' AND forecast_reference_time IS NULL`.
  - `(location_id, data_source_id, measurement_type, valid_at)` for `observation_type = 'analysis'`.
  If duplicates are found, it aborts with an informative message providing the exact SQL inspection query to investigate.
- **Indexes Created:**
  - `uq_observations_forecast_active`: Enforces one active forecast horizon point per station, metric, and valid time when model run timestamp is null.
  - `uq_observations_analysis`: Enforces one analysis nowcast per station, metric, and valid time.

### Change 5: Protected Forecast Revision History (`forecast_observation_revisions`)
- **Retention Design:**
  - `observation_id UUID REFERENCES public.observations(id) ON DELETE SET NULL`: If the active forecast row is deleted or pruned, the revision record is **NOT** destroyed.
  - `location_id UUID REFERENCES public.monitoring_locations(id) ON DELETE SET NULL`: If a location is removed, historical forecast revisions survive.
  - `data_source_id UUID REFERENCES public.data_sources(id) ON DELETE RESTRICT`: Source records cannot be removed while revisions reference them.
  - `ingestion_run_id UUID REFERENCES public.ingestion_runs(id) ON DELETE SET NULL`: Ingestion run cleanup does not purge revision records.
- **Integrity Check:** Enforces `chk_revision_measurement_physics` matching the observations table.
- **Row Level Security:** RLS is enabled on `public.forecast_observation_revisions`.

### Change 6: Hardened Revision Archival Trigger
- **Function:** `public.trg_archive_forecast_revision()`
- **Security & Scope Hardening:**
  - Uses `SECURITY INVOKER` (default, no unnecessary elevation).
  - Explicitly sets `search_path = public, extensions, pg_temp` to prevent search_path injection attacks.
  - Fully schema-qualifies `public.forecast_observation_revisions`.
  - Evaluates `OLD.value IS DISTINCT FROM NEW.value OR OLD.unit IS DISTINCT FROM NEW.unit`:
    * Unchanged forecast upserts (e.g. repeated scheduled runs confirming the same prediction) **do not fire insertions**.
    * Only genuine revisions to forecast values or units trigger historical preservation.

---

## 3. Seed Script Design ([`002_seed_pilot_weather_locations.sql`](file:///c:/Users/rsoum/Desktop/FloodWatch/database/seed/002_seed_pilot_weather_locations.sql))

Registers the three initial pilot centroids as **virtual weather grid locations**:
1. **Paradeep Weather Grid Point:** Jagatsinghpur district (`20.3160°N, 86.6110°E`).
2. **Cuttack Weather Grid Point:** Cuttack district (`20.4625°N, 85.8830°E`).
3. **Kendrapara Weather Grid Point:** Kendrapara district (`20.5017°N, 86.4223°E`).

### Authoritative Elevation Correction & Reference Integrity
- Previous hardcoded values (`5.0m`, `30.0m`, `13.0m`) were removed because they were not verified against an authoritative digital elevation model (DEM) or survey.
- In `001_initial_schema.sql`, `elevation_m NUMERIC(7,2)` is nullable.
- In `002_seed_pilot_weather_locations.sql`, `elevation_m` is explicitly seeded and updated as **`NULL`**, with metadata documenting `"elevation_status": "unverified"`.
- Explicit verification: Raises an exception if the Mahanadi basin or any of the 3 pilot districts are missing from the database.
- Idempotent upsert preserves `basin_id`, `location_type`, coordinates, and NULL elevation upon rerun.

---

## 4. Destructive Rollback — Requires Explicit Approval

> **CRITICAL WARNING — DESTRUCTIVE ROLLBACK PROTOCOL:**  
> 1. Dropping `public.forecast_observation_revisions` **permanently destroys all historical forecast revision audit records**.  
> 2. Dropping `ingestion_runs` columns **permanently deletes all recorded audit metrics** (`records_fetched`, `records_inserted`, `records_updated`, `data_freshness_timestamp`, `idempotency_key`, `location_id`).  
> 3. Restoring the legacy 3-way `observation_type` check constraint will **fail immediately** if any records with `observation_type IN ('analysis', 'reanalysis')` exist in `public.observations`.  
> **MANDATORY PREREQUISITE:** A complete database backup/export must be performed before executing this rollback.

```sql
-- Destructive Rollback for Migration 002 (Requires Explicit Approval)
BEGIN;

SET LOCAL search_path = public, extensions;

-- 1. Preflight check: abort rollback if observations contain 'analysis' or 'reanalysis'
DO $$
DECLARE
    v_incompatible_obs_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_incompatible_obs_count
    FROM public.observations
    WHERE observation_type IN ('analysis', 'reanalysis');

    IF v_incompatible_obs_count > 0 THEN
        RAISE EXCEPTION 'Rollback aborted: found % observations with observation_type IN (''analysis'', ''reanalysis''). Restoring the original 3-way check constraint would fail and invalidate data. Backup and archive these records before proceeding.', v_incompatible_obs_count;
    END IF;
END $$;

-- 2. Drop trigger and archival function
DROP TRIGGER IF EXISTS trg_observations_forecast_revision ON public.observations;
DROP FUNCTION IF EXISTS public.trg_archive_forecast_revision();

-- 3. PERMANENT DATA LOSS: Drop forecast revisions table
DROP TABLE IF EXISTS public.forecast_observation_revisions;

-- 4. Drop partial unique and audit indexes
DROP INDEX IF EXISTS public.uq_observations_analysis;
DROP INDEX IF EXISTS public.uq_observations_forecast_active;
DROP INDEX IF EXISTS public.idx_ingestion_runs_idempotency;
DROP INDEX IF EXISTS public.idx_ingestion_runs_location_status;

-- 5. PERMANENT DATA LOSS: Remove audit columns and constraints from ingestion_runs
ALTER TABLE public.ingestion_runs 
    DROP CONSTRAINT IF EXISTS fk_ingestion_runs_location,
    DROP CONSTRAINT IF EXISTS chk_ingestion_runs_counters_non_negative,
    DROP CONSTRAINT IF EXISTS uq_ingestion_runs_idempotency_key,
    DROP COLUMN IF EXISTS data_freshness_timestamp,
    DROP COLUMN IF EXISTS records_updated,
    DROP COLUMN IF EXISTS records_inserted,
    DROP COLUMN IF EXISTS records_fetched,
    DROP COLUMN IF EXISTS location_id,
    DROP COLUMN IF EXISTS idempotency_key;

-- 6. Restore legacy observation_type check constraint
ALTER TABLE public.observations DROP CONSTRAINT IF EXISTS chk_observation_type;
ALTER TABLE public.observations ADD CONSTRAINT chk_observation_type CHECK (
    observation_type IN ('observed', 'forecast', 'simulation')
);

-- 7. Drop location uniqueness constraint
ALTER TABLE public.monitoring_locations DROP CONSTRAINT IF EXISTS uq_monitoring_locations_district_name;

COMMIT;
```

---

## 5. Security, Permissions & Supabase Service-Role Path

### Role Separation & Table Grants
- **Service Role Privileges:** Backend ingestion operates via Supabase's `service_role` key. In PostgreSQL, `service_role` has the `BYPASSRLS` attribute. However, `BYPASSRLS` only bypasses Row-Level Security policies; it does **not** grant table-level DML privileges (`SELECT`, `INSERT`, `UPDATE`, `DELETE`).
- **Explicit Grants in Section 7:** Migration 002 includes defensive, conditional grants:
  ```sql
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
  ```
- **Trigger Privileges (`SECURITY INVOKER`):** `public.trg_archive_forecast_revision()` is declared `SECURITY INVOKER` with fixed `search_path = public, extensions, pg_temp`. When the backend service-role executes an `UPDATE` on `observations`, the trigger runs under `service_role` privileges and successfully writes to `public.forecast_observation_revisions`.
- **Public & Anonymous Protection:** No `INSERT`, `UPDATE`, or `DELETE` grants are given to `anon` or `authenticated` roles. Anonymous users cannot tamper with operational logs or forecast revisions.
- **Required Manual Verification in Supabase Dashboard:**
  - Verify that the Supabase project default role `service_role` exists and possesses default table privileges in schema `public`.
  - Verify that PostGIS functions resolve from schema `extensions`.

---

## 6. Execution Order & Protocol

1. **Step 1 (Already Applied):** `database/migrations/001_initial_schema.sql` (Creates 6 core tables and PostGIS foundation).
2. **Step 2 (Already Applied):** `database/seed/001_seed_initial_data.sql` (Seeds Mahanadi basin, 3 pilot districts, 3 data sources).
3. **Step 3 (Pending Manual Review & Execution):** `database/migrations/002_ingestion_extensions.sql` (Applies audit counters, taxonomy expansion, deduplication indexes, and revision tracking).
4. **Step 4 (Pending Manual Review & Execution):** `database/seed/002_seed_pilot_weather_locations.sql` (Seeds virtual weather points for Paradeep, Cuttack, and Kendrapara).

---

## 7. Limitations & Operational Assumptions

1. **Physical Metrics Constraint:** Migration 002 maintains measurement types strictly to `rainfall`, `water_level`, and `discharge`. Atmospheric ancillary variables (temperature, wind, humidity) are preserved inside `metadata` JSONB.
2. **Redundant Index Elimination:** PostgreSQL unique constraints automatically create supporting unique B-Tree indexes. Therefore, redundant secondary indexes on `idempotency_key` are avoided to optimize database write throughput.
3. **Static Local Verification Notice:** Static parsing and test validation confirm SQL syntax, transactional atomicity, and schema compatibility locally. Real execution on Supabase must still undergo manual review in the Supabase SQL editor.
