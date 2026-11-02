-- Run once for a newly created PostgreSQL volume. QuantumLeap owns its tenant schemas.
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS postgis;
