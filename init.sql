-- Initial database setup for Yandex Maps Bot
-- This file runs automatically when PostgreSQL container starts for the first time

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE yandex_maps_bot TO postgres;
