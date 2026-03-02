"""Plugin API routes for SKU DB Cache.

Mounted by az-scout under ``/plugins/bdd-sku/``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from az_scout_bdd_sku.db import get_conn, is_healthy

router = APIRouter()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    """Cache status: database health, row count and last ingestion run."""
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

    return {
        "db_connected": db_ok,
        "retail_prices_count": count,
        "spot_eviction_rates_count": eviction_count,
        "spot_price_history_count": price_count,
        "last_run": last_run,
    }


# ------------------------------------------------------------------
# Spot eviction rates
# ------------------------------------------------------------------


@router.get("/spot/eviction-rates")
async def spot_eviction_rates(
    region: str | None = Query(None, description="Filter by region"),
    sku_name: str | None = Query(None, description="Substring match on SKU name"),
    job_id: str | None = Query(None, description="Filter by specific job_id snapshot"),
    limit: int = Query(200, ge=1, le=5000, description="Max rows to return"),
) -> dict[str, Any]:
    """Return cached spot eviction rates with optional filters.

    Without ``job_id``, returns the latest snapshot (most recent job_datetime).
    """
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
        # Default: latest snapshot only
        clauses.append(
            "job_datetime = ("
            "SELECT MAX(job_datetime) FROM spot_eviction_rates"
            ")"
        )

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = limit

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
        logger.exception("Error querying spot eviction rates")
        return {"count": 0, "items": [], "error": "Query failed"}


# ------------------------------------------------------------------
# Spot eviction rates history (list available snapshots)
# ------------------------------------------------------------------


@router.get("/spot/eviction-rates/history")
async def spot_eviction_history(
    limit: int = Query(50, ge=1, le=500, description="Max snapshots to return"),
) -> dict[str, Any]:
    """List available eviction rate snapshots (job_id + job_datetime + row count)."""
    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                "SELECT job_id, job_datetime, COUNT(*) AS cnt "
                "FROM spot_eviction_rates "
                "GROUP BY job_id, job_datetime "
                "ORDER BY job_datetime DESC "
                "LIMIT %(limit)s",
                {"limit": limit},
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
        logger.exception("Error querying eviction rate history")
        return {"count": 0, "snapshots": [], "error": "Query failed"}


# ------------------------------------------------------------------
# Spot price history
# ------------------------------------------------------------------


@router.get("/spot/price-history")
async def spot_price_history(
    region: str | None = Query(None, description="Filter by region"),
    sku_name: str | None = Query(None, description="Substring match on SKU name"),
    os_type: str | None = Query(None, description="Filter by OS type (Linux/Windows)"),
    limit: int = Query(200, ge=1, le=5000, description="Max rows to return"),
) -> dict[str, Any]:
    """Return cached spot price history with optional filters."""
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
    params["limit"] = limit

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
        logger.exception("Error querying spot price history")
        return {"count": 0, "items": [], "error": "Query failed"}
