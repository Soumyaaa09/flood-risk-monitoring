-- FloodWatch Phase 1: Initial Reference Data Seed
-- Target: PostgreSQL 15+ / Supabase
--
-- IMPORTANT SAFETY NOTICE:
-- Do NOT execute this seed script without explicit approval.
-- This script inserts ONLY verified administrative entities:
-- 1. Basin: Mahanadi
-- 2. Pilot Districts: Jagatsinghpur, Cuttack, Kendrapara
-- 3. Data Sources: Open-Meteo Weather API, CWC Portal Investigation, Open-Meteo Flood Connector
--
-- NO fake monitoring stations, NO invented river gauge readings, and NO arbitrary thresholds are inserted.

SET search_path = public, extensions;

-- 1. Insert Basin: Mahanadi
INSERT INTO basins (name, code, description, metadata)
VALUES (
    'Mahanadi',
    'MAHANADI_BASIN',
    'Major river basin in East Central India draining into the Bay of Bengal through Odisha delta.',
    '{"drainage_area_sq_km": 141600, "primary_state": "Odisha"}'::jsonb
)
ON CONFLICT (name) DO UPDATE 
SET description = EXCLUDED.description;

-- 2. Insert Pilot Districts
-- Approximate administrative centroids provided for spatial indexing purposes
DO $$
DECLARE
    v_basin_id UUID;
BEGIN
    SELECT id INTO v_basin_id FROM basins WHERE name = 'Mahanadi' LIMIT 1;

    -- Jagatsinghpur (Initial monitoring focus area: Paradeep)
    INSERT INTO districts (basin_id, name, state, census_code, centroid, metadata)
    VALUES (
        v_basin_id,
        'Jagatsinghpur',
        'Odisha',
        '393',
        extensions.ST_SetSRID(extensions.ST_MakePoint(86.347, 20.258), 4326),
        '{"coastal": true, "key_monitoring_focus_area": "Paradeep", "notes": "Coastal delta district vulnerable to cyclonic rainfall and estuarine surge."}'::jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET metadata = EXCLUDED.metadata;

    -- Cuttack
    INSERT INTO districts (basin_id, name, state, census_code, centroid, metadata)
    VALUES (
        v_basin_id,
        'Cuttack',
        'Odisha',
        '383',
        extensions.ST_SetSRID(extensions.ST_MakePoint(85.882, 20.462), 4326),
        '{"coastal": false, "notes": "Head of the Mahanadi delta bifurcation system into Kathajodi and Mahanadi main stem."}'::jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET metadata = EXCLUDED.metadata;

    -- Kendrapara
    INSERT INTO districts (basin_id, name, state, census_code, centroid, metadata)
    VALUES (
        v_basin_id,
        'Kendrapara',
        'Odisha',
        '382',
        extensions.ST_SetSRID(extensions.ST_MakePoint(86.422, 20.501), 4326),
        '{"coastal": true, "notes": "Coastal floodplain district intersecting Brahmani and Mahanadi distributaries."}'::jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET metadata = EXCLUDED.metadata;
END $$;

-- 3. Register Data Sources
INSERT INTO data_sources (name, source_type, base_url, is_active, description, metadata)
VALUES
    (
        'open_meteo_weather',
        'weather_api',
        'https://api.open-meteo.com/v1',
        TRUE,
        'Open-Meteo Weather Forecast API providing precipitation, temperature, and atmospheric forecasts.',
        '{"license": "CC-BY-4.0", "rate_limit": "non-commercial free tier"}'::jsonb
    ),
    (
        'cwc_nwdp_portal',
        'hydrological_portal',
        'https://indiawris.gov.in',
        FALSE,
        'Central Water Commission / National Water Data Portal (Integration to be investigated in Phase 2).',
        '{"status": "investigation_pending", "auth_required": true}'::jsonb
    ),
    (
        'open_meteo_flood',
        'flood_api',
        'https://flood-api.open-meteo.com/v1',
        FALSE,
        'Open-Meteo Global Flood API for river discharge forecasts (Future connector planned for Phase 2).',
        '{"status": "future_connector", "measurement": "discharge_m3_s"}'::jsonb
    )
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description,
    is_active = EXCLUDED.is_active,
    metadata = EXCLUDED.metadata;
