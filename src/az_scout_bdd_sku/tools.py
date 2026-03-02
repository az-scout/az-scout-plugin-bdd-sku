"""MCP tools for the SKU DB Cache plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from az_scout_bdd_sku.db import get_conn, is_healthy


def cache_status() -> dict[str, Any]:
    """Return the current cache status: DB health, row counts, regions, SKUs, and last runs."""
    return asyncio.run(_cache_status_async())


async def _cache_status_async() -> dict[str, Any]:
    db_ok = await is_healthy()
    count: int = -1
    eviction_count: int = -1
    price_count: int = -1
    regions_count: int = 0
    spot_skus_count: int = 0
    last_run: dict[str, Any] | None = None
    last_run_spot: dict[str, Any] | None = None

    if db_ok:
        try:
            async with get_conn() as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM retail_prices_vm")
                row = await cur.fetchone()
                count = row[0] if row else 0
        except Exception:
            count = -1

        try:
            async with get_conn() as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM spot_eviction_rates")
                row = await cur.fetchone()
                eviction_count = row[0] if row else 0
        except Exception:
            eviction_count = -1

        try:
            async with get_conn() as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM spot_price_history")
                row = await cur.fetchone()
                price_count = row[0] if row else 0
        except Exception:
            price_count = -1

        try:
            async with get_conn() as conn:
                cur = await conn.execute(
                    "SELECT COUNT(DISTINCT region), COUNT(DISTINCT sku_name) "
                    "FROM spot_eviction_rates "
                    "WHERE job_datetime = "
                    "(SELECT MAX(job_datetime) FROM spot_eviction_rates)"
                )
                row = await cur.fetchone()
                if row:
                    regions_count = row[0] or 0
                    spot_skus_count = row[1] or 0
        except Exception:
            pass

        try:
            async with get_conn() as conn:
                cur = await conn.execute(
                    """
                    SELECT run_id, status, started_at_utc, finished_at_utc,
                           items_read, items_written, error_message
                    FROM job_runs WHERE dataset = 'azure_pricing'
                    ORDER BY started_at_utc DESC LIMIT 1
                    """
                )
                lr = await cur.fetchone()
                if lr:
                    last_run = {
                        "run_id": str(lr[0]),
                        "status": lr[1],
                        "started_at_utc": lr[2].isoformat() if lr[2] else None,
                        "finished_at_utc": lr[3].isoformat() if lr[3] else None,
                        "items_read": lr[4] or 0,
                        "items_written": lr[5] or 0,
                        "error_message": lr[6],
                    }
        except Exception:
            last_run = None

        try:
            async with get_conn() as conn:
                cur = await conn.execute(
                    """
                    SELECT run_id, status, started_at_utc, finished_at_utc,
                           items_read, items_written, error_message
                    FROM job_runs WHERE dataset = 'azure_spot'
                    ORDER BY started_at_utc DESC LIMIT 1
                    """
                )
                lr = await cur.fetchone()
                if lr:
                    last_run_spot = {
                        "run_id": str(lr[0]),
                        "status": lr[1],
                        "started_at_utc": lr[2].isoformat() if lr[2] else None,
                        "finished_at_utc": lr[3].isoformat() if lr[3] else None,
                        "items_read": lr[4] or 0,
                        "items_written": lr[5] or 0,
                        "error_message": lr[6],
                    }
        except Exception:
            last_run_spot = None

    return {
        "db_connected": db_ok,
        "retail_prices_count": count,
        "spot_eviction_rates_count": eviction_count,
        "spot_price_history_count": price_count,
        "regions_count": regions_count,
        "spot_skus_count": spot_skus_count,
        "last_run": last_run,
        "last_run_spot": last_run_spot,
    }


def get_spot_eviction_rates(
    region: str = "",
    sku_name: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """Query cached spot eviction rates.

    Optionally filter by region, sku_name (substring), or job_id.
    Without job_id returns the latest snapshot only.
    """
    return asyncio.run(_spot_eviction_rates_async(region, sku_name, job_id))


async def _spot_eviction_rates_async(region: str, sku_name: str, job_id: str) -> dict[str, Any]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if region:
        clauses.append("region = %(region)s")
        params["region"] = region
    if sku_name:
        clauses.append("sku_name ILIKE %(sku_name)s")
        params["sku_name"] = f"%{sku_name}%"
    if job_id:
        clauses.append("job_id = %(job_id)s")
        params["job_id"] = job_id
    else:
        clauses.append("job_datetime = (SELECT MAX(job_datetime) FROM spot_eviction_rates)")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = 200

    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                f"SELECT sku_name, region, eviction_rate, job_id, job_datetime "
                f"FROM spot_eviction_rates{where} "
                f"ORDER BY sku_name LIMIT %(limit)s",
                params,
            )
            rows = await cur.fetchall()
            return {
                "count": len(rows),
                "items": [
                    {
                        "sku_name": r[0],
                        "region": r[1],
                        "eviction_rate": r[2],
                        "job_id": r[3],
                        "job_datetime": r[4].isoformat() if r[4] else None,
                    }
                    for r in rows
                ],
            }
    except Exception:
        return {"count": 0, "items": [], "error": "Query failed"}


def get_spot_price_history(
    region: str = "",
    sku_name: str = "",
    os_type: str = "",
) -> dict[str, Any]:
    """Query cached spot price history.

    Optionally filter by region, sku_name (substring), or os_type.
    """
    return asyncio.run(_spot_price_history_async(region, sku_name, os_type))


async def _spot_price_history_async(region: str, sku_name: str, os_type: str) -> dict[str, Any]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if region:
        clauses.append("region = %(region)s")
        params["region"] = region
    if sku_name:
        clauses.append("sku_name ILIKE %(sku_name)s")
        params["sku_name"] = f"%{sku_name}%"
    if os_type:
        clauses.append("os_type = %(os_type)s")
        params["os_type"] = os_type

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = 200

    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                f"SELECT sku_name, os_type, region, price_history "
                f"FROM spot_price_history{where} "
                f"ORDER BY sku_name LIMIT %(limit)s",
                params,
            )
            rows = await cur.fetchall()
            return {
                "count": len(rows),
                "items": [
                    {
                        "sku_name": r[0],
                        "os_type": r[1],
                        "region": r[2],
                        "price_history": r[3],
                    }
                    for r in rows
                ],
            }
    except Exception:
        return {"count": 0, "items": [], "error": "Query failed"}


def get_spot_eviction_history() -> dict[str, Any]:
    """List available eviction rate snapshots (job_id, job_datetime, row_count)."""
    return asyncio.run(_spot_eviction_history_async())


async def _spot_eviction_history_async() -> dict[str, Any]:
    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                "SELECT job_id, job_datetime, COUNT(*) AS cnt "
                "FROM spot_eviction_rates "
                "GROUP BY job_id, job_datetime "
                "ORDER BY job_datetime DESC "
                "LIMIT 50",
            )
            rows = await cur.fetchall()
            return {
                "count": len(rows),
                "snapshots": [
                    {
                        "job_id": r[0],
                        "job_datetime": r[1].isoformat() if r[1] else None,
                        "row_count": r[2],
                    }
                    for r in rows
                ],
            }
    except Exception:
        return {"count": 0, "snapshots": [], "error": "Query failed"}


# ==================================================================
# V1 MCP tools — thin wrappers over db_api
# ==================================================================


def v1_status() -> dict[str, Any]:
    """Return v1 database status: health, row counts, last job per dataset."""
    return asyncio.run(_v1_status_async())


async def _v1_status_async() -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import get_status

    return await get_status()


def v1_list_locations(
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List distinct Azure location names across all tables. Paginated (keyset cursor)."""
    return asyncio.run(_v1_list_locations_async(limit, cursor))


async def _v1_list_locations_async(limit: int, cursor: str) -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import list_locations
    from az_scout_bdd_sku.pagination import build_page, decode_cursor

    cursor_payload = decode_cursor(cursor) if cursor else None
    items = await list_locations(limit, cursor_payload)
    trimmed, page = build_page(
        items,
        limit,
        cursor_builder=lambda it: {"name": it["name"]},
    )
    return {"items": trimmed, "page": page}


def v1_list_skus(
    search: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """List distinct VM SKU names. Optional substring search. Paginated."""
    return asyncio.run(_v1_list_skus_async(search, limit, cursor))


async def _v1_list_skus_async(search: str, limit: int, cursor: str) -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import list_skus
    from az_scout_bdd_sku.pagination import build_page, decode_cursor

    cursor_payload = decode_cursor(cursor) if cursor else None
    items = await list_skus(limit, cursor_payload, search=search or None)
    trimmed, page = build_page(
        items,
        limit,
        cursor_builder=lambda it: {"skuName": it["skuName"]},
    )
    return {"items": trimmed, "page": page}


def v1_retail_prices(
    region: str = "",
    sku: str = "",
    currency: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Query retail VM prices with filters. Paginated (keyset cursor)."""
    return asyncio.run(_v1_retail_prices_async(region, sku, currency, limit, cursor))


async def _v1_retail_prices_async(
    region: str,
    sku: str,
    currency: str,
    limit: int,
    cursor: str,
) -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import list_retail_prices
    from az_scout_bdd_sku.pagination import build_page, decode_cursor

    cursor_payload = decode_cursor(cursor) if cursor else None
    items = await list_retail_prices(
        limit,
        cursor_payload,
        region=region or None,
        sku=sku or None,
        currency=currency or None,
    )

    def _cb(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "currencyCode": it["currencyCode"],
            "armRegionName": it["armRegionName"],
            "armSkuName": it["armSkuName"],
            "skuId": it["skuId"],
            "pricingType": it["pricingType"],
            "reservationTerm": it["reservationTerm"],
        }

    trimmed, page = build_page(items, limit, cursor_builder=_cb)
    return {"items": trimmed, "page": page}


def v1_eviction_rates(
    region: str = "",
    sku: str = "",
    limit: int = 1000,
    cursor: str = "",
) -> dict[str, Any]:
    """Query spot eviction rates with filters. Paginated (keyset cursor)."""
    return asyncio.run(_v1_eviction_rates_async(region, sku, limit, cursor))


async def _v1_eviction_rates_async(
    region: str,
    sku: str,
    limit: int,
    cursor: str,
) -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import list_eviction_rates
    from az_scout_bdd_sku.pagination import build_page, decode_cursor

    cursor_payload = decode_cursor(cursor) if cursor else None
    items = await list_eviction_rates(
        limit,
        cursor_payload,
        region=region or None,
        sku=sku or None,
    )

    def _cb(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "jobDatetimeUtc": it["jobDatetimeUtc"],
            "region": it["region"],
            "skuName": it["skuName"],
            "jobId": it["jobId"],
        }

    trimmed, page = build_page(items, limit, cursor_builder=_cb)
    return {"items": trimmed, "page": page}


def v1_eviction_rates_latest(
    region: str = "",
    sku: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Latest eviction rate per (region, sku_name). Not paginated."""
    return asyncio.run(_v1_eviction_rates_latest_async(region, sku, limit))


async def _v1_eviction_rates_latest_async(
    region: str,
    sku: str,
    limit: int,
) -> dict[str, Any]:
    from az_scout_bdd_sku.db_api import list_eviction_rates_latest

    items = await list_eviction_rates_latest(
        limit,
        region=region or None,
        sku=sku or None,
    )
    return {"items": items}
