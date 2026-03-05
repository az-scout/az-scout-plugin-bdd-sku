"""HTTP client for the standalone BDD-SKU API.

All functions are synchronous and use ``requests`` to call the external API.
The base URL is read from ``plugin_config.get_config().api_base_url``.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from az_scout_bdd_sku.plugin_config import get_config, is_configured

logger = logging.getLogger(__name__)

_TIMEOUT = 30


class ApiNotConfiguredError(Exception):
    """Raised when the API base URL has not been set."""


def _base_url() -> str:
    """Return the configured API base URL or raise."""
    if not is_configured():
        raise ApiNotConfiguredError("BDD-SKU API URL is not configured")
    return get_config().api_base_url.rstrip("/")


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Issue a GET request and return the parsed JSON response."""
    url = f"{_base_url()}{path}"
    if params:
        # Drop empty/None values
        params = {k: v for k, v in params.items() if v is not None and v != ""}
    resp = requests.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------
# Legacy endpoints
# ------------------------------------------------------------------


def get_status() -> dict[str, Any]:
    """GET /status — cache status, row counts, last runs."""
    return _get("/status")  # type: ignore[no-any-return]


def get_spot_eviction_rates(
    region: str = "",
    sku_name: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """GET /spot/eviction-rates — spot eviction rates with optional filters."""
    return _get(  # type: ignore[no-any-return]
        "/spot/eviction-rates",
        {"region": region, "sku_name": sku_name, "job_id": job_id},
    )


def get_spot_price_history(
    region: str = "",
    sku_name: str = "",
    os_type: str = "",
) -> dict[str, Any]:
    """GET /spot/price-history — spot price history with optional filters."""
    return _get(  # type: ignore[no-any-return]
        "/spot/price-history",
        {"region": region, "sku_name": sku_name, "os_type": os_type},
    )


def get_spot_eviction_history() -> dict[str, Any]:
    """GET /spot/eviction-rates/history — available eviction rate snapshots."""
    return _get("/spot/eviction-rates/history")  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# V1 endpoints
# ------------------------------------------------------------------


def v1_status() -> dict[str, Any]:
    """GET /v1/status — database status."""
    return _get("/v1/status")  # type: ignore[no-any-return]


def v1_list_locations(
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/locations — list distinct locations, paginated."""
    return _get("/v1/locations", {"limit": limit, "cursor": cursor})  # type: ignore[no-any-return]


def v1_list_skus(
    search: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/skus — list VM SKU names, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/skus",
        {"search": search, "limit": limit, "cursor": cursor},
    )


def v1_retail_prices(
    region: str = "",
    sku: str = "",
    currency: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/retail/prices — retail VM prices, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/retail/prices",
        {
            "region": region,
            "sku": sku,
            "currency": currency,
            "snapshotDate": snapshot_date,
            "limit": limit,
            "cursor": cursor,
        },
    )


def v1_eviction_rates(
    region: str = "",
    sku: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/spot/eviction-rates — spot eviction rates, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/spot/eviction-rates",
        {
            "region": region,
            "sku": sku,
            "snapshotDate": snapshot_date,
            "limit": limit,
            "cursor": cursor,
        },
    )


def v1_eviction_rates_latest(
    region: str = "",
    sku: str = "",
    snapshot_date: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """GET /v1/spot/eviction-rates/latest — latest eviction rate per (region, sku)."""
    return _get(  # type: ignore[no-any-return]
        "/v1/spot/eviction-rates/latest",
        {"region": region, "sku": sku, "snapshotDate": snapshot_date, "limit": limit},
    )


def test_connection(url: str) -> dict[str, Any]:
    """Test connectivity to *url* by hitting /health. Returns status dict."""
    try:
        resp = requests.get(f"{url.rstrip('/')}/health", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {"ok": True, "status": data.get("status", "unknown")}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


# ------------------------------------------------------------------
# V1 pricing endpoints
# ------------------------------------------------------------------


def v1_pricing_categories(
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/pricing/categories — distinct pricing categories, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/pricing/categories",
        {"limit": limit, "cursor": cursor},
    )


def v1_pricing_summary(
    region: str = "",
    category: str = "",
    price_type: str = "",
    snapshot_since: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/pricing/summary — price summary rows, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/pricing/summary",
        {
            "region": region,
            "category": category,
            "priceType": price_type,
            "snapshotSince": snapshot_since,
            "limit": limit,
            "cursor": cursor,
        },
    )


def v1_pricing_summary_latest(
    region: str = "",
    category: str = "",
    price_type: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/pricing/summary/latest — latest run price summary, paginated."""
    return _get(  # type: ignore[no-any-return]
        "/v1/pricing/summary/latest",
        {
            "region": region,
            "category": category,
            "priceType": price_type,
            "limit": limit,
            "cursor": cursor,
        },
    )


def v1_pricing_summary_series(
    region: str,
    price_type: str,
    bucket: str,
    metric: str = "median",
    category: str = "",
) -> dict[str, Any]:
    """GET /v1/pricing/summary/series — time-bucketed pricing metric."""
    return _get(  # type: ignore[no-any-return]
        "/v1/pricing/summary/series",
        {
            "region": region,
            "priceType": price_type,
            "bucket": bucket,
            "metric": metric,
            "category": category,
        },
    )


def v1_pricing_cheapest(
    price_type: str = "retail",
    metric: str = "median",
    category: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """GET /v1/pricing/summary/cheapest — N cheapest regions."""
    return _get(  # type: ignore[no-any-return]
        "/v1/pricing/summary/cheapest",
        {
            "priceType": price_type,
            "metric": metric,
            "category": category,
            "limit": limit,
        },
    )


# ------------------------------------------------------------------
# SKU catalog
# ------------------------------------------------------------------


def v1_sku_catalog(
    *,
    search: str = "",
    category: str = "",
    family: str = "",
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/skus/catalog — paginated VM SKU catalog."""
    params: dict[str, Any] = {"limit": limit}
    if search:
        params["search"] = search
    if category:
        params["category"] = category
    if family:
        params["family"] = family
    if min_vcpus is not None:
        params["minVcpus"] = min_vcpus
    if max_vcpus is not None:
        params["maxVcpus"] = max_vcpus
    if cursor:
        params["cursor"] = cursor
    return _get("/v1/skus/catalog", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Jobs
# ------------------------------------------------------------------


def v1_jobs(
    *,
    dataset: str = "",
    status: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/jobs — paginated job runs (newest first)."""
    params: dict[str, Any] = {"limit": limit}
    if dataset:
        params["dataset"] = dataset
    if status:
        params["status"] = status
    if cursor:
        params["cursor"] = cursor
    return _get("/v1/jobs", params)  # type: ignore[no-any-return]


def v1_job_logs(
    run_id: str,
    *,
    level: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/jobs/{run_id}/logs — paginated job logs (newest first)."""
    params: dict[str, Any] = {"limit": limit}
    if level:
        params["level"] = level
    if cursor:
        params["cursor"] = cursor
    return _get(f"/v1/jobs/{run_id}/logs", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Spot price series
# ------------------------------------------------------------------


def v1_spot_prices_series(
    region: str,
    sku: str,
    *,
    os_type: str = "",
    bucket: str = "day",
) -> dict[str, Any]:
    """GET /v1/spot/prices/series — spot price JSONB time series."""
    params: dict[str, Any] = {"region": region, "sku": sku, "bucket": bucket}
    if os_type:
        params["osType"] = os_type
    return _get("/v1/spot/prices/series", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Retail prices compare
# ------------------------------------------------------------------


def v1_retail_prices_compare(
    sku: str,
    *,
    currency: str = "",
    pricing_type: str = "",
    snapshot_date: str = "",
) -> dict[str, Any]:
    """GET /v1/retail/prices/compare — compare a SKU across all regions."""
    params: dict[str, Any] = {"sku": sku}
    if currency:
        params["currency"] = currency
    if pricing_type:
        params["pricingType"] = pricing_type
    if snapshot_date:
        params["snapshotDate"] = snapshot_date
    return _get("/v1/retail/prices/compare", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Spot detail (composite)
# ------------------------------------------------------------------


def v1_spot_detail(
    region: str,
    sku: str,
    *,
    os_type: str = "",
    snapshot_date: str = "",
) -> dict[str, Any]:
    """GET /v1/spot/detail — composite spot detail."""
    params: dict[str, Any] = {"region": region, "sku": sku}
    if os_type:
        params["osType"] = os_type
    if snapshot_date:
        params["snapshotDate"] = snapshot_date
    return _get("/v1/spot/detail", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Savings plans
# ------------------------------------------------------------------


def v1_savings_plans(
    *,
    region: str = "",
    sku: str = "",
    currency: str = "",
    snapshot_date: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """GET /v1/retail/savings-plans — paginated savings plan data."""
    params: dict[str, Any] = {"limit": limit}
    if region:
        params["region"] = region
    if sku:
        params["sku"] = sku
    if currency:
        params["currency"] = currency
    if snapshot_date:
        params["snapshotDate"] = snapshot_date
    if cursor:
        params["cursor"] = cursor
    return _get("/v1/retail/savings-plans", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Pricing summary compare
# ------------------------------------------------------------------


def v1_pricing_summary_compare(
    regions: list[str],
    *,
    price_type: str = "",
    category: str = "",
) -> dict[str, Any]:
    """GET /v1/pricing/summary/compare — compare pricing across regions."""
    params: dict[str, Any] = {"regions": regions}
    if price_type:
        params["priceType"] = price_type
    if category:
        params["category"] = category
    return _get("/v1/pricing/summary/compare", params)  # type: ignore[no-any-return]


# ------------------------------------------------------------------
# Global stats
# ------------------------------------------------------------------


def v1_stats() -> dict[str, Any]:
    """GET /v1/stats — global dashboard metrics."""
    return _get("/v1/stats", {})  # type: ignore[no-any-return]
