# SKU Mapper Job

Batch job that enriches PostgreSQL with Azure VM SKU naming conventions and categories.  
Runs once per day (after ingestion) — reads distinct SKU names from existing data tables, parses the `Standard_…` naming convention, and upserts a `vm_sku_catalog` table with family, series, version, vCPU count, category, and workload tags.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PGHOST` | yes | `localhost` | PostgreSQL host |
| `PGPORT` | no | `5432` | PostgreSQL port |
| `PGDATABASE` | no | `az_scout` | Database name |
| `PGUSER` | yes | `postgres` | Database user |
| `PGPASSWORD` | yes | *(empty)* | Database password |
| `PGSSLMODE` | no | `disable` | SSL mode (`disable` or `require`) |
| `JOB_DATASET_NAME` | no | `sku_mapper` | Dataset label in `job_runs` |
| `LOG_LEVEL` | no | `info` | Log level (`debug`, `info`, `warning`, `error`) |
| `DRY_RUN` | no | `false` | When `true`, parse SKUs but skip DB writes |
| `BATCH_SIZE` | no | `1000` | Number of rows per upsert batch |

## Run Locally

```bash
# Install dependencies
cd sku-mapper-job
uv sync

# Set environment variables
export PGHOST=localhost PGPORT=5432 PGDATABASE=az_scout
export PGUSER=postgres PGPASSWORD=mysecret PGSSLMODE=disable

# Run the job
uv run python -m sku_mapper_job.main

# Dry-run mode (no DB writes, logs 10 example rows)
DRY_RUN=true uv run python -m sku_mapper_job.main
```

## Run with Docker

```bash
# Build the image
docker build -t sku-mapper-job .

# Run the job
docker run --rm \
  -e PGHOST=host.docker.internal \
  -e PGPORT=5432 \
  -e PGDATABASE=az_scout \
  -e PGUSER=postgres \
  -e PGPASSWORD=mysecret \
  -e PGSSLMODE=disable \
  sku-mapper-job

# Dry-run
docker run --rm \
  -e PGHOST=host.docker.internal \
  -e PGPORT=5432 \
  -e PGDATABASE=az_scout \
  -e PGUSER=postgres \
  -e PGPASSWORD=mysecret \
  -e DRY_RUN=true \
  sku-mapper-job
```

## Scheduling

### Kubernetes CronJob

```bash
kubectl apply -f deploy/cronjob.yaml
```

See [deploy/cronjob.yaml](deploy/cronjob.yaml) — runs daily at 04:00 UTC.

### Azure Container Apps Job

```bash
az containerapp job create \
  --name sku-mapper-job \
  --resource-group <rg> \
  --environment <env> \
  --yaml deploy/container-app-job.yaml
```

See [deploy/container-app-job.yaml](deploy/container-app-job.yaml).

## How It Works

1. **Connect** to PostgreSQL and create `vm_sku_catalog` table if absent.
2. **Fetch** all distinct VM SKU names from `retail_prices_vm`, `spot_eviction_rates`, and `spot_price_history`.
3. **Parse** each SKU name via regex to extract: `tier`, `family`, `vcpus`, `suffix`, `version`.
4. **Map** the family to a category (`general`, `compute`, `memory`, `storage`, `gpu`, `hpc`, `burstable`, `other`).
5. **Upsert** results into `vm_sku_catalog` — idempotent, `first_seen_utc` is never overwritten.
6. **Track** the run in the existing `job_runs` table.

## Tests

```bash
uv run pytest tests/ -v
```

## Quality Checks

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```
