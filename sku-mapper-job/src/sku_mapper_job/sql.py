"""SQL DDL and query constants for the SKU mapper job."""

# -- DDL -----------------------------------------------------------------------

CREATE_VM_SKU_CATALOG = """\
CREATE TABLE IF NOT EXISTS vm_sku_catalog (
    sku_name        TEXT PRIMARY KEY,
    tier            TEXT,
    family          TEXT,
    series          TEXT,
    version         TEXT,
    vcpus           INTEGER,
    sku_type        TEXT,
    category        TEXT,
    workload_tags   TEXT[],
    source          TEXT NOT NULL DEFAULT 'naming',
    first_seen_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vm_sku_catalog_category
    ON vm_sku_catalog (category);

CREATE INDEX IF NOT EXISTS idx_vm_sku_catalog_sku_type
    ON vm_sku_catalog (sku_type);
"""

# -- Read SKUs -----------------------------------------------------------------

SELECT_DISTINCT_SKUS = """\
SELECT arm_sku_name AS sku_name
  FROM retail_prices_vm
 WHERE arm_sku_name IS NOT NULL
UNION
SELECT sku_name
  FROM spot_eviction_rates
UNION
SELECT sku_name
  FROM spot_price_history;
"""

# -- Upsert --------------------------------------------------------------------

UPSERT_SKU_CATALOG = """\
INSERT INTO vm_sku_catalog (
    sku_name, tier, family, series, version, vcpus,
    sku_type, category, workload_tags, source
)
VALUES (
    %(sku_name)s, %(tier)s, %(family)s, %(series)s, %(version)s, %(vcpus)s,
    %(sku_type)s, %(category)s, %(workload_tags)s, %(source)s
)
ON CONFLICT (sku_name) DO UPDATE SET
    tier           = EXCLUDED.tier,
    family         = EXCLUDED.family,
    series         = EXCLUDED.series,
    version        = EXCLUDED.version,
    vcpus          = EXCLUDED.vcpus,
    sku_type       = EXCLUDED.sku_type,
    category       = EXCLUDED.category,
    workload_tags  = EXCLUDED.workload_tags,
    last_seen_utc  = NOW(),
    updated_at_utc = NOW();
"""

# -- Job tracking --------------------------------------------------------------

INSERT_JOB_RUN = """\
INSERT INTO job_runs (run_id, dataset, status, started_at_utc)
VALUES (%(run_id)s, %(dataset)s, 'running', NOW());
"""

UPDATE_JOB_RUN_OK = """\
UPDATE job_runs
   SET status         = 'ok',
       finished_at_utc = NOW(),
       items_read      = %(items_read)s,
       items_written   = %(items_written)s
 WHERE run_id = %(run_id)s;
"""

UPDATE_JOB_RUN_ERROR = """\
UPDATE job_runs
   SET status         = 'error',
       finished_at_utc = NOW(),
       error_message   = %(error_message)s
 WHERE run_id = %(run_id)s;
"""
