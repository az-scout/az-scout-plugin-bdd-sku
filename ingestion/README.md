# Ingestion CLI

One-shot CLI job that collects **Azure VM retail prices** from the [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices) (public, no authentication required) and inserts them into PostgreSQL.

## Architecture

```
Azure Retail Prices API         Ingestion CLI              PostgreSQL (:5432)
  (public, no auth)              (one-shot)
┌───────────────────┐  GET     ┌──────────────────┐ INSERT ┌────────────────┐
│ prices.azure.com  │ ◂─────  │  python main.py  │ ─────▸ │ retail_prices  │
│ /api/retail/      │  OData   │                  │        │ job_runs       │
│   prices          │  $filter │ AzurePricingColl │        │ job_logs       │
└───────────────────┘          └──────────────────┘        └────────────────┘
```

## Prerequisites

- **Docker** and **Docker Compose** installed ([install Docker](https://docs.docker.com/get-docker/))
- No Azure account required — the pricing API is public and free
- ~500 MB of disk space for the Docker image and PostgreSQL data

Verify that Docker is working:

```bash
docker --version          # Docker version 24+ expected
docker compose version    # Docker Compose version v2+ expected
```

## Step-by-step Guide

### Step 1 — Start PostgreSQL

PostgreSQL receives the collected data. A `docker-compose.yml` file is provided in the `postgresql/` folder to start it with one click.

```bash
# From the root of the az-scout-plugin-bdd-sku project
cd postgresql/
docker compose up -d
```

This automatically creates:
- A PostgreSQL 17 container on port **5432**
- A database `azscout` (user: `azscout`, password: `azscout`)
- The 3 required tables (`retail_prices_vm`, `job_runs`, `job_logs`) via `sql/schema.sql`
- A Docker network named `postgresql_default` (used later by the ingestion)

**Verify that PostgreSQL is ready:**

```bash
docker compose ps
```

You should see:

```
NAME       IMAGE         STATUS
postgres   postgres:17   Up X seconds (healthy)
```

> **Troubleshooting:** If the status is `(health: starting)`, wait a few seconds and re-run `docker compose ps`. If port 5432 is already in use, stop the other service or modify the port in `docker-compose.yml`.

### Step 2 — Build the ingestion image

```bash
# Go back to the project root then into ingestion/
cd ../ingestion/
docker build -t bdd-sku-ingestion .
```

The build installs Python 3.12, the dependencies (`requests`, `psycopg2`), and copies the code. It takes about 30 seconds the first time.

### Step 3 — Run the ingestion

```bash
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  bdd-sku-ingestion
```

**Parameter explanation:**

| Parameter | Purpose |
|---|---|
| `--rm` | Removes the container after execution (it's a one-shot job) |
| `--network postgresql_default` | Connects the container to the same network as PostgreSQL |
| `-e POSTGRES_HOST=postgres` | Specifies the PostgreSQL address (`postgres` is the service name in docker-compose) |
| `-e ENABLE_AZURE_PRICING_COLLECTOR=true` | Enables collection (disabled by default for safety) |

The job starts, displays progress logs (pages collected, items inserted), then terminates automatically.

> **Note:** Without filters, the ingestion collects **all** Azure prices (several hundred thousand items). Use `AZURE_PRICING_MAX_ITEMS` to limit during testing — see the [Examples](#examples) section below.

### Step 4 — Verify the data

```bash
# Connect to PostgreSQL
docker exec -it postgresql-postgres-1 psql -U azscout -d azscout

# Count collected prices
SELECT COUNT(*) FROM retail_prices_vm;

# View the last ingestion run
SELECT run_id, status, items_written, started_at_utc FROM job_runs ORDER BY started_at_utc DESC LIMIT 1;

# Exit psql
\q
```

### Step 5 — Stop the environment

When you're done:

```bash
cd ../postgresql/
docker compose down        # Stops PostgreSQL (data is preserved in the volume)
docker compose down -v     # Stops AND deletes PostgreSQL data
```

## Alternative: without Docker

If you prefer not to use Docker for ingestion (PostgreSQL is still required):

```bash
cd ingestion/
pip install -e .

POSTGRES_HOST=localhost \
POSTGRES_PORT=5432 \
POSTGRES_DB=azscout \
POSTGRES_USER=azscout \
POSTGRES_PASSWORD=azscout \
ENABLE_AZURE_PRICING_COLLECTOR=true \
  python app/main.py
```

> Here `POSTGRES_HOST=localhost` because the script runs directly on the host machine, not inside a Docker container.

## Examples

### Quick test (1000 items only)

```bash
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  -e AZURE_PRICING_MAX_ITEMS=1000 \
  bdd-sku-ingestion
```

### Collect VM prices only

```bash
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  -e 'AZURE_PRICING_FILTERS={"serviceName": "Virtual Machines"}' \
  bdd-sku-ingestion
```

### Verbose mode (debug)

```bash
docker run --rm \
  --network postgresql_default \
  -e POSTGRES_HOST=postgres \
  -e ENABLE_AZURE_PRICING_COLLECTOR=true \
  -e AZURE_PRICING_MAX_ITEMS=100 \
  -e LOG_LEVEL=DEBUG \
  bdd-sku-ingestion
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | PostgreSQL server address. Use `postgres` if the container is on the Docker network, `localhost` if the script runs locally |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `azscout` | Database name |
| `POSTGRES_USER` | `azscout` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `azscout` | PostgreSQL password |
| `POSTGRES_SSLMODE` | `disable` | SSL mode (`disable` locally, `require` on Azure) |
| `ENABLE_AZURE_PRICING_COLLECTOR` | `false` | **Must be `true`** to start collection. Safety measure to prevent accidental runs |
| `AZURE_PRICING_MAX_ITEMS` | `-1` | Maximum number of items to collect. `-1` = unlimited. Use `1000` for testing |
| `AZURE_PRICING_FILTERS` | `{}` | OData filters as JSON. Example: `{"serviceName": "Virtual Machines"}` |
| `AZURE_PRICING_API_RETRY_ATTEMPTS` | `3` | Number of retries on API error (429, 5xx) |
| `AZURE_PRICING_API_RETRY_DELAY` | `2.0` | Delay in seconds between retries |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `JOB_TYPE` | `manual` | Job metadata (free-form description) |

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `network postgresql_default not found` | PostgreSQL is not running | Run `cd postgresql/ && docker compose up -d` first |
| `connection refused` on port 5432 | PostgreSQL not ready yet | Wait a few seconds, check `docker compose ps` |
| `POSTGRES_HOST=postgres` doesn't work | The script is running outside Docker | Use `POSTGRES_HOST=localhost` instead |
| `ENABLE_AZURE_PRICING_COLLECTOR` forgotten | Collection is not enabled | Add `-e ENABLE_AZURE_PRICING_COLLECTOR=true` |
| Very slow ingestion | Collecting the entire catalog without filters | Add `-e AZURE_PRICING_MAX_ITEMS=1000` for testing |
