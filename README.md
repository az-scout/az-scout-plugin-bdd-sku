# az-scout-plugin-bdd-sku

[![CI](https://github.com/lrivallain/az-scout-plugin-bdd-sku/actions/workflows/ci.yml/badge.svg)](https://github.com/lrivallain/az-scout-plugin-bdd-sku/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/az-scout-bdd-sku)](https://pypi.org/project/az-scout-bdd-sku/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**SKU DB Cache** plugin for [az-scout](https://github.com/lrivallain/az-scout) — adds a UI tab and 24 MCP tools for querying Azure VM pricing, spot eviction rates, and availability data. This plugin is a **lightweight HTTP client** that proxies all requests to the standalone [az-scout-bdd-api](https://github.com/lrivallain/az-scout-bdd-api).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    az-scout (core)                    │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │          az-scout-plugin-bdd-sku                │ │
│  │                                                 │ │
│  │  Plugin Routes     MCP Tools      UI Tab        │ │
│  │  /status           24 tools       SKU DB Cache  │ │
│  │  /settings         (LLM chat)     (D3 charts)   │ │
│  └────────┬────────────────────────────────────────┘ │
│           │ HTTP                                     │
└───────────┼──────────────────────────────────────────┘
            │
            ▼
┌───────────────────────┐       ┌──────────────────────────┐
│  az-scout-bdd-api     │       │  az-scout-bdd-ingestion   │
│  (REST API)           │◄──────│  (data pipeline)          │
│  Container App        │  PG   │  Ingestion + SKU Mapper   │
│  25 endpoints         │       │  + Price Aggregator       │
└───────────┬───────────┘       └────────────┬─────────────┘
            │                                │
            ▼                                ▼
       ┌──────────────────────────────────────────┐
       │           PostgreSQL 17                   │
       │           (Azure Flexible Server)         │
       └──────────────────────────────────────────┘
```

The plugin itself contains **no database code** — it calls the API over HTTP via `api_client.py`.

## Prerequisites

- [az-scout](https://github.com/lrivallain/az-scout) ≥ 2026.1
- Python 3.11+
- A running instance of [az-scout-bdd-api](https://github.com/lrivallain/az-scout-bdd-api) with ingested data from [az-scout-bdd-ingestion](https://github.com/lrivallain/az-scout-bdd-ingestion)

## Installation

```bash
# Install the plugin (automatically discovered by az-scout)
pip install az-scout-bdd-sku

# Or install in development mode
pip install -e .
```

## Configuration

The plugin needs to know the URL of the standalone API. Configure it via any of these methods (in priority order):

### 1. Environment variable

```bash
export BDD_SKU_API_URL="https://my-api.azurecontainerapps.io"
```

### 2. TOML config file

```toml
# ~/.config/az-scout/bdd-sku.toml
[api]
base_url = "https://my-api.azurecontainerapps.io"
```

Override the config file path with `AZ_SCOUT_BDD_SKU_CONFIG=/path/to/config.toml`.

### 3. Settings UI

The plugin adds a **Settings** button in the tab header. Configure the API URL directly from the az-scout web interface — the value is persisted to the TOML config file.

## MCP Tools

The plugin exposes 24 tools on the az-scout MCP server, usable by LLMs in the integrated chat. All tools proxy to the standalone API.

| Tool | Parameters | Description |
|---|---|---|
| `cache_status` | *(none)* | Database status: connectivity, count per table, last run |
| `get_spot_eviction_rates` | `region?`, `sku_name?`, `job_id?` | Spot eviction rates. Without `job_id` → latest snapshot |
| `get_spot_eviction_history` | *(none)* | Lists available eviction snapshots (last 50) |
| `get_spot_price_history` | `region?`, `sku_name?`, `os_type?` | Spot price history per SKU×region×OS |
| `v1_status` | *(none)* | v1 status: DB health, stats per dataset |
| `v1_list_locations` | `limit?`, `cursor?` | List Azure regions (paginated) |
| `v1_list_skus` | `search?`, `limit?`, `cursor?` | List VM SKUs (paginated) |
| `v1_retail_prices` | `region?`, `sku?`, `currency?`, `snapshot_date?`, `limit?`, `cursor?` | VM retail prices (paginated) |
| `v1_eviction_rates` | `region?`, `sku?`, `snapshot_date?`, `limit?`, `cursor?` | Spot eviction rates (paginated) |
| `v1_eviction_rates_latest` | `region?`, `sku?`, `snapshot_date?`, `limit?` | Latest eviction rate per (region, sku) |
| `v1_pricing_categories` | `limit?`, `cursor?` | Distinct pricing categories (paginated) |
| `v1_pricing_summary` | `region?`, `category?`, `priceType?`, `snapshotSince?`, `limit?`, `cursor?` | Aggregated price summaries (multi-value, paginated) |
| `v1_pricing_summary_latest` | `region?`, `category?`, `priceType?`, `limit?`, `cursor?` | Summaries from latest run (paginated) |
| `v1_pricing_summary_series` | `region`, `priceType`, `bucket`, `metric?`, `category?` | Time series of a price metric |
| `v1_pricing_cheapest` | `priceType`, `metric?`, `category?`, `limit?` | Top N cheapest regions |
| `v1_sku_catalog` | `search?`, `category?`, `family?`, `min_vcpus?`, `max_vcpus?`, `limit?`, `cursor?` | Full VM SKU catalog (paginated) |
| `v1_jobs` | `dataset?`, `status?`, `limit?`, `cursor?` | Ingestion job runs (paginated, most recent first) |
| `v1_job_logs` | `run_id`, `level?`, `limit?`, `cursor?` | Logs for a specific job run (paginated) |
| `v1_spot_prices_series` | `region`, `sku`, `os_type?`, `bucket?` | Spot price time series (denormalized JSONB) |
| `v1_retail_prices_compare` | `sku`, `currency?`, `pricing_type?`, `snapshot_date?` | Compare a SKU across all regions |
| `v1_spot_detail` | `region`, `sku`, `os_type?`, `snapshot_date?` | Composite Spot detail (price + eviction + catalog) |
| `v1_savings_plans` | `region?`, `sku?`, `currency?`, `snapshot_date?`, `limit?`, `cursor?` | Retail prices with savings plan data (paginated) |
| `v1_pricing_summary_compare` | `regions`, `price_type?`, `category?` | Compare pricing summaries between regions |
| `v1_stats` | *(none)* | Global dashboard metrics |

## API Reference

The full v1 API (25 endpoints) is documented in the standalone API repo:
- **Swagger UI:** Available at `/docs` on your API instance
- **OpenAPI spec:** [`openapi/v1.yaml`](openapi/v1.yaml) (reference copy)
- **API repo:** [az-scout-bdd-api](https://github.com/lrivallain/az-scout-bdd-api)

## Related Repositories

| Repository | Description |
|---|---|
| [az-scout](https://github.com/lrivallain/az-scout) | Core application — FastAPI backend, MCP server, plugin framework |
| [az-scout-bdd-api](https://github.com/lrivallain/az-scout-bdd-api) | Standalone REST API serving pricing data from PostgreSQL |
| [az-scout-bdd-ingestion](https://github.com/lrivallain/az-scout-bdd-ingestion) | Data pipeline — ingestion, SKU enrichment, price aggregation, Terraform infra |
