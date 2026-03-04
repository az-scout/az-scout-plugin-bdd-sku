# az-scout-plugin-bdd-sku

[az-scout](https://github.com/lrivallain/az-scout) plugin that caches **Azure VM retail prices**, **Spot eviction rates**, and **Spot price history** in a local PostgreSQL database. Enables fast, offline queries without calling Azure APIs every time.

## Architecture

```
┌────────────────────┐         ┌───────────────────────────┐         ┌──────────────┐
│     az-scout       │  GET    │   Plugin routes            │  async  │  PostgreSQL  │
│  (FastAPI :5001)   │ ──────▸ │  /plugins/bdd-sku/        │ ──────▸ │   (:5432)    │
│                    │         │    /status                 │         │              │
│  MCP server        │         │    /spot/eviction-rates    │         │ retail_prices│
│  24 tools exposed  │         │    /spot/eviction-rates/   │         │ spot_evict.  │
│                    │         │         history            │         │ spot_price_h.│
│                    │         │    /spot/price-history     │         │ vm_sku_catal.│
│                    │         │    /spot/price-history     │         │ job_runs     │
└────────────────────┘         └───────────────────────────┘         │ job_logs     │
                                                                     └──────┬───────┘
┌────────────────────┐                                                      │
│  Standalone API    │  GET (async)                                         │
│  (Container Apps)  │ ────────────────────────────────────────────────────┘
│  azscout-api:8000  │  ◂── MSI token auth (Entra ID)
│                    │
│  /health           │
│  /v1/*  (25 EP)    │
│  /status, /spot/*  │
└────────────────────┘
                                                                     ┌──────┴───────┐
┌────────────────────┐                                               │  PostgreSQL  │
│  Ingestion Jobs    │  INSERT (psycopg2)                            │   (:5432)    │
│  (Container Apps)  │ ─────────────────────────────────────────────▸│              │
│                    │  ◂── Azure Retail Prices API (public, no auth)└──────┬───────┘
│  3 jobs:           │  ◂── Azure Resource Graph API (SpotResources)        │
│  - daily (02:00)   │                                                      │
│  - hourly (evict.) │                                                      │
│  - manual          │                                                      │
└────────────────────┘                                                      │
                                                                            │
┌────────────────────┐                                                      │
│  SKU Mapper Job    │  READ distinct SKUs + UPSERT vm_sku_catalog          │
│  (Container Apps)  │ ─────────────────────────────────────────────────────┘
│                    │
│  daily (04:00)     │  ◂── No external API (local data only)
│  password auth     │
└────────────────────┘
```

**Four independent components:**

| Component | Role | Technology |
|---|---|---|
| **Plugin** (`src/az_scout_bdd_sku/`) | UI tab + API routes + MCP tools, read-only from Postgres | FastAPI, psycopg (async), psycopg_pool |
| **Standalone API** (`api/`) | Dedicated Container App exposing all endpoints over HTTPS 24/7 | FastAPI, uvicorn, azure-identity |
| **Ingestion** (`ingestion/`) | One-shot CLI jobs that collect prices and insert them into Postgres | requests, psycopg2-binary, azure-identity |
| **SKU Mapper** (`sku-mapper-job/`) | Daily batch job that enriches the `vm_sku_catalog` table (family, category, vCPU…) | psycopg3, regex parser |

---

## API Endpoints

All endpoints are mounted under `/plugins/bdd-sku/` by az-scout.

### `GET /plugins/bdd-sku/status`

Global database status: connectivity, table counts, and last ingestion run.

**Response:**

```json
{
  "db_connected": true,
  "retail_prices_count": 42000,
  "spot_eviction_rates_count": 11237,
  "spot_price_history_count": 243335,
  "last_run": {
    "run_id": "a1b2c3...",
    "status": "ok",
    "started_at_utc": "2026-01-15T10:00:00+00:00",
    "finished_at_utc": "2026-01-15T10:05:00+00:00",
    "items_read": 45000,
    "items_written": 42000,
    "error_message": null
  }
}
```

---

### `GET /plugins/bdd-sku/spot/eviction-rates`

Spot VM eviction rates. Without `job_id`, returns only the **latest snapshot** (most recent `job_datetime`).

**Query parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `region` | string | No | Exact filter by Azure region (e.g. `eastus`) |
| `sku_name` | string | No | Case-insensitive substring filter (e.g. `D2s` → `Standard_D2s_v3`) |
| `job_id` | string | No | UUID of a specific snapshot. If omitted, returns the latest |
| `limit` | int | No | Maximum number of rows (default: 200, min: 1, max: 5000) |

**Example:**

```bash
# Latest snapshot, filtered on eastus
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates?region=eastus"

# Specific snapshot
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates?job_id=abc-123"
```

**Response:**

```json
{
  "count": 150,
  "items": [
    {
      "sku_name": "Standard_D2s_v3",
      "region": "eastus",
      "eviction_rate": "5-10%",
      "job_id": "abc-123",
      "job_datetime": "2026-03-02T14:00:00+00:00"
    }
  ]
}
```

---

### `GET /plugins/bdd-sku/spot/eviction-rates/history`

Lists the **available snapshots** of eviction rates. Each snapshot corresponds to a collector execution (a unique `job_id`).

**Query parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | int | No | Maximum number of snapshots (default: 50, min: 1, max: 500) |

**Example:**

```bash
curl "http://localhost:5001/plugins/bdd-sku/spot/eviction-rates/history"
```

**Response:**

```json
{
  "count": 24,
  "snapshots": [
    {
      "job_id": "abc-123",
      "job_datetime": "2026-03-02T14:00:00+00:00",
      "row_count": 11237
    },
    {
      "job_id": "def-456",
      "job_datetime": "2026-03-02T13:00:00+00:00",
      "row_count": 11235
    }
  ]
}
```

---

### `GET /plugins/bdd-sku/spot/price-history`

Spot VM price history (price array per SKU×region×OS).

**Query parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `region` | string | No | Exact filter by Azure region |
| `sku_name` | string | No | Case-insensitive substring filter |
| `os_type` | string | No | Filter by OS (`Linux` or `Windows`) |
| `limit` | int | No | Maximum number of rows (default: 200, min: 1, max: 5000) |

**Example:**

```bash
curl "http://localhost:5001/plugins/bdd-sku/spot/price-history?region=westeurope&os_type=Linux&sku_name=D4s"
```

**Response:**

```json
{
  "count": 5,
  "items": [
    {
      "sku_name": "Standard_D4s_v3",
      "os_type": "Linux",
      "region": "westeurope",
      "price_history": [
        {"timestamp": "2026-03-01T00:00:00Z", "spotPrice": 0.042},
        {"timestamp": "2026-02-28T00:00:00Z", "spotPrice": 0.045}
      ]
    }
  ]
}
```

---

## MCP Tools

The plugin exposes 4 tools on the az-scout MCP server, usable by LLMs in the integrated chat.

| Tool | Parameters | Description |
|---|---|---|
| `cache_status` | *(none)* | Database status: connectivity, count per table, last run |
| `get_spot_eviction_rates` | `region?`, `sku_name?`, `job_id?` | Spot eviction rates. Without `job_id` → latest snapshot |
| `get_spot_eviction_history` | *(none)* | Lists available eviction snapshots (last 50) |
| `get_spot_price_history` | `region?`, `sku_name?`, `os_type?` | Spot price history per SKU×region×OS |
| `v1_status` | *(none)* | v1 status: DB health, stats per dataset |
| `v1_list_locations` | `limit?`, `cursor?` | List Azure regions (paginated) |
| `v1_list_skus` | `search?`, `limit?`, `cursor?` | List VM SKUs (paginated) |
| `v1_retail_prices` | `region?`, `sku?`, `currency?`, `limit?`, `cursor?` | VM retail prices (paginated) |
| `v1_eviction_rates` | `region?`, `sku?`, `limit?`, `cursor?` | Spot eviction rates (paginated) |
| `v1_eviction_rates_latest` | `region?`, `sku?`, `limit?` | Latest eviction rate per (region, sku) |
| `v1_pricing_categories` | `limit?`, `cursor?` | Distinct pricing categories (paginated) |
| `v1_pricing_summary` | `region?`, `category?`, `priceType?`, `snapshotSince?`, `limit?`, `cursor?` | Aggregated price summaries (multi-value, paginated) |
| `v1_pricing_summary_latest` | `region?`, `category?`, `priceType?`, `limit?`, `cursor?` | Summaries from latest run (paginated) |
| `v1_pricing_summary_series` | `region`, `priceType`, `bucket`, `metric?`, `category?` | Time series of a price metric |
| `v1_pricing_cheapest` | `priceType`, `metric?`, `category?`, `limit?` | Top N cheapest regions |
| `v1_sku_catalog` | `search?`, `category?`, `family?`, `min_vcpus?`, `max_vcpus?`, `limit?`, `cursor?` | Full VM SKU catalog (paginated) |
| `v1_jobs` | `dataset?`, `status?`, `limit?`, `cursor?` | Ingestion job runs (paginated, most recent first) |
| `v1_job_logs` | `run_id`, `level?`, `limit?`, `cursor?` | Logs for a specific job run (paginated) |
| `v1_spot_prices_series` | `region`, `sku`, `os_type?`, `bucket?` | Spot price time series (denormalized JSONB) |
| `v1_retail_prices_compare` | `sku`, `currency?`, `pricing_type?` | Compare a SKU across all regions |
| `v1_spot_detail` | `region`, `sku`, `os_type?` | Composite Spot detail (price + eviction + catalog) |
| `v1_savings_plans` | `region?`, `sku?`, `currency?`, `limit?`, `cursor?` | Retail prices with savings plan data (paginated) |
| `v1_pricing_summary_compare` | `regions`, `price_type?`, `category?` | Compare pricing summaries between regions |
| `v1_stats` | *(none)* | Global dashboard metrics |

---

## v1 API — Read-only (cursor-paginated)

The v1 API is mounted under `/plugins/bdd-sku/v1/` and provides read-only access
to PostgreSQL data via high-performance keyset (cursor) pagination.

**Base path :** `/plugins/bdd-sku/v1`

### Pagination

All paginated endpoints use **keyset pagination**:
- `limit`: number of items per page (1–5000, default 1000)
- `cursor`: opaque token (base64url) returned in the response `page.cursor`
- `page.hasMore`: `true` if a next page exists

To iterate through all pages:
```bash
# Page 1
curl ".../v1/locations?limit=100"
# → page.cursor = "eyJuYW1lIjoi..."
# Page 2
curl ".../v1/locations?limit=100&cursor=eyJuYW1lIjoi..."
```

### JSON Contract

All 2xx responses follow the `ListResponse<T>` structure:

```json
{
  "items": [...],
  "page": { "limit": 1000, "cursor": "...", "hasMore": true },
  "meta": { "dataSource": "local-db", "generatedAt": "2026-03-02T..." }
}
```

Errors follow `ErrorResponse`:

```json
{
  "error": { "code": "BAD_REQUEST", "message": "..." },
  "meta": { "dataSource": "local-db", "generatedAt": "..." }
}
```

### v1 Endpoints

| # | Endpoint | Method | Paginated | Description |
|---|---|---|---|---|
| 1 | `/v1/status` | GET | No | DB health, stats per dataset, API version |
| 2 | `/v1/locations` | GET | Yes | Distinct region names (union of 3 tables) |
| 3 | `/v1/skus` | GET | Yes | Distinct SKU names (`search` ILIKE filter) |
| 4 | `/v1/currencies` | GET | Yes | Distinct currency codes (retail) |
| 5 | `/v1/os-types` | GET | Yes | Distinct OS types (spot) |
| 6 | `/v1/retail/prices` | GET | Yes | Retail prices with filters (region, sku, currency, effectiveAt, updatedSince) |
| 7 | `/v1/retail/prices/latest` | GET | Yes | Latest retail snapshot per unique key |
| 8 | `/v1/spot/prices` | GET | Yes | Spot price history (sample=raw only, otherwise 501) |
| 9 | `/v1/spot/eviction-rates` | GET | Yes | Spot eviction rates (filters: region, sku, updatedSince) |
| 10 | `/v1/spot/eviction-rates/series` | GET | No | Aggregated time series (bucket=hour\|day\|week, agg=avg\|min\|max) |
| 11 | `/v1/spot/eviction-rates/latest` | GET | No | Latest rate per (region, sku) |
| 12 | `/v1/pricing/categories` | GET | Yes | Distinct pricing categories |
| 13 | `/v1/pricing/summary` | GET | Yes | Aggregated summaries (multi-value: region, category, priceType) |
| 14 | `/v1/pricing/summary/latest` | GET | Yes | Summaries from latest aggregation run |
| 15 | `/v1/pricing/summary/series` | GET | No | Time series (bucket=day\|week\|month, metric=avg\|median\|…) |
| 16 | `/v1/pricing/summary/cheapest` | GET | No | Top N cheapest regions (latest run) |
| 17 | `/v1/skus/catalog` | GET | Yes | Full VM SKU catalog (category, family, vCPU, memory…) |
| 18 | `/v1/jobs` | GET | Yes | Ingestion job runs (most recent first) |
| 19 | `/v1/jobs/{run_id}/logs` | GET | Yes | Logs for a specific job run |
| 20 | `/v1/spot/prices/series` | GET | No | Spot price time series (denormalized JSONB, bucket=day\|week\|month) |
| 21 | `/v1/retail/prices/compare` | GET | No | Compare a SKU across all regions |
| 22 | `/v1/spot/detail` | GET | No | Composite Spot detail (price + eviction + SKU catalog) |
| 23 | `/v1/retail/savings-plans` | GET | Yes | Retail prices with savings plan data |
| 24 | `/v1/pricing/summary/compare` | GET | No | Compare price summaries between regions |
| 25 | `/v1/stats` | GET | No | Global metrics: table counts, regions, SKUs, data freshness |

### curl Examples

```bash
# Status
curl "http://localhost:5001/plugins/bdd-sku/v1/status"

# Retail prices (first 100 in USD, region eastus)
curl "http://localhost:5001/plugins/bdd-sku/v1/retail/prices?region=eastus&currency=USD&limit=100"

# Eviction rates updated in the last 24h
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/eviction-rates?updatedSince=2026-03-01T00:00:00Z&limit=500"

# Hourly eviction series for a SKU
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/eviction-rates/series?region=eastus&sku=Standard_D2s_v3&bucket=hour"

# Prix Spot (raw)
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/prices?region=westeurope&sku=Standard_D4s_v3"

# Pricing categories
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/categories?limit=50"

# Pricing summaries (multi-value region + priceType)
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/summary?region=eastus&region=westeurope&priceType=spot&limit=100"

# Latest pricing run
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/summary/latest?priceType=retail"

# Median time series, monthly bucket
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/summary/series?region=eastus&priceType=spot&bucket=month&metric=median"

# Top 5 cheapest regions
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/summary/cheapest?priceType=spot&metric=median&limit=5"

# SKU catalog (filtered by category)
curl "http://localhost:5001/plugins/bdd-sku/v1/skus/catalog?category=General%20purpose&limit=50"

# Ingestion jobs (errors only)
curl "http://localhost:5001/plugins/bdd-sku/v1/jobs?status=error"

# Job logs
curl "http://localhost:5001/plugins/bdd-sku/v1/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/logs?level=error"

# Spot price series (monthly bucket)
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/prices/series?region=eastus&sku=Standard_D2s_v3&bucket=month"

# Compare a SKU across regions
curl "http://localhost:5001/plugins/bdd-sku/v1/retail/prices/compare?sku=Standard_D2s_v3&currency=USD"

# Composite Spot detail
curl "http://localhost:5001/plugins/bdd-sku/v1/spot/detail?region=eastus&sku=Standard_D2s_v3"

# Savings plans
curl "http://localhost:5001/plugins/bdd-sku/v1/retail/savings-plans?region=eastus&limit=100"

# Compare pricing between regions
curl "http://localhost:5001/plugins/bdd-sku/v1/pricing/summary/compare?regions=eastus&regions=westeurope&priceType=spot"

# Global stats
curl "http://localhost:5001/plugins/bdd-sku/v1/stats"
```

> **Note:** `spot/prices` returns `price_history` as-is (JSONB). The `from`/`to` parameters filter on `job_datetime` (snapshot). The `sample=hourly|daily` mode is not yet implemented (returns 501).

### OpenAPI Specification

The full specification is available at [`openapi/v1.yaml`](openapi/v1.yaml) (OpenAPI 3.0.3).

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker & Docker Compose
- [az-scout](https://github.com/lrivallain/az-scout) installed (`uv pip install az-scout` or in dev mode)

---

## Local Setup

### 1. Start PostgreSQL

```bash
cd postgresql/
docker compose up -d
```

This starts a PostgreSQL 17 container with:
- **Database**: `azscout`, **user**: `azscout`, **password**: `azscout`
- **Port**: `localhost:5432`
- The schema (`sql/schema.sql`) is applied automatically on first startup

Verify that Postgres is ready:

```bash
docker compose ps           # State: running (healthy)
docker compose logs postgres  # ... database system is ready to accept connections
```

### 2. Install the plugin in development mode

```bash
# From the repo root
uv pip install -e ".[dev]"
```

The plugin is automatically discovered by az-scout thanks to the `az_scout.plugins` entry point in `pyproject.toml`.

### 3. (Optional) Configure the PostgreSQL connection

By default, the plugin connects to `localhost:5432/azscout` (user/pass: `azscout`). To customize:

Create `~/.config/az-scout/bdd-sku.toml`:

```toml
[database]
host = "localhost"
port = 5432
dbname = "azscout"
user = "azscout"
password = "azscout"
sslmode = "disable"
```

Or point to a custom file:

```bash
export AZ_SCOUT_BDD_SKU_CONFIG=/chemin/vers/mon/config.toml
```

### 4. Start az-scout

```bash
uv run az-scout web --host 0.0.0.0 --port 5001 --reload --no-open -v
```

Open http://localhost:5001 — the **SKU DB Cache** tab appears in the navigation bar.

### 5. Populate the database (ingestion)

Ingestion is a separate **one-shot Docker job**:

```bash
cd ingestion/

# Build the image
docker build -t bdd-sku-ingestion .

# Run the ingestion
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  bdd-sku-ingestion
```

> **Note:** `--network postgresql_default` allows the container to reach the Postgres from `docker compose`. The network name may vary — verify with `docker network ls`.

#### Ingestion environment variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `azscout` | Database name |
| `POSTGRES_USER` | `azscout` | User |
| `POSTGRES_PASSWORD` | `azscout` | Password |
| `POSTGRES_SSLMODE` | `disable` | SSL mode (`disable`, `require`, `verify-full`) |
| `ENABLE_AZURE_PRICING_COLLECTOR` | `false` | Enable the retail pricing collector |
| `AZURE_PRICING_MAX_ITEMS` | `-1` | Pricing item limit (-1 = unlimited) |
| `AZURE_PRICING_API_RETRY_ATTEMPTS` | `3` | Number of retries on pricing API error |
| `AZURE_PRICING_API_RETRY_DELAY` | `2.0` | Delay between pricing retries (seconds) |
| `AZURE_PRICING_FILTERS` | `{}` | OData JSON filters (e.g. `{"serviceName": "Virtual Machines"}`) |
| `ENABLE_AZURE_SPOT_COLLECTOR` | `false` | Enable the Spot collector (eviction + prices) |
| `AZURE_SPOT_EVICTION_ONLY` | `false` | Eviction-only mode (skip Spot price history) |
| `AZURE_SPOT_MAX_ITEMS` | `-1` | Spot item limit (-1 = unlimited) |
| `AZURE_SPOT_API_RETRY_ATTEMPTS` | `3` | Number of retries on Spot API error |
| `AZURE_SPOT_API_RETRY_DELAY` | `2.0` | Delay between Spot retries (seconds) |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `JOB_TYPE` | `manual` | Job type (metadata) |

#### Example with filters

```bash
# Pricing collection with filters
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  -e 'AZURE_PRICING_FILTERS={"serviceName": "Virtual Machines"}' \
  -e AZURE_PRICING_MAX_ITEMS=1000 \
  -e LOG_LEVEL=DEBUG \
  bdd-sku-ingestion

# Spot collection (eviction + prices) — requires Azure credentials
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_SPOT_COLLECTOR=true \
  bdd-sku-ingestion

# Eviction only (hourly historization mode)
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_SPOT_COLLECTOR=true \
  -e AZURE_SPOT_EVICTION_ONLY=true \
  bdd-sku-ingestion
```

### 6. Check the status

- **UI**: SKU DB Cache tab → click "Refresh Status"
- **API**: `curl http://localhost:5001/plugins/bdd-sku/status`
- **MCP**: The `cache_status` tool is automatically exposed on the MCP server

The `/status` response:

```json
{
  "db_connected": true,
  "retail_prices_count": 42000,
  "spot_eviction_rates_count": 11237,
  "spot_price_history_count": 243335,
  "last_run": {
    "run_id": "a1b2c3...",
    "status": "ok",
    "started_at_utc": "2026-01-15T10:00:00+00:00",
    "finished_at_utc": "2026-01-15T10:05:00+00:00",
    "items_read": 45000,
    "items_written": 42000,
    "error_message": null
  }
}
```

---

## Azure Deployment (Terraform)

The Azure infrastructure is defined in the `infra/` folder and is deployed with Terraform. It creates:

- **Azure Database for PostgreSQL – Flexible Server** (Burstable, PostgreSQL 17)
- **Azure Container Registry** (Basic) to store the ingestion image
- **Azure Container Apps Environment** with Log Analytics
- **3 Container Apps Jobs**:
  - `{prefix}-sched` — daily cron (02:00 UTC): full collection (pricing + spot)
  - `{prefix}-spot-evict` — hourly cron: eviction only (historization)
  - `{prefix}-manual` — on-demand manual trigger
- **1 Container Apps Job – SKU Mapper**:
  - `sku-mapper-job` — daily cron (04:00 UTC): enriches `vm_sku_catalog` from collected SKUs

### Ingestion Jobs

| Job | Cron | Collectors | CPU/Mem | Timeout |
|---|---|---|---|---|
| **Scheduled** (`-sched`) | `0 2 * * *` (02:00 UTC) | pricing + spot (eviction + prices) | 1 CPU / 2 Gi | 3600s |
| **Spot Eviction Hourly** (`-spot-evict`) | `0 * * * *` (every hour) | spot eviction only | 0.5 CPU / 1 Gi | 900s |
| **Manual** (`-manual`) | On-demand | pricing + spot | 1 CPU / 2 Gi | 21600s |
| **SKU Mapper** (`sku-mapper-job`) | `0 4 * * *` (04:00 UTC) | parse & enrich `vm_sku_catalog` | 0.25 CPU / 0.5 Gi | 600s |

The hourly job (`spot-evict`) runs the Spot collector with `AZURE_SPOT_EVICTION_ONLY=true`, collecting only the ~11,000 eviction rates without querying the price history (~243,000 rows). Each execution creates a new snapshot in `spot_eviction_rates` (identified by `job_id`), allowing you to track eviction rate evolution over time.

The **SKU Mapper** job (`sku-mapper-job`) runs at 04:00 UTC, after ingestion completes (02:00 UTC). It reads all distinct SKU names from the 3 data tables (`retail_prices_vm`, `spot_eviction_rates`, `spot_price_history`), parses the `Standard_…` naming convention via regex to extract family, series, version, and vCPU count, then upserts the results into `vm_sku_catalog`. The job is idempotent and uses password authentication (like the ingestion jobs).

### 1. Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) logged in (`az login`)
- An Azure subscription with sufficient permissions

### 2. Configuration

```bash
cd infra/

# Copy the example file and fill in your values
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` — at minimum provide:

| Variable | Description |
|---|---|
| `subscription_id` | Your Azure subscription ID |
| `postgres_admin_password` | Strong password for the PostgreSQL server |

### 3. Deploy

```bash
terraform init
terraform plan        # Review resources that will be created
terraform apply       # Confirm with 'yes'
```

### 4. Build and push images

After deployment, build the images in ACR:

```bash
# Get the registry name
ACR_NAME=$(terraform output -raw container_registry_login_server)

# Ingestion image
az acr build --registry ${ACR_NAME%%.*} --image bdd-sku-ingestion:latest ../ingestion/

# SKU Mapper image
az acr build --registry ${ACR_NAME%%.*} --image sku-mapper-job:latest ../sku-mapper-job/
```

### 5. Apply the PostgreSQL schema

```bash
PG_FQDN=$(terraform output -raw postgresql_fqdn)

psql "host=$PG_FQDN port=5432 dbname=azscout user=azscout sslmode=require" \
  -f ../sql/schema.sql
```

### 6. Trigger a manual job (optional)

```bash
az containerapp job start \
  --resource-group $(terraform output -raw resource_group_name) \
  --name $(terraform output -raw pricing_manual_job_name)
```

### 7. Destroy the infrastructure

```bash
terraform destroy     # Confirmer avec 'yes'
```

> **Note:** `terraform.tfvars` contains secrets (PostgreSQL password). This file is ignored by `.gitignore` and should **never** be committed.

---

## API Standalone (Container App)

The standalone API is a self-contained FastAPI container (`api/`) that exposes **all endpoints** (legacy + v1) directly over HTTPS, without depending on az-scout. It runs 24/7 in Azure Container Apps with auto-scaling.

### Architecture

```
Internet
    │
    ▼
┌──────────────────────────────┐
│  Azure Container App         │
│  azscout-api (:8000)         │
│                              │
│  GET /health                 │  ← liveness/readiness probe
│  GET /status                 │  ← legacy endpoints (4)
│  GET /v1/status              │  ← v1 endpoints (11)
│  GET /v1/retail/prices       │
│  GET /v1/spot/eviction-rates │
│  ...                         │
│                              │
│  Auth DB : MSI (Entra ID)    │
└──────────┬───────────────────┘
           │ token OAuth2
           ▼
┌──────────────────────────────┐
│  Azure Database for PG       │
│  az-scout-pg (:5432)         │
└──────────────────────────────┘
```

### Structure

```
api/
├── Dockerfile         # Python 3.12-slim image, PYTHONPATH=/app/src
├── main.py            # FastAPI app with lifespan (DB pool), CORS, /health
└── requirements.txt   # fastapi, uvicorn, psycopg, psycopg_pool, azure-identity
```

`api/main.py` directly imports the plugin's `router` (`az_scout_bdd_sku.routes`) — the same endpoints, without going through az-scout.

### Available Endpoints

| Category | Endpoints | Prefix |
|---|---|---|
| Infra | `/health` | — |
| Legacy (4) | `/status`, `/spot/eviction-rates`, `/spot/eviction-rates/history`, `/spot/price-history` | — |
| v1 (16) | `/v1/status`, `/v1/locations`, `/v1/skus`, `/v1/currencies`, `/v1/os-types`, `/v1/retail/prices`, `/v1/retail/prices/latest`, `/v1/spot/prices`, `/v1/spot/eviction-rates`, `/v1/spot/eviction-rates/series`, `/v1/spot/eviction-rates/latest`, `/v1/pricing/categories`, `/v1/pricing/summary`, `/v1/pricing/summary/latest`, `/v1/pricing/summary/series`, `/v1/pricing/summary/cheapest` | — |

> **Note:** In standalone mode, routes are mounted at the root (not under `/plugins/bdd-sku/`).

### Build & Deployment

```bash
# Build the image in ACR (from the repo root)
az acr build --registry azscoutacr --image bdd-sku-api:latest \
  --platform linux/amd64 --file api/Dockerfile .

# The Container App automatically pulls the latest image on restart
# To force an update:
az containerapp update --name azscout-api --resource-group rg-azure-scout-bdd \
  --image azscoutacr.azurecr.io/bdd-sku-api:latest
```

### Configuration (environment variables)

| Variable | Value | Description |
|---|---|---|
| `POSTGRES_HOST` | PG server FQDN | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `azscout` | Database name |
| `POSTGRES_USER` | MSI name | PG user (= managed identity name) |
| `POSTGRES_SSLMODE` | `require` | SSL mode |
| `POSTGRES_AUTH_METHOD` | `msi` | Authentication mode (`password` or `msi`) |
| `AZURE_CLIENT_ID` | MSI client ID | For `DefaultAzureCredential` (user-assigned identity) |
| `PYTHONUNBUFFERED` | `1` | Real-time logs |

### Auto-scaling

The Container App is configured with:
- **Min replicas**: 1 (always available)
- **Max replicas**: 10
- **HTTP rule**: scale-out at 50 concurrent requests per replica

---

## Authentification Managed Identity (MSI)

The standalone API and ingestion jobs use a **Managed Identity (User-Assigned)** to connect to PostgreSQL without a password.

### How It Works

```
Container App        DefaultAzureCredential        PostgreSQL
    │                        │                         │
    │  get_token(scope)      │                         │
    │ ─────────────────────▸ │                         │
    │                        │  OAuth2 token           │
    │ ◂───────────────────── │                         │
    │                                                  │
    │  CONNECT user=<msi-name> password=<token>        │
    │ ────────────────────────────────────────────────▸ │
    │                                                  │  Entra ID
    │                                   token verify ◂─┤─── ✓
    │  Connection established                          │
    │ ◂──────────────────────────────────────────────── │
```

1. `DefaultAzureCredential` acquires an OAuth2 token with scope `https://ossrdbms-aad.database.windows.net/.default`
2. The token is passed as the **password** in the PostgreSQL connection
3. PostgreSQL validates the token via Entra ID
4. The PG user is the **Managed Identity name** (not a traditional login)

### Azure-side Configuration

#### 1. Enable Entra ID authentication on PostgreSQL

```bash
az postgres flexible-server update \
  --name az-scout-pg \
  --resource-group rg-azure-scout-bdd \
  --microsoft-entra-auth Enabled \
  --password-auth Enabled
```

#### 2. Add the MSI as an Entra administrator

```bash
az postgres flexible-server microsoft-entra-admin create \
  --server-name az-scout-pg \
  --resource-group rg-azure-scout-bdd \
  --object-id <principal-id-de-la-msi> \
  --display-name <nom-de-la-msi> \
  --type ServicePrincipal
```

#### 3. Configure the Container App

```bash
az containerapp update \
  --name azscout-api \
  --resource-group rg-azure-scout-bdd \
  --set-env-vars \
    POSTGRES_AUTH_METHOD=msi \
    AZURE_CLIENT_ID=<client-id-de-la-msi> \
    POSTGRES_USER=<nom-de-la-msi> \
  --remove-env-vars POSTGRES_PASSWORD
```

### Code-side Configuration

The `plugin_config.py` file supports two authentication modes:

- **`password`** (default): classic DSN `postgresql://user:pass@host/db`
- **`msi`**: passwordless DSN (`host=... user=...`), the token is provided dynamically

The `db.py` file acquires a fresh token via `DefaultAzureCredential` on each pool creation:

```python
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential(managed_identity_client_id="<client-id>")
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")
# token.token is passed as password to psycopg
```

### Terraform (IaC)

The MSI configuration is managed in `infra/`:

- **`main.tf`**: Enables `active_directory_auth_enabled` on the PG server and declares the MSI as `azurerm_postgresql_flexible_server_active_directory_administrator`
- **`container-apps.tf`**: The API Container App uses `POSTGRES_AUTH_METHOD=msi` and `AZURE_CLIENT_ID` instead of `POSTGRES_PASSWORD`

> **MSI benefits:** No password to manage, automatic token rotation, audit via Entra ID, no secrets in environment variables.

---

## Plugin Integration with az-scout

### Install from PyPI (or a private registry)

```bash
uv pip install az-scout-bdd-sku
```

### Install from Git

```bash
uv pip install "az-scout-bdd-sku @ git+https://github.com/rsabile/az-scout-plugin-bdd-sku.git"
```

### Install in development mode

```bash
git clone https://github.com/rsabile/az-scout-plugin-bdd-sku.git
cd az-scout-plugin-bdd-sku
uv pip install -e ".[dev]"
```

### Via the az-scout plugin manager

In the az-scout UI: **Settings → Plugins → Install** and enter the repo's Git URL.

### Verify the plugin is loaded

On az-scout startup, the logs show:

```
INFO: Discovered plugin: bdd-sku v0.1.0
INFO: Mounted plugin routes at /plugins/bdd-sku/
INFO: Registered MCP tools: cache_status, get_spot_eviction_rates, get_spot_eviction_history, get_spot_price_history
INFO: Loaded plugin tab: SKU DB Cache
```

The plugin adds:

| Element | Description |
|---|---|
| **UI Tab** `SKU DB Cache` | Displays counts (prices, eviction, history) and last run |
| **4 API routes** | `/status`, `/spot/eviction-rates`, `/spot/eviction-rates/history`, `/spot/price-history` |
| **4 MCP tools** | `cache_status`, `get_spot_eviction_rates`, `get_spot_eviction_history`, `get_spot_price_history` |

---

## Project Structure

```
az-scout-plugin-bdd-sku/
├── pyproject.toml                  # Package config, entry point, dependencies
├── README.md
├── LICENSE.txt
├── api/
│   ├── Dockerfile                  # Python 3.12-slim image, PYTHONPATH=/app/src
│   ├── main.py                     # Standalone FastAPI (lifespan, CORS, /health)
│   └── requirements.txt            # fastapi, uvicorn, psycopg, azure-identity
├── sql/
│   └── schema.sql                  # PostgreSQL schema (6 tables)
├── postgresql/
│   └── docker-compose.yml          # Postgres 17 for local development
├── infra/
│   ├── main.tf                     # Provider + RG + PG Flexible Server + Entra admin MSI
│   ├── container-apps.tf           # ACR + Container Apps (API + 4 Jobs) + MSI
│   ├── variables.tf                # Terraform variables
│   ├── outputs.tf                  # Outputs (FQDN, resource names, etc.)
│   └── terraform.tfvars.example    # Example variables file
├── ingestion/
│   ├── Dockerfile                  # Docker image for CLI jobs
│   ├── pyproject.toml              # Ingestion dependencies (psycopg2, requests, azure-identity)
│   └── app/
│       ├── main.py                 # CLI entry point
│       ├── core/
│       │   ├── base_collector.py   # Abstract BaseCollector class
│       │   └── orchestrator.py     # Job orchestrator
│       ├── collectors/
│       │   ├── azure_pricing_collector.py  # Azure Retail Prices API collector
│       │   └── azure_spot_collector.py     # Azure Spot collector (eviction + prices)
│       └── shared/
│           ├── config.py           # Environment variable management
│           └── pg_client.py        # PostgreSQL client (sync)
├── sku-mapper-job/
│   ├── Dockerfile                  # Python 3.11-slim image, multi-stage
│   ├── pyproject.toml              # Dependencies (psycopg[binary])
│   ├── README.md                   # Dedicated documentation
│   ├── deploy/
│   │   ├── cronjob.yaml            # Kubernetes CronJob
│   │   └── container-app-job.yaml  # Azure Container Apps Job
│   ├── src/sku_mapper_job/
│   │   ├── __init__.py
│   │   ├── __main__.py             # python -m sku_mapper_job
│   │   ├── config.py               # Configuration env vars (dataclass)
│   │   ├── db.py                   # Fonctions PostgreSQL (psycopg3)
│   │   ├── main.py                 # Entry point run()
│   │   ├── mapping.py              # FAMILY_CATEGORY dict
│   │   ├── parser.py               # Regex parser SKU Azure
│   │   └── sql.py                  # DDL + SQL queries
│   └── tests/                      # 52 tests pytest
├── src/
│   └── az_scout_bdd_sku/
│       ├── __init__.py             # Classe plugin + entry point
│       ├── plugin_config.py        # Configuration (TOML, env vars, auth password/msi)
│       ├── db.py                   # Pool async (psycopg) + MSI token auth
│       ├── routes.py               # Routes FastAPI (legacy + v1, 15 endpoints)
│       ├── tools.py                # Outils MCP (4 outils)
│       └── static/
│           ├── css/bdd-sku.css
│           ├── html/bdd-sku-tab.html
│           └── js/bdd-sku-tab.js
└── tests/
    └── test_bdd_sku.py             # Tests pytest (routes + outils MCP)
```

## Database

5 PostgreSQL tables:

| Table | Description |
|---|---|
| `job_runs` | Ingestion execution tracking (status, duration, items read/written) |
| `job_logs` | Detailed logs per run |
| `retail_prices_vm` | Azure VM retail prices (25 columns, UPSERT via UNIQUE constraint) |
| `spot_eviction_rates` | Spot eviction rates per SKU×region (`UNIQUE (sku_name, region, job_id)` — one snapshot per execution) |
| `spot_price_history` | Spot price history per SKU×region×OS (timestamped JSONB price array) |
| `vm_sku_catalog` | Enriched VM SKU catalog: family, series, version, vCPU, category, workload tags (created by the SKU Mapper) |

---

## Quality checks

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Pricing values are dynamic and may change between ingestion and actual deployment.
