"""MCP tools for the SKU DB Cache plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from az_scout_bdd_sku.db import get_conn, is_healthy


def cache_status() -> dict[str, Any]:
    """Return the current cache status: DB health, row count and last ingestion run."""
    return asyncio.run(_cache_status_async())


async def _cache_status_async() -> dict[str, Any]:
    db_ok = await is_healthy()
    count: int = -1
    eviction_count: int = -1
    price_count: int = -1
    last_run: dict[str, Any] | None = None

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
                    """
                    SELECT run_id, status, started_at_utc, finished_at_utc,
                           items_read, items_written, error_message
                    FROM job_runs WHERE dataset IN ('azure_pricing', 'azure_spot')
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

    return {
        "db_connected": db_ok,
        "retail_prices_count": count,
        "spot_eviction_rates_count": eviction_count,
        "spot_price_history_count": price_count,
        "last_run": last_run,
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


async def _spot_eviction_rates_async(
    region: str, sku_name: str, job_id: str
) -> dict[str, Any]:
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
        clauses.append(
            "job_datetime = (SELECT MAX(job_datetime) FROM spot_eviction_rates)"
        )

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


async def _spot_price_history_async(
    region: str, sku_name: str, os_type: str
) -> dict[str, Any]:
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
