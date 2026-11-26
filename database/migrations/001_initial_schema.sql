-- FloodWatch Phase 1: Initial Database Schema
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this migration or apply it to live environments without explicit approval.
-- This file defines the foundational schema for flood-risk monitoring in pilot districts of Odisha.

-- Configure search path and enable extensions in designated schema
CREATE SCHEMA IF NOT EXISTS extensions;
SET search_path = public, extensions;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "postgis" WITH SCHEMA extensions;

-- ---------------------------------------------------------------------
-- 1. Basins Table
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS basins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    code VARCHAR(50) UNIQUE,
    description TEXT,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL
);

-- ---------------------------------------------------------------------
-- 2. Districts Table
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS districts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    basin_id UUID REFERENCES basins(id) ON DELETE SET NULL,
    name VARCHAR(100) NOT NULL UNIQUE,
    state VARCHAR(100) DEFAULT 'Odisha' NOT NULL,
    census_code VARCHAR(50),
    centroid GEOMETRY(Point, 4326),
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL
);

-- ---------------------------------------------------------------------
-- 3. Data Sources Table
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    source_type VARCHAR(50) NOT NULL,
    base_url TEXT,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    description TEXT,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL
);

-- ---------------------------------------------------------------------
-- 4. Monitoring Locations Table
-- ---------------------------------------------------------------------
-- NOTE: In Phase 1, no official gauge stations or invented thresholds are registered.
-- Sample coordinates are solely for API connectivity testing and spatial registration.
CREATE TABLE IF NOT EXISTS monitoring_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    district_id UUID REFERENCES districts(id) ON DELETE RESTRICT NOT NULL,
    basin_id UUID REFERENCES basins(id) ON DELETE SET NULL,
    name VARCHAR(200) NOT NULL,
    location_type VARCHAR(50) NOT NULL, -- e.g. 'sample_point', 'virtual_grid', 'river_gauge', 'rainfall_station'
    coordinates GEOMETRY(Point, 4326) NOT NULL,
    latitude NUMERIC(9,6) NOT NULL CHECK (latitude >= -90.0 AND latitude <= 90.0),
    longitude NUMERIC(9,6) NOT NULL CHECK (longitude >= -180.0 AND longitude <= 180.0),
    elevation_m NUMERIC(7,2),
    river_name VARCHAR(100),
    is_official_gauge BOOLEAN DEFAULT FALSE NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL
);

-- ---------------------------------------------------------------------
-- 5. Ingestion Runs Table
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id UUID REFERENCES data_sources(id) ON DELETE RESTRICT NOT NULL,
    status VARCHAR(50) NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'partial')),
    started_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    completed_at TIMESTAMPTZ,
    records_ingested INTEGER DEFAULT 0 CHECK (records_ingested >= 0),
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL
);

-- ---------------------------------------------------------------------
-- 6. Observations Table
-- ---------------------------------------------------------------------
-- Distinction rules:
-- 1. observation_type: 'observed', 'forecast', 'simulation'
-- 2. measurement_type: 'rainfall' (mm), 'water_level' (m), 'discharge' (m3/s)
-- 3. Temporal dimensions:
--    - valid_at: Target timestamp when measurement occurred or when forecast/simulation applies.
--    - fetched_at: Timestamp when FloodWatch retrieved the data from the provider.
--    - created_at: Timestamp when the database row was inserted.
--    - forecast_reference_time: (Optional) Model run / initialization cycle timestamp for forecasts.
-- 4. Gauge water_level has NO positive-only constraint because readings depend on local datum.
-- 5. Discharge (m3/s) is volumetric rate, never to be compared directly with stage/water-level thresholds.
CREATE TABLE IF NOT EXISTS observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID REFERENCES monitoring_locations(id) ON DELETE CASCADE NOT NULL,
    data_source_id UUID REFERENCES data_sources(id) ON DELETE RESTRICT NOT NULL,
    ingestion_run_id UUID REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    
    valid_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,
    forecast_reference_time TIMESTAMPTZ,

    observation_type VARCHAR(30) NOT NULL CHECK (
        observation_type IN ('observed', 'forecast', 'simulation')
    ),
    measurement_type VARCHAR(30) NOT NULL CHECK (
        measurement_type IN ('rainfall', 'water_level', 'discharge')
    ),
    value NUMERIC(12,4) NOT NULL,
    unit VARCHAR(20) NOT NULL,
    quality_flag VARCHAR(50) DEFAULT 'unverified' NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp() NOT NULL,

    -- Ensure unit and value validity based on physical measurement:
    -- - Rainfall (mm): must be non-negative (>= 0)
    -- - Discharge (m3/s): must be non-negative (>= 0)
    -- - Water level (m): can be negative or positive depending on vertical datum reference
    CONSTRAINT chk_measurement_physics CHECK (
        (measurement_type = 'rainfall' AND unit = 'mm' AND value >= 0) OR
        (measurement_type = 'water_level' AND unit = 'm') OR
        (measurement_type = 'discharge' AND unit = 'm3/s' AND value >= 0)
    )
);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_districts_basin_id ON districts(basin_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_locations_district ON monitoring_locations(district_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_locations_coords ON monitoring_locations USING GIST(coordinates);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_status ON ingestion_runs(data_source_id, status);

-- Composite lookup index for filtering by station, measurement, observation nature, and valid time
CREATE INDEX IF NOT EXISTS idx_observations_lookup 
ON observations(location_id, measurement_type, observation_type, valid_at DESC);

-- Time-range queries across monitoring locations
CREATE INDEX IF NOT EXISTS idx_observations_valid_at 
ON observations(valid_at DESC);

-- Deduplication index for ground-truth observations:
-- Guarantees strictly at most one ground-truth measurement per station, provider, metric, and valid time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_observed 
ON observations(location_id, data_source_id, measurement_type, valid_at) 
WHERE observation_type = 'observed';

-- Deduplication index for numerical weather/hydrological forecasts:
-- Preserves distinct forecast cycles (e.g. 00Z vs 06Z model run) for the same valid time,
-- while preventing duplicate ingestion of the exact same forecast cycle.
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_forecast_cycle 
ON observations(location_id, data_source_id, measurement_type, forecast_reference_time, valid_at) 
WHERE observation_type = 'forecast' AND forecast_reference_time IS NOT NULL;

-- ---------------------------------------------------------------------
-- Security & Row Level Security (RLS) Foundation
-- ---------------------------------------------------------------------
-- Tables must NOT be publicly writable. RLS is enabled by default.
-- Anonymous / public users cannot write or mutate data.
ALTER TABLE basins ENABLE ROW LEVEL SECURITY;
ALTER TABLE districts ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE monitoring_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE observations ENABLE ROW LEVEL SECURITY;

-- Phase 1 Read-only policies for public/anon access where appropriate,
-- while write operations are restricted exclusively to authenticated service-role operations.
DO $$
BEGIN
    -- Public read policy for reference districts
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'districts' AND policyname = 'Allow public read access to districts'
    ) THEN
        CREATE POLICY "Allow public read access to districts" ON districts FOR SELECT USING (true);
    END IF;

    -- Public read policy for reference basins
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'basins' AND policyname = 'Allow public read access to basins'
    ) THEN
        CREATE POLICY "Allow public read access to basins" ON basins FOR SELECT USING (true);
    END IF;

    -- Public read policy for data sources
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'data_sources' AND policyname = 'Allow public read access to data sources'
    ) THEN
        CREATE POLICY "Allow public read access to data sources" ON data_sources FOR SELECT USING (true);
    END IF;
END $$;
