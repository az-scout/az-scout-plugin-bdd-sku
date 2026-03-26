"""MCP tools for the SKU DB Cache plugin.

All tools call the standalone BDD-SKU API via ``api_client``.
The API base URL must be configured in the plugin settings.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from az_scout_bdd_sku.api_client import ApiNotConfiguredError
from az_scout_bdd_sku.api_client import get_spot_eviction_history as _api_eviction_history
from az_scout_bdd_sku.api_client import get_spot_eviction_rates as _api_eviction_rates
from az_scout_bdd_sku.api_client import get_spot_price_history as _api_price_history
from az_scout_bdd_sku.api_client import get_status as _api_status
from az_scout_bdd_sku.api_client import v1_eviction_rates as _api_v1_eviction_rates
from az_scout_bdd_sku.api_client import v1_eviction_rates_latest as _api_v1_eviction_rates_latest
from az_scout_bdd_sku.api_client import (
    v1_job_logs as _api_v1_job_logs,
)
from az_scout_bdd_sku.api_client import (
    v1_jobs as _api_v1_jobs,
)
from az_scout_bdd_sku.api_client import v1_list_locations as _api_v1_list_locations
from az_scout_bdd_sku.api_client import v1_list_skus as _api_v1_list_skus
from az_scout_bdd_sku.api_client import v1_pricing_categories as _api_v1_pricing_categories
from az_scout_bdd_sku.api_client import v1_pricing_cheapest as _api_v1_pricing_cheapest
from az_scout_bdd_sku.api_client import v1_pricing_summary as _api_v1_pricing_summary
from az_scout_bdd_sku.api_client import (
    v1_pricing_summary_compare as _api_v1_pricing_summary_compare,
)
from az_scout_bdd_sku.api_client import v1_pricing_summary_latest as _api_v1_pricing_summary_latest
from az_scout_bdd_sku.api_client import v1_pricing_summary_series as _api_v1_pricing_summary_series
from az_scout_bdd_sku.api_client import v1_retail_prices as _api_v1_retail_prices
from az_scout_bdd_sku.api_client import v1_retail_prices_compare as _api_v1_retail_prices_compare
from az_scout_bdd_sku.api_client import v1_savings_plans as _api_v1_savings_plans
from az_scout_bdd_sku.api_client import v1_sku_catalog as _api_v1_sku_catalog
from az_scout_bdd_sku.api_client import v1_spot_detail as _api_v1_spot_detail
from az_scout_bdd_sku.api_client import v1_spot_prices_series as _api_v1_spot_prices_series
from az_scout_bdd_sku.api_client import v1_stats as _api_v1_stats
from az_scout_bdd_sku.api_client import v1_status as _api_v1_status

_NOT_CONFIGURED = {"error": "BDD-SKU API URL is not configured. Set it in the plugin settings."}


async def _safe_call(
    fn: Callable[..., Awaitable[dict[str, Any]]], *args: Any, **kwargs: Any
) -> dict[str, Any]:
    """Call async *fn* and catch ApiNotConfiguredError + request errors."""
    try:
        return await fn(*args, **kwargs)
    except ApiNotConfiguredError:
        return _NOT_CONFIGURED
    except Exception as exc:
        return {"error": f"API call failed: {exc}"}


async def cache_status() -> dict[str, Any]:
    """Return the current cache status: DB health, row counts, regions, SKUs, and last runs."""
    return await _safe_call(_api_status)


async def get_spot_eviction_rates(
    region: str = "",
    sku_name: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """Query cached spot eviction rates.

    Optionally filter by region, sku_name (substring), or job_id.
    Without job_id returns the latest snapshot only.
    """
    return await _safe_call(_api_eviction_rates, region, sku_name, job_id)


async def get_spot_price_history(
    region: str = "",
    sku_name: str = "",
    os_type: str = "",
) -> dict[str, Any]:
    """Query cached spot price history.

    Optionally filter by region, sku_name (substring), or os_type.
    """
    return await _safe_call(_api_price_history, region, sku_name, os_type)


async def get_spot_eviction_history() -> dict[str, Any]:
    """List available eviction rate snapshots (job_id, job_datetime, row_count)."""
    return await _safe_call(_api_eviction_history)


# ==================================================================
# V1 MCP tools
# ==================================================================


async def v1_status() -> dict[str, Any]:
    """Return v1 database status: health, row counts, last job per dataset."""
    return await _safe_call(_api_v1_status)


async def v1_list_locations(
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List distinct Azure location names across all tables. Paginated (keyset cursor)."""
    return await _safe_call(_api_v1_list_locations, limit, cursor)


async def v1_list_skus(
    search: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List distinct VM SKU names. Optional substring search. Paginated."""
    return await _safe_call(_api_v1_list_skus, search, limit, cursor)


async def v1_retail_prices(
    region: str = "",
    sku: str = "",
    currency: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Query retail VM prices with filters. Paginated (keyset cursor).

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_retail_prices,
        region,
        sku,
        currency,
        snapshot_date,
        limit,
        cursor,
    )


async def v1_eviction_rates(
    region: str = "",
    sku: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Query spot eviction rates with filters. Paginated (keyset cursor).

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_eviction_rates,
        region,
        sku,
        snapshot_date,
        limit,
        cursor,
    )


async def v1_eviction_rates_latest(
    region: str = "",
    sku: str = "",
    snapshot_date: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Latest eviction rate per (region, sku_name).

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_eviction_rates_latest,
        region,
        sku,
        snapshot_date,
        limit,
    )


# ==================================================================
# V1 Pricing MCP tools
# ==================================================================


async def v1_pricing_categories(
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List distinct pricing categories from pre-aggregated price summaries. Paginated."""
    return await _safe_call(_api_v1_pricing_categories, limit, cursor)


async def v1_pricing_summary(
    region: str = "",
    category: str = "",
    price_type: str = "",
    snapshot_since: str = "",
    limit: int = 1000,
    cursor: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Query pre-aggregated price summaries with filters. Paginated (keyset cursor).

    Filters: region, category, price_type (retail/spot), currency (e.g. USD, EUR),
    snapshot_since (ISO datetime).
    Returns avg, median, min, max, and percentile prices per region/category.
    """
    return await _safe_call(
        _api_v1_pricing_summary,
        region,
        category,
        price_type,
        snapshot_since,
        limit,
        cursor,
        currency,
    )


async def v1_pricing_summary_latest(
    region: str = "",
    category: str = "",
    price_type: str = "",
    limit: int = 1000,
    cursor: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Latest price summary snapshot (most recent aggregation run). Paginated."""
    return await _safe_call(
        _api_v1_pricing_summary_latest,
        region,
        category,
        price_type,
        limit,
        cursor,
        currency,
    )


async def v1_pricing_summary_series(
    region: str,
    price_type: str,
    bucket: str,
    metric: str = "median",
    category: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Time-bucketed pricing metric evolution over aggregation runs.

    bucket: day|week|month.  metric: avg|median|min|max|p10|p25|p75|p90.
    """
    return await _safe_call(
        _api_v1_pricing_summary_series,
        region,
        price_type,
        bucket,
        metric,
        category,
        currency,
    )


async def v1_pricing_cheapest(
    price_type: str = "retail",
    metric: str = "median",
    category: str = "",
    limit: int = 10,
    currency: str = "",
) -> dict[str, Any]:
    """Top N cheapest Azure regions from latest run, ranked by a pricing metric."""
    return await _safe_call(_api_v1_pricing_cheapest, price_type, metric, category, limit, currency)


# ==================================================================
# V1 SKU Catalog MCP tools
# ==================================================================


async def v1_sku_catalog(
    search: str = "",
    category: str = "",
    family: str = "",
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Browse the full VM SKU catalog. Filter by search, category, family, vCPUs. Paginated."""
    return await _safe_call(
        _api_v1_sku_catalog,
        search=search,
        category=category,
        family=family,
        min_vcpus=min_vcpus,
        max_vcpus=max_vcpus,
        limit=limit,
        cursor=cursor,
    )


# ==================================================================
# V1 Job & Logs MCP tools
# ==================================================================


async def v1_jobs(
    dataset: str = "",
    status: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List ingestion job runs. Filter by dataset and status. Paginated (newest first)."""
    return await _safe_call(
        _api_v1_jobs, dataset=dataset, status=status, limit=limit, cursor=cursor
    )


async def v1_job_logs(
    run_id: str,
    level: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List log entries for a specific job run. Filter by level. Paginated (newest first)."""
    return await _safe_call(_api_v1_job_logs, run_id, level=level, limit=limit, cursor=cursor)


# ==================================================================
# V1 Spot series & detail MCP tools
# ==================================================================


async def v1_spot_prices_series(
    region: str,
    sku: str,
    os_type: str = "",
    bucket: str = "day",
) -> dict[str, Any]:
    """Spot price time series from JSONB history, bucketed by day/week/month."""
    return await _safe_call(_api_v1_spot_prices_series, region, sku, os_type=os_type, bucket=bucket)


async def v1_spot_detail(
    region: str,
    sku: str,
    os_type: str = "",
    snapshot_date: str = "",
) -> dict[str, Any]:
    """Composite spot detail: latest price, eviction rate, and SKU catalog entry.

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_spot_detail,
        region,
        sku,
        os_type=os_type,
        snapshot_date=snapshot_date,
    )


# ==================================================================
# V1 Retail compare & savings MCP tools
# ==================================================================


async def v1_retail_prices_compare(
    sku: str,
    currency: str = "",
    pricing_type: str = "",
    snapshot_date: str = "",
) -> dict[str, Any]:
    """Compare a SKU's retail price across all regions, grouped by region.

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_retail_prices_compare,
        sku,
        currency=currency,
        pricing_type=pricing_type,
        snapshot_date=snapshot_date,
    )


async def v1_savings_plans(
    region: str = "",
    sku: str = "",
    currency: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Browse retail prices that include savings plan data. Paginated (keyset cursor).

    snapshot_date: ISO datetime — scope to the latest snapshot <= this value.
    Defaults to the most recent snapshot when omitted.
    """
    return await _safe_call(
        _api_v1_savings_plans,
        region=region,
        sku=sku,
        currency=currency,
        snapshot_date=snapshot_date,
        limit=limit,
        cursor=cursor,
    )


# ==================================================================
# V1 Pricing compare & stats MCP tools
# ==================================================================


async def v1_pricing_summary_compare(
    regions: list[str],
    price_type: str = "",
    category: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Compare pricing summaries across multiple regions from the latest run."""
    return await _safe_call(
        _api_v1_pricing_summary_compare,
        regions,
        price_type=price_type,
        category=category,
        currency=currency,
    )


async def v1_stats() -> dict[str, Any]:
    """Global dashboard stats: table row counts, distinct regions/SKUs, data freshness."""
    return await _safe_call(_api_v1_stats)
