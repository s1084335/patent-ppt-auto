-- Ensure database layer schemas exist.
-- Base tables should live in raw_layer / core_layer. This file does not create table views.

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw_layer;
CREATE SCHEMA IF NOT EXISTS core_layer;
CREATE SCHEMA IF NOT EXISTS derived_layer;
CREATE SCHEMA IF NOT EXISTS app_layer;

COMMENT ON SCHEMA raw_layer IS 'Layer 1 Raw Layer: source file tracking and original raw records.';
COMMENT ON SCHEMA core_layer IS 'Layer 2 Core Layer: normalized patent core tables.';
COMMENT ON SCHEMA derived_layer IS 'Layer 3 Derived / Analytics Layer: reserved for report and analytics tables/views.';
COMMENT ON SCHEMA app_layer IS 'Layer 4 API / Report Layer: reserved for API/report-facing views.';

COMMIT;
