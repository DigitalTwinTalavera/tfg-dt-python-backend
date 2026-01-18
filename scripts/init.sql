-- Digital Twin Traffic Database Initialization
-- This script runs automatically on first container startup

-- Enable PostGIS extension for spatial data support
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Verify PostGIS installation
DO $$
BEGIN
    RAISE NOTICE 'PostGIS version: %', PostGIS_Version();
    RAISE NOTICE 'Digital Twin database initialized successfully';
END $$;
