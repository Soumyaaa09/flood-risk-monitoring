-- FloodWatch Phase 2.3.1: Seed Pilot Virtual Weather Locations
-- Target: PostgreSQL 15+ / Supabase with PostGIS
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this seed script without explicit approval.
-- This script inserts ONLY virtual atmospheric weather model interpolation centroids for the three pilot districts.
--
-- STRICT DISCLAIMER:
-- These locations are NOT official India Meteorological Department (IMD) observation stations,
-- nor are they Central Water Commission (CWC) or State river water level gauges.
-- Elevation values are explicitly left NULL because they have not been verified against an authoritative elevation model.

BEGIN;

-- Apply local search path strictly for the duration of this transaction
SET LOCAL search_path = public, extensions;

DO $$
DECLARE
    v_basin_id UUID;
    v_jagatsinghpur_id UUID;
    v_cuttack_id UUID;
    v_kendrapara_id UUID;
BEGIN
    -- Resolve parent basin (Mahanadi) with explicit verification
    SELECT id INTO v_basin_id FROM public.basins WHERE name = 'Mahanadi' LIMIT 1;
    IF v_basin_id IS NULL THEN
        RAISE EXCEPTION 'Parent basin "Mahanadi" not found in public.basins. Ensure 001_seed_initial_data.sql has run first.';
    END IF;

    -- Resolve pilot districts with explicit verification
    SELECT id INTO v_jagatsinghpur_id FROM public.districts WHERE name = 'Jagatsinghpur' LIMIT 1;
    SELECT id INTO v_cuttack_id FROM public.districts WHERE name = 'Cuttack' LIMIT 1;
    SELECT id INTO v_kendrapara_id FROM public.districts WHERE name = 'Kendrapara' LIMIT 1;

    IF v_jagatsinghpur_id IS NULL OR v_cuttack_id IS NULL OR v_kendrapara_id IS NULL THEN
        RAISE EXCEPTION 'One or more parent pilot districts (Jagatsinghpur, Cuttack, Kendrapara) not found in public.districts. Ensure 001_seed_initial_data.sql has run first.';
    END IF;

    -- 1. Paradeep Weather Grid Point (District: Jagatsinghpur)
    -- Coordinates: 20.3160°N, 86.6110°E
    INSERT INTO public.monitoring_locations (
        district_id,
        basin_id,
        name,
        location_type,
        coordinates,
        latitude,
        longitude,
        elevation_m,
        is_official_gauge,
        metadata
    ) VALUES (
        v_jagatsinghpur_id,
        v_basin_id,
        'Paradeep Weather Grid Point',
        'virtual_weather_point',
        extensions.ST_SetSRID(extensions.ST_MakePoint(86.6110, 20.3160), 4326),
        20.3160,
        86.6110,
        NULL, -- Elevation explicitly NULL until verified against an authoritative DEM/survey
        FALSE,
        '{
            "station_nature": "virtual_weather_grid",
            "pilot_area": "Paradeep",
            "provider_coverage": "Open-Meteo",
            "elevation_status": "unverified",
            "disclaimer": "Virtual weather grid centroid for coastal Jagatsinghpur/Paradeep. NOT an official IMD station or river gauge."
        }'::jsonb
    )
    ON CONFLICT (district_id, name) DO UPDATE
    SET basin_id = EXCLUDED.basin_id,
        location_type = EXCLUDED.location_type,
        coordinates = EXCLUDED.coordinates,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        elevation_m = NULL,
        is_official_gauge = FALSE,
        metadata = EXCLUDED.metadata,
        updated_at = clock_timestamp();

    -- 2. Cuttack Weather Grid Point (District: Cuttack)
    -- Coordinates: 20.4625°N, 85.8830°E
    INSERT INTO public.monitoring_locations (
        district_id,
        basin_id,
        name,
        location_type,
        coordinates,
        latitude,
        longitude,
        elevation_m,
        is_official_gauge,
        metadata
    ) VALUES (
        v_cuttack_id,
        v_basin_id,
        'Cuttack Weather Grid Point',
        'virtual_weather_point',
        extensions.ST_SetSRID(extensions.ST_MakePoint(85.8830, 20.4625), 4326),
        20.4625,
        85.8830,
        NULL, -- Elevation explicitly NULL until verified against an authoritative DEM/survey
        FALSE,
        '{
            "station_nature": "virtual_weather_grid",
            "pilot_area": "Cuttack Delta Apex",
            "provider_coverage": "Open-Meteo",
            "elevation_status": "unverified",
            "disclaimer": "Virtual weather grid centroid for Cuttack delta apex. NOT an official IMD station or river gauge."
        }'::jsonb
    )
    ON CONFLICT (district_id, name) DO UPDATE
    SET basin_id = EXCLUDED.basin_id,
        location_type = EXCLUDED.location_type,
        coordinates = EXCLUDED.coordinates,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        elevation_m = NULL,
        is_official_gauge = FALSE,
        metadata = EXCLUDED.metadata,
        updated_at = clock_timestamp();

    -- 3. Kendrapara Weather Grid Point (District: Kendrapara)
    -- Coordinates: 20.5017°N, 86.4223°E
    INSERT INTO public.monitoring_locations (
        district_id,
        basin_id,
        name,
        location_type,
        coordinates,
        latitude,
        longitude,
        elevation_m,
        is_official_gauge,
        metadata
    ) VALUES (
        v_kendrapara_id,
        v_basin_id,
        'Kendrapara Weather Grid Point',
        'virtual_weather_point',
        extensions.ST_SetSRID(extensions.ST_MakePoint(86.4223, 20.5017), 4326),
        20.5017,
        86.4223,
        NULL, -- Elevation explicitly NULL until verified against an authoritative DEM/survey
        FALSE,
        '{
            "station_nature": "virtual_weather_grid",
            "pilot_area": "Kendrapara Floodplain",
            "provider_coverage": "Open-Meteo",
            "elevation_status": "unverified",
            "disclaimer": "Virtual weather grid centroid for Kendrapara coastal floodplain. NOT an official IMD station or river gauge."
        }'::jsonb
    )
    ON CONFLICT (district_id, name) DO UPDATE
    SET basin_id = EXCLUDED.basin_id,
        location_type = EXCLUDED.location_type,
        coordinates = EXCLUDED.coordinates,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        elevation_m = NULL,
        is_official_gauge = FALSE,
        metadata = EXCLUDED.metadata,
        updated_at = clock_timestamp();
END $$;

COMMIT;
