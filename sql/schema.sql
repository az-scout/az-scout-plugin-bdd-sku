-- az-scout-plugin-bdd-sku: PostgreSQL 17 schema
-- Mounted into postgres container at /docker-entrypoint-initdb.d/01-schema.sql

BEGIN;

-- ============================================================
-- job_runs: tracks ingestion runs for each collector
-- ============================================================
CREATE TABLE IF NOT EXISTS job_runs (
    run_id       UUID PRIMARY KEY,
    dataset      TEXT NOT NULL,          -- azure_pricing
    status       TEXT NOT NULL,          -- running | ok | error
    started_at_utc  TIMESTAMPTZ NOT NULL,
    finished_at_utc TIMESTAMPTZ,
    items_read   BIGINT NOT NULL DEFAULT 0,
    items_written BIGINT NOT NULL DEFAULT 0,
    error_message TEXT,
    details      JSONB
);

CREATE INDEX IF NOT EXISTS idx_job_runs_dataset_started
    ON job_runs (dataset, started_at_utc DESC);

-- ============================================================
-- job_logs: per-run log entries
-- ============================================================
CREATE TABLE IF NOT EXISTS job_logs (
    id       BIGSERIAL PRIMARY KEY,
    run_id   UUID NOT NULL REFERENCES job_runs(run_id) ON DELETE CASCADE,
    ts_utc   TIMESTAMPTZ NOT NULL,
    level    TEXT NOT NULL,              -- info | warning | error
    message  TEXT NOT NULL,
    context  JSONB
);

CREATE INDEX IF NOT EXISTS idx_job_logs_run_ts
    ON job_logs (run_id, ts_utc DESC);

-- ============================================================
-- retail_prices_vm: VM retail pricing from Azure Retail Prices API
-- ============================================================
CREATE TABLE IF NOT EXISTS retail_prices_vm (
    job_id              TEXT,
    job_datetime        TIMESTAMPTZ,
    job_type            TEXT,
    currency_code       TEXT NOT NULL,
    tier_minimum_units  NUMERIC,
    retail_price        NUMERIC,
    unit_price          NUMERIC,
    arm_region_name     TEXT NOT NULL,
    location            TEXT,
    effective_start_date TIMESTAMPTZ,
    meter_id            TEXT,
    meter_name          TEXT,
    product_id          TEXT,
    sku_id              TEXT,
    product_name        TEXT,
    sku_name            TEXT,
    service_name        TEXT,
    service_id          TEXT,
    service_family      TEXT,
    unit_of_measure     TEXT,
    pricing_type        TEXT,
    is_primary_meter_region BOOLEAN,
    arm_sku_name        TEXT,
    reservation_term    TEXT,
    savings_plan        JSONB,
    UNIQUE (currency_code, arm_region_name, sku_id, pricing_type, reservation_term)
);

CREATE INDEX IF NOT EXISTS idx_retail_prices_vm_region
    ON retail_prices_vm (arm_region_name);
CREATE INDEX IF NOT EXISTS idx_retail_prices_vm_sku
    ON retail_prices_vm (arm_sku_name);
CREATE INDEX IF NOT EXISTS idx_retail_prices_vm_job
    ON retail_prices_vm (job_id);

-- ============================================================
-- spot_eviction_rates: VM spot eviction rates from Resource Graph
-- ============================================================
CREATE TABLE IF NOT EXISTS spot_eviction_rates (
    job_id       TEXT NOT NULL,
    job_datetime TIMESTAMPTZ NOT NULL,
    sku_name     TEXT NOT NULL,
    region       TEXT NOT NULL,
    eviction_rate TEXT NOT NULL,
    UNIQUE (sku_name, region, job_id)
);

CREATE INDEX IF NOT EXISTS idx_spot_eviction_region
    ON spot_eviction_rates (region);
CREATE INDEX IF NOT EXISTS idx_spot_eviction_sku
    ON spot_eviction_rates (sku_name);
CREATE INDEX IF NOT EXISTS idx_spot_eviction_job_datetime
    ON spot_eviction_rates (job_datetime DESC);

-- ============================================================
-- spot_price_history: VM spot price history from Resource Graph
-- ============================================================
CREATE TABLE IF NOT EXISTS spot_price_history (
    job_id        TEXT,
    job_datetime  TIMESTAMPTZ,
    sku_name      TEXT NOT NULL,
    os_type       TEXT NOT NULL,
    region        TEXT NOT NULL,
    price_history JSONB NOT NULL,
    UNIQUE (sku_name, os_type, region)
);

CREATE INDEX IF NOT EXISTS idx_spot_price_region
    ON spot_price_history (region);
CREATE INDEX IF NOT EXISTS idx_spot_price_sku
    ON spot_price_history (sku_name);

COMMIT;
