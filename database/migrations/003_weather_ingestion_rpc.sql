-- FloodWatch Phase 2.3.2.1: Hardened Weather Data Ingestion RPC Schema
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- PURPOSE:
-- Supabase PostgREST does not support specifying partial index WHERE clauses
-- in the ?on_conflict query parameter.
-- This migration provides an atomic, transactional PostgreSQL RPC function
-- that correctly targets partial unique indexes:
--   1. uq_observations_analysis
--   2. uq_observations_forecast_active
--   3. uq_observations_forecast_cycle
--
-- It automatically triggers the hardened forecast revision trigger
-- (trg_observations_forecast_revision) on forecast updates and returns
-- exact records_inserted and records_updated counts.
--
-- SECURITY & PERMISSIONS:
-- - Fixed search path: public, extensions, pg_temp
-- - Explicit SECURITY INVOKER
-- - Execution revoked from PUBLIC, anon, authenticated
-- - Execution granted exclusively to service_role
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this migration against Supabase without explicit approval.
-- This file is prepared locally for review and execution.

BEGIN;

SET LOCAL search_path = public, extensions;

-- ---------------------------------------------------------------------
-- 1. Ingestion Upsert RPC Function with Hardened Defensive Validation
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.upsert_weather_observations(
    p_observations JSONB,
    p_ingestion_run_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    v_item JSONB;
    v_location_id UUID;
    v_data_source_id UUID;
    v_observation_type VARCHAR(30);
    v_measurement_type VARCHAR(30);
    v_valid_at TIMESTAMPTZ;
    v_fetched_at TIMESTAMPTZ;
    v_forecast_ref_time TIMESTAMPTZ;
    v_value NUMERIC(12,4);
    v_unit VARCHAR(20);
    v_quality_flag VARCHAR(50);
    v_metadata JSONB;
    
    v_inserted_count INTEGER := 0;
    v_updated_count INTEGER := 0;
    v_total_fetched INTEGER := 0;
    v_is_insert BOOLEAN;
    v_uuid_pattern CONSTANT TEXT := '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
BEGIN
    -- 1. Top-level parameter validation
    IF p_ingestion_run_id IS NULL THEN
        RAISE EXCEPTION 'Payload error: p_ingestion_run_id is required and cannot be null';
    END IF;

    IF p_observations IS NULL THEN
        RAISE EXCEPTION 'Payload error: observations payload cannot be null';
    END IF;

    IF jsonb_typeof(p_observations) <> 'array' THEN
        RAISE EXCEPTION 'Payload error: observations payload must be a JSON array (got %)', jsonb_typeof(p_observations);
    END IF;

    IF jsonb_array_length(p_observations) = 0 THEN
        RAISE EXCEPTION 'Payload error: observations array cannot be empty';
    END IF;

    IF jsonb_array_length(p_observations) > 5000 THEN
        RAISE EXCEPTION 'Payload error: payload exceeds maximum limit of 5000 records (got %)', jsonb_array_length(p_observations);
    END IF;

    v_total_fetched := jsonb_array_length(p_observations);

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_observations)
    LOOP
        -- 2. Item-level structure validation
        IF jsonb_typeof(v_item) <> 'object' THEN
            RAISE EXCEPTION 'Payload error: observation record must be a JSON object (got %)', jsonb_typeof(v_item);
        END IF;

        -- 3. location_id validation (existence, nullity, UUID format)
        IF NOT (v_item ? 'location_id') OR (v_item->>'location_id') IS NULL OR trim(v_item->>'location_id') = '' THEN
            RAISE EXCEPTION 'Payload error: location_id is required and cannot be null or empty';
        END IF;

        IF NOT (v_item->>'location_id' ~* v_uuid_pattern) THEN
            RAISE EXCEPTION 'Payload error: location_id must be a valid UUID format (got %)', v_item->>'location_id';
        END IF;
        v_location_id := (v_item->>'location_id')::UUID;

        -- 4. data_source_id validation (existence, nullity, UUID format)
        IF NOT (v_item ? 'data_source_id') OR (v_item->>'data_source_id') IS NULL OR trim(v_item->>'data_source_id') = '' THEN
            RAISE EXCEPTION 'Payload error: data_source_id is required and cannot be null or empty';
        END IF;

        IF NOT (v_item->>'data_source_id' ~* v_uuid_pattern) THEN
            RAISE EXCEPTION 'Payload error: data_source_id must be a valid UUID format (got %)', v_item->>'data_source_id';
        END IF;
        v_data_source_id := (v_item->>'data_source_id')::UUID;

        -- 5. observation_type taxonomy validation
        IF NOT (v_item ? 'observation_type') OR (v_item->>'observation_type') IS NULL OR trim(v_item->>'observation_type') = '' THEN
            RAISE EXCEPTION 'Payload error: observation_type is required and cannot be null';
        END IF;

        v_observation_type := trim(v_item->>'observation_type');
        IF v_observation_type NOT IN ('analysis', 'forecast') THEN
            RAISE EXCEPTION 'Payload error: unauthorized observation_type "%". Ingestion RPC only accepts "analysis" or "forecast".', v_observation_type;
        END IF;

        -- 6. measurement_type taxonomy validation
        IF NOT (v_item ? 'measurement_type') OR (v_item->>'measurement_type') IS NULL OR trim(v_item->>'measurement_type') = '' THEN
            RAISE EXCEPTION 'Payload error: measurement_type is required and cannot be null';
        END IF;

        v_measurement_type := trim(v_item->>'measurement_type');
        IF v_measurement_type <> 'rainfall' THEN
            RAISE EXCEPTION 'Payload error: unsupported measurement_type "%". Only "rainfall" is accepted during Phase 2.3.', v_measurement_type;
        END IF;

        -- 7. valid_at timestamp validation
        IF NOT (v_item ? 'valid_at') OR (v_item->>'valid_at') IS NULL OR trim(v_item->>'valid_at') = '' THEN
            RAISE EXCEPTION 'Payload error: valid_at timestamp is required and cannot be null';
        END IF;

        BEGIN
            v_valid_at := (v_item->>'valid_at')::TIMESTAMPTZ;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'Payload error: valid_at must be a valid ISO-8601 timestamp (got %)', v_item->>'valid_at';
        END;

        -- 8. fetched_at timestamp validation
        IF v_item ? 'fetched_at' AND (v_item->>'fetched_at') IS NOT NULL AND trim(v_item->>'fetched_at') <> '' THEN
            BEGIN
                v_fetched_at := (v_item->>'fetched_at')::TIMESTAMPTZ;
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'Payload error: fetched_at must be a valid ISO-8601 timestamp (got %)', v_item->>'fetched_at';
            END;
        ELSE
            v_fetched_at := clock_timestamp();
        END IF;

        -- 9. value validation (existence, non-null, finite numeric, non-negative)
        IF NOT (v_item ? 'value') OR (v_item->>'value') IS NULL OR trim(v_item->>'value') = '' THEN
            RAISE EXCEPTION 'Payload error: value is required and cannot be null';
        END IF;

        IF (v_item->>'value') ~* '(nan|inf|infinity)' THEN
            RAISE EXCEPTION 'Payload error: value cannot be NaN or non-finite';
        END IF;

        BEGIN
            v_value := (v_item->>'value')::NUMERIC(12,4);
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'Payload error: value must be a valid numeric decimal (got %)', v_item->>'value';
        END;

        IF v_value < 0 THEN
            RAISE EXCEPTION 'Payload error: rainfall value cannot be negative (got %)', v_value;
        END IF;

        -- 10. unit validation
        IF v_item ? 'unit' AND (v_item->>'unit') IS NOT NULL AND trim(v_item->>'unit') <> '' THEN
            v_unit := trim(v_item->>'unit');
            IF v_unit <> 'mm' THEN
                RAISE EXCEPTION 'Payload error: unsupported unit "%" for rainfall. Canonical unit is "mm".', v_unit;
            END IF;
        ELSE
            v_unit := 'mm';
        END IF;

        -- 11. forecast_reference_time validation
        IF v_item ? 'forecast_reference_time' AND (v_item->>'forecast_reference_time') IS NOT NULL AND trim(v_item->>'forecast_reference_time') <> '' THEN
            BEGIN
                v_forecast_ref_time := (v_item->>'forecast_reference_time')::TIMESTAMPTZ;
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'Payload error: forecast_reference_time must be a valid ISO-8601 timestamp (got %)', v_item->>'forecast_reference_time';
            END;
        ELSE
            v_forecast_ref_time := NULL;
        END IF;

        -- 12. metadata validation
        IF v_item ? 'metadata' AND (v_item->'metadata') IS NOT NULL THEN
            IF jsonb_typeof(v_item->'metadata') <> 'object' THEN
                RAISE EXCEPTION 'Payload error: metadata must be a JSON object (got %)', jsonb_typeof(v_item->'metadata');
            END IF;
            v_metadata := v_item->'metadata';
        ELSE
            v_metadata := '{}'::jsonb;
        END IF;

        v_quality_flag := COALESCE(v_item->>'quality_flag', 'unverified');

        -- -------------------------------------------------------------
        -- Persistence targeting partial unique indexes
        -- -------------------------------------------------------------
        IF v_observation_type = 'analysis' THEN
            -- Target partial unique index uq_observations_analysis
            INSERT INTO public.observations (
                location_id,
                data_source_id,
                ingestion_run_id,
                valid_at,
                fetched_at,
                forecast_reference_time,
                observation_type,
                measurement_type,
                value,
                unit,
                quality_flag,
                metadata
            ) VALUES (
                v_location_id,
                v_data_source_id,
                p_ingestion_run_id,
                v_valid_at,
                v_fetched_at,
                NULL,
                'analysis',
                v_measurement_type,
                v_value,
                v_unit,
                v_quality_flag,
                v_metadata
            )
            ON CONFLICT (location_id, data_source_id, measurement_type, valid_at)
            WHERE observation_type = 'analysis'
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                fetched_at = EXCLUDED.fetched_at,
                ingestion_run_id = EXCLUDED.ingestion_run_id,
                metadata = EXCLUDED.metadata
            RETURNING (xmax = 0) INTO v_is_insert;

            IF v_is_insert THEN
                v_inserted_count := v_inserted_count + 1;
            ELSE
                v_updated_count := v_updated_count + 1;
            END IF;

        ELSIF v_observation_type = 'forecast' AND v_forecast_ref_time IS NULL THEN
            -- Target partial unique index uq_observations_forecast_active
            -- Fires trg_observations_forecast_revision automatically on value/unit update
            INSERT INTO public.observations (
                location_id,
                data_source_id,
                ingestion_run_id,
                valid_at,
                fetched_at,
                forecast_reference_time,
                observation_type,
                measurement_type,
                value,
                unit,
                quality_flag,
                metadata
            ) VALUES (
                v_location_id,
                v_data_source_id,
                p_ingestion_run_id,
                v_valid_at,
                v_fetched_at,
                NULL,
                'forecast',
                v_measurement_type,
                v_value,
                v_unit,
                v_quality_flag,
                v_metadata
            )
            ON CONFLICT (location_id, data_source_id, measurement_type, valid_at)
            WHERE observation_type = 'forecast' AND forecast_reference_time IS NULL
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                fetched_at = EXCLUDED.fetched_at,
                ingestion_run_id = EXCLUDED.ingestion_run_id,
                metadata = EXCLUDED.metadata
            RETURNING (xmax = 0) INTO v_is_insert;

            IF v_is_insert THEN
                v_inserted_count := v_inserted_count + 1;
            ELSE
                v_updated_count := v_updated_count + 1;
            END IF;

        ELSIF v_observation_type = 'forecast' AND v_forecast_ref_time IS NOT NULL THEN
            -- Target cycle index uq_observations_forecast_cycle
            INSERT INTO public.observations (
                location_id,
                data_source_id,
                ingestion_run_id,
                valid_at,
                fetched_at,
                forecast_reference_time,
                observation_type,
                measurement_type,
                value,
                unit,
                quality_flag,
                metadata
            ) VALUES (
                v_location_id,
                v_data_source_id,
                p_ingestion_run_id,
                v_valid_at,
                v_fetched_at,
                v_forecast_ref_time,
                'forecast',
                v_measurement_type,
                v_value,
                v_unit,
                v_quality_flag,
                v_metadata
            )
            ON CONFLICT (location_id, data_source_id, measurement_type, forecast_reference_time, valid_at)
            WHERE observation_type = 'forecast' AND forecast_reference_time IS NOT NULL
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                fetched_at = EXCLUDED.fetched_at,
                ingestion_run_id = EXCLUDED.ingestion_run_id,
                metadata = EXCLUDED.metadata
            RETURNING (xmax = 0) INTO v_is_insert;

            IF v_is_insert THEN
                v_inserted_count := v_inserted_count + 1;
            ELSE
                v_updated_count := v_updated_count + 1;
            END IF;
        END IF;
    END LOOP;

    RETURN jsonb_build_object(
        'records_fetched', v_total_fetched,
        'records_inserted', v_inserted_count,
        'records_updated', v_updated_count
    );
END;
$$;

-- ---------------------------------------------------------------------
-- 2. Explicit Function Access Control & Permissions Hardening
-- ---------------------------------------------------------------------
-- By default, PostgreSQL grants EXECUTE to PUBLIC on new functions in schema public.
-- To prevent unauthorized invocation through PostgREST by anonymous or standard
-- authenticated users, revoke public execution and grant exclusively to service_role.
REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM PUBLIC;

DO $$
BEGIN
    -- Revoke from Supabase client roles if they exist
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM anon;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) FROM authenticated;
    END IF;

    -- Grant execute permissions strictly to service_role for backend operations
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.upsert_weather_observations(JSONB, UUID) TO service_role;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.observations TO service_role;
        GRANT SELECT, INSERT ON TABLE public.forecast_observation_revisions TO service_role;
    END IF;
END $$;

COMMIT;
