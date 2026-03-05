"""Plugin API routes for SKU DB Cache.

Mounted by az-scout under ``/plugins/bdd-sku/``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from az_scout_bdd_sku.db import get_conn, is_healthy

router = APIRouter()
v1_router = APIRouter(prefix="/v1", tags=["v1"])
logger = logging.getLogger(__name__)

API_VERSION = "v1"


def _meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "dataSource": "local-db",
        "generatedAt": datetime.now(UTC).isoformat(),
    }
    if extra:
        m.update(extra)
    return m


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "meta": _meta(),
    }
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(content=body, status_code=status_code)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _last_run_for(dataset: str, fallback_table: str) -> dict[str, Any] | None:
    """Return the most recent job_runs entry for *dataset*.

    If no ``job_runs`` row exists (ingestion ran before the orchestrator
    recorded runs), fall back to ``MAX(job_datetime)`` from the data table
    so the dashboard still shows meaningful "Last update" info.
    """
    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                "SELECT run_id, status, started_at_utc, finished_at_utc,"
                "       items_read, items_written, error_message "
                "FROM job_runs WHERE dataset = %s "
                "ORDER BY started_at_utc DESC LIMIT 1",
                (dataset,),
            )
            lr = await cur.fetchone()
            if lr:
                return {
                    "run_id": str(lr[0]),
                    "status": lr[1],
                    "started_at_utc": lr[2].isoformat() if lr[2] else None,
                    "finished_at_utc": lr[3].isoformat() if lr[3] else None,
                    "items_read": lr[4] or 0,
                    "items_written": lr[5] or 0,
                    "error_message": lr[6],
                }
    except Exception:
        pass

    # Fallback: derive from the data table itself
    try:
        async with get_conn() as conn:
            cur = await conn.execute(
                f"SELECT MAX(job_datetime), COUNT(*) FROM {fallback_table}"  # noqa: S608
            )
            row = await cur.fetchone()
            if row and row[0] is not None:
                return {
                    "run_id": None,
                    "status": "ok",
                    "started_at_utc": row[0].isoformat(),
                    "finished_at_utc": row[0].isoformat(),
                    "items_read": row[1] or 0,
                    "items_written": row[1] or 0,
                    "error_message": None,
                }
    except Exception:
        pass

    return None


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    """Cache status: database health, row counts, regions, SKUs, and last runs."""
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

        last_run = await _last_run_for("azure_pricing", "retail_prices_vm")
        last_run_spot = await _last_run_for("azure_spot", "spot_eviction_rates")

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
        clauses.append("job_datetime = (SELECT MAX(job_datetime) FROM spot_eviction_rates)")

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


# ==================================================================
# V1 API — Keyset-paginated read-only endpoints
# ==================================================================


@v1_router.get("/status")
async def v1_status() -> JSONResponse:
    """Database health and per-dataset statistics (v1)."""
    from az_scout_bdd_sku.db_api import get_status

    try:
        data = await get_status()
    except Exception:
        logger.exception("v1/status error")
        return _error_response(500, "INTERNAL", "Failed to query database status")
    body: dict[str, Any] = {
        **data,
        "version": {"apiVersion": API_VERSION},
        "meta": _meta(),
    }
    return JSONResponse(content=body)


@v1_router.get("/locations")
async def v1_locations(
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Distinct location names across all tables (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_locations
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_locations(lim, cursor_payload)
    except Exception:
        logger.exception("v1/locations error")
        return _error_response(500, "INTERNAL", "Query failed")

    trimmed, page = build_page(
        items,
        lim,
        cursor_builder=lambda it: {"name": it["name"]},
    )
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/skus")
async def v1_skus(
    search: str | None = Query(None, description="Substring filter (case-insensitive)"),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Distinct SKU names across all tables (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_skus
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_skus(lim, cursor_payload, search=search)
    except Exception:
        logger.exception("v1/skus error")
        return _error_response(500, "INTERNAL", "Query failed")

    trimmed, page = build_page(
        items,
        lim,
        cursor_builder=lambda it: {"skuName": it["skuName"]},
    )
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/currencies")
async def v1_currencies(
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Distinct currency codes from retail prices (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_currencies
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_currencies(lim, cursor_payload)
    except Exception:
        logger.exception("v1/currencies error")
        return _error_response(500, "INTERNAL", "Query failed")

    trimmed, page = build_page(
        items,
        lim,
        cursor_builder=lambda it: {"currencyCode": it["currencyCode"]},
    )
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/os-types")
async def v1_os_types(
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Distinct OS types from spot price history (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_os_types
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_os_types(lim, cursor_payload)
    except Exception:
        logger.exception("v1/os-types error")
        return _error_response(500, "INTERNAL", "Query failed")

    trimmed, page = build_page(
        items,
        lim,
        cursor_builder=lambda it: {"osType": it["osType"]},
    )
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/retail/prices")
async def v1_retail_prices(
    region: str | None = Query(None, description="Filter by arm_region_name"),
    sku: str | None = Query(None, description="Filter by arm_sku_name"),
    currency: str | None = Query(None, description="Filter by currency_code"),
    effectiveAt: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — effective_start_date <= value",
    ),
    updatedSince: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — job_datetime >= value",
    ),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Retail VM prices with optional filters (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_retail_prices
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit)
        eff = parse_iso_dt(effectiveAt, param_name="effectiveAt")
        upd = parse_iso_dt(updatedSince, param_name="updatedSince")
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await list_retail_prices(
            lim,
            cursor_payload,
            region=region,
            sku=sku,
            currency=currency,
            effective_at=eff,
            updated_since=upd,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/retail/prices error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "currencyCode": it["currencyCode"],
            "armRegionName": it["armRegionName"],
            "armSkuName": it["armSkuName"],
            "skuId": it["skuId"],
            "pricingType": it["pricingType"],
            "reservationTerm": it["reservationTerm"],
        }

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta(snap_meta)})


@v1_router.get("/retail/prices/latest")
async def v1_retail_prices_latest(
    region: str | None = Query(None, description="Filter by arm_region_name"),
    sku: str | None = Query(None, description="Filter by arm_sku_name"),
    currency: str | None = Query(None, description="Filter by currency_code"),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Latest retail price per unique key (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_retail_prices_latest
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit)
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await list_retail_prices_latest(
            lim,
            cursor_payload,
            region=region,
            sku=sku,
            currency=currency,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/retail/prices/latest error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "currencyCode": it["currencyCode"],
            "armRegionName": it["armRegionName"],
            "skuId": it["skuId"],
            "pricingType": it["pricingType"],
            "reservationTerm": it["reservationTerm"],
        }

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta(snap_meta)})


@v1_router.get("/spot/prices")
async def v1_spot_prices(
    region: str | None = Query(None, description="Filter by region"),
    sku: str | None = Query(None, description="Filter by sku_name"),
    osType: str | None = Query(None, alias="osType", description="Filter by os_type"),  # noqa: N803
    sample: str = Query("raw", description="Sampling mode (raw|hourly|daily)"),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Spot price history (v1, paginated). Only sample=raw is implemented."""
    from az_scout_bdd_sku.db_api import list_spot_prices
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit, validate_sample

    try:
        lim = parse_limit(limit)
        sample_val = validate_sample(sample)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    if sample_val != "raw":
        return _error_response(
            501,
            "NOT_IMPLEMENTED",
            f"sample='{sample_val}' is not implemented yet, use sample=raw",
        )

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_spot_prices(
            lim,
            cursor_payload,
            region=region,
            sku=sku,
            os_type=osType,
        )
    except Exception:
        logger.exception("v1/spot/prices error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "region": it["region"],
            "skuName": it["skuName"],
            "osType": it["osType"],
        }

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/spot/eviction-rates")
async def v1_eviction_rates(
    region: str | None = Query(None, description="Filter by region"),
    sku: str | None = Query(None, description="Filter by sku_name"),
    updatedSince: str | None = Query(  # noqa: N803
        None,
        alias="updatedSince",
        description="ISO datetime — job_datetime >= value",
    ),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Spot eviction rates (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_eviction_rates
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit)
        upd = parse_iso_dt(updatedSince, param_name="updatedSince")
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await list_eviction_rates(
            lim,
            cursor_payload,
            region=region,
            sku=sku,
            updated_since=upd,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/spot/eviction-rates error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "jobDatetimeUtc": it["jobDatetimeUtc"],
            "region": it["region"],
            "skuName": it["skuName"],
            "jobId": it["jobId"],
        }

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta(snap_meta)})


@v1_router.get("/spot/eviction-rates/series")
async def v1_eviction_rates_series(
    region: str = Query(..., description="Region (required)"),
    sku: str = Query(..., description="SKU name (required)"),
    bucket: str = Query(..., description="Time bucket: hour|day|week"),
    agg: str = Query("avg", description="Aggregation: avg|min|max"),
    limit: int | None = Query(  # noqa: ARG001
        None,
        description="Ignored (no pagination for series)",
    ),
    cursor: str | None = Query(None, description="Ignored"),  # noqa: ARG001
) -> JSONResponse:
    """Time-bucketed eviction rate aggregation (v1, not paginated)."""
    from az_scout_bdd_sku.db_api import eviction_rate_series
    from az_scout_bdd_sku.validation import (
        ValidationError,
        validate_agg,
        validate_bucket,
    )

    try:
        bucket_val = validate_bucket(bucket)
        agg_val = validate_agg(agg)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await eviction_rate_series(region, sku, bucket_val, agg=agg_val)
    except Exception:
        logger.exception("v1/spot/eviction-rates/series error")
        return _error_response(500, "INTERNAL", "Query failed")

    page = {"limit": len(items), "cursor": None, "hasMore": False}
    meta = _meta({"bucket": bucket_val, "agg": agg_val})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


@v1_router.get("/spot/eviction-rates/latest")
async def v1_eviction_rates_latest(
    region: str | None = Query(None, description="Filter by region"),
    sku: str | None = Query(None, description="Filter by sku_name"),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
    limit: int | None = Query(None, description="Max rows (default 200, max 5000)"),
) -> JSONResponse:
    """Latest eviction rate per (region, sku_name) (v1, no cursor pagination)."""
    from az_scout_bdd_sku.db_api import list_eviction_rates_latest
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit, default=200)
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await list_eviction_rates_latest(
            lim,
            region=region,
            sku=sku,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/spot/eviction-rates/latest error")
        return _error_response(500, "INTERNAL", "Query failed")

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    page = {"limit": lim, "cursor": None, "hasMore": False}
    return JSONResponse(content={"items": items, "page": page, "meta": _meta(snap_meta)})


# ------------------------------------------------------------------
# Pricing summary
# ------------------------------------------------------------------


@v1_router.get("/pricing/categories")
async def v1_pricing_categories(
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Distinct category values from the price_summary table (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_pricing_categories
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_pricing_categories(lim, cursor_payload)
    except Exception:
        logger.exception("v1/pricing/categories error")
        return _error_response(500, "INTERNAL", "Query failed")

    trimmed, page = build_page(
        items,
        lim,
        cursor_builder=lambda it: {"category": it["category"] or ""},
    )
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/pricing/summary")
async def v1_pricing_summary(
    region: list[str] | None = Query(None, description="Filter by region(s)"),  # noqa: B008
    category: list[str] | None = Query(None, description="Filter by category(ies)"),  # noqa: B008
    priceType: list[str] | None = Query(  # noqa: N803, B008
        None, description="Filter by price type(s): retail, spot"
    ),
    snapshotSince: str | None = Query(  # noqa: N803
        None, description="ISO datetime — snapshot_utc >= value"
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Price summary rows with multi-value filters (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_pricing_summary
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit)
        ss = parse_iso_dt(snapshotSince, param_name="snapshotSince")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_pricing_summary(
            lim,
            cursor_payload,
            regions=region,
            categories=category,
            price_types=priceType,
            snapshot_since=ss,
        )
    except Exception:
        logger.exception("v1/pricing/summary error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "region": it["region"],
            "category": it["category"] or "",
            "priceType": it["priceType"],
            "id": it["id"],
        }

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/pricing/summary/latest")
async def v1_pricing_summary_latest(
    region: list[str] | None = Query(None, description="Filter by region(s)"),  # noqa: B008
    category: list[str] | None = Query(None, description="Filter by category(ies)"),  # noqa: B008
    priceType: list[str] | None = Query(  # noqa: N803, B008
        None, description="Filter by price type(s): retail, spot"
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Price summary rows from the latest run only (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_pricing_summary_latest
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_pricing_summary_latest(
            lim,
            cursor_payload,
            regions=region,
            categories=category,
            price_types=priceType,
        )
    except Exception:
        logger.exception("v1/pricing/summary/latest error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "region": it["region"],
            "category": it["category"] or "",
            "priceType": it["priceType"],
            "id": it["id"],
        }

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/pricing/summary/series")
async def v1_pricing_summary_series(
    region: str = Query(..., description="Region (required)"),
    priceType: str = Query(..., description="Price type: retail|spot"),  # noqa: N803
    bucket: str = Query(..., description="Time bucket: day|week|month"),
    metric: str = Query("median", description="Metric: avg|median|min|max|p10|p25|p75|p90"),
    category: str | None = Query(None, description="Category filter (omit for global)"),
) -> JSONResponse:
    """Time-bucketed pricing metric aggregation over runs (v1, not paginated)."""
    from az_scout_bdd_sku.db_api import pricing_summary_series
    from az_scout_bdd_sku.validation import (
        ValidationError,
        validate_metric,
        validate_price_type,
        validate_pricing_bucket,
    )

    try:
        bucket_val = validate_pricing_bucket(bucket)
        metric_val = validate_metric(metric)
        pt_val = validate_price_type(priceType)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await pricing_summary_series(
            region,
            pt_val,
            bucket_val,
            metric=metric_val,
            category=category,
        )
    except Exception:
        logger.exception("v1/pricing/summary/series error")
        return _error_response(500, "INTERNAL", "Query failed")

    page = {"limit": len(items), "cursor": None, "hasMore": False}
    meta = _meta({"bucket": bucket_val, "metric": metric_val, "priceType": pt_val})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


@v1_router.get("/pricing/summary/cheapest")
async def v1_pricing_summary_cheapest(
    priceType: str = Query("retail", description="Price type: retail|spot"),  # noqa: N803
    metric: str = Query("median", description="Metric to rank by (default median)"),
    category: str | None = Query(None, description="Category filter (omit for global)"),
    limit: int | None = Query(None, description="Max results (default 10, max 100)"),
) -> JSONResponse:
    """Top N cheapest regions from the latest run, ranked by metric (v1)."""
    from az_scout_bdd_sku.db_api import list_pricing_cheapest
    from az_scout_bdd_sku.validation import (
        ValidationError,
        validate_metric,
        validate_price_type,
    )

    try:
        pt_val = validate_price_type(priceType)
        metric_val = validate_metric(metric)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    lim = min(max(limit or 10, 1), 100)

    try:
        items = await list_pricing_cheapest(
            lim,
            price_type=pt_val,
            metric=metric_val,
            category=category,
        )
    except Exception:
        logger.exception("v1/pricing/summary/cheapest error")
        return _error_response(500, "INTERNAL", "Query failed")

    page = {"limit": lim, "cursor": None, "hasMore": False}
    meta = _meta({"metric": metric_val, "priceType": pt_val})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


# ------------------------------------------------------------------
# SKU catalog
# ------------------------------------------------------------------


@v1_router.get("/skus/catalog")
async def v1_sku_catalog(
    search: str | None = Query(None, description="Substring match on SKU name"),
    category: str | None = Query(None, description="Filter by category"),
    family: str | None = Query(None, description="Filter by family"),
    minVcpus: int | None = Query(  # noqa: N803
        None, description="Minimum vCPU count"
    ),
    maxVcpus: int | None = Query(  # noqa: N803
        None, description="Maximum vCPU count"
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """VM SKU catalog from vm_sku_catalog table (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_sku_catalog
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_limit

    try:
        lim = parse_limit(limit)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_sku_catalog(
            lim,
            cursor_payload,
            search=search,
            category=category,
            family=family,
            min_vcpus=minVcpus,
            max_vcpus=maxVcpus,
        )
    except Exception:
        logger.exception("v1/skus/catalog error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {"skuName": it["skuName"]}

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


# ------------------------------------------------------------------
# Jobs
# ------------------------------------------------------------------


@v1_router.get("/jobs")
async def v1_jobs(
    dataset: str | None = Query(None, description="Filter by dataset name"),
    status: str | None = Query(None, description="Filter by status: running|ok|error"),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Job runs from job_runs table (v1, paginated, newest first)."""
    from az_scout_bdd_sku.db_api import list_jobs
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import (
        ValidationError,
        parse_limit,
        validate_job_dataset,
        validate_job_status,
    )

    try:
        lim = parse_limit(limit)
        ds_val = validate_job_dataset(dataset) if dataset else None
        st_val = validate_job_status(status) if status else None
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_jobs(lim, cursor_payload, dataset=ds_val, status=st_val)
    except Exception:
        logger.exception("v1/jobs error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {"startedAtUtc": it["startedAtUtc"], "runId": it["runId"]}

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


@v1_router.get("/jobs/{run_id}/logs")
async def v1_job_logs(
    run_id: str,
    level: str | None = Query(None, description="Filter by level: info|warning|error"),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Job logs for a specific run (v1, paginated, newest first)."""
    from az_scout_bdd_sku.db_api import list_job_logs
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import (
        ValidationError,
        parse_limit,
        validate_log_level,
        validate_uuid,
    )

    try:
        validate_uuid(run_id)
        lim = parse_limit(limit)
        lvl_val = validate_log_level(level) if level else None
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await list_job_logs(run_id, lim, cursor_payload, level=lvl_val)
    except Exception:
        logger.exception("v1/jobs/%s/logs error", run_id)
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {"tsUtc": it["tsUtc"], "id": it["id"]}

    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta()})


# ------------------------------------------------------------------
# Spot price series
# ------------------------------------------------------------------


@v1_router.get("/spot/prices/series")
async def v1_spot_prices_series(
    region: str = Query(..., description="Region (required)"),
    sku: str = Query(..., description="SKU name (required)"),
    osType: str | None = Query(None, description="OS type filter"),  # noqa: N803
    bucket: str = Query("day", description="Time bucket: day|week|month"),
) -> JSONResponse:
    """Spot price history denormalised and aggregated by time bucket (v1)."""
    from az_scout_bdd_sku.db_api import spot_price_series
    from az_scout_bdd_sku.validation import (
        ValidationError,
        validate_pricing_bucket,
    )

    try:
        bucket_val = validate_pricing_bucket(bucket)
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await spot_price_series(region, sku, bucket_val, os_type=osType)
    except Exception:
        logger.exception("v1/spot/prices/series error")
        return _error_response(500, "INTERNAL", "Query failed")

    page = {"limit": len(items), "cursor": None, "hasMore": False}
    meta = _meta({"bucket": bucket_val, "region": region, "sku": sku})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


# ------------------------------------------------------------------
# Retail prices compare
# ------------------------------------------------------------------


@v1_router.get("/retail/prices/compare")
async def v1_retail_prices_compare(
    sku: str = Query(..., description="SKU name or substring (required)"),
    currency: str | None = Query(None, description="Currency code filter (e.g. USD)"),
    pricingType: str | None = Query(  # noqa: N803
        None, description="Pricing type filter (e.g. Consumption)"
    ),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
) -> JSONResponse:
    """Compare a SKU's retail price across all regions (v1)."""
    from az_scout_bdd_sku.db_api import retail_prices_compare
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt

    try:
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await retail_prices_compare(
            sku,
            currency=currency,
            pricing_type=pricingType,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/retail/prices/compare error")
        return _error_response(500, "INTERNAL", "Query failed")

    region_count = len(items)
    page = {"limit": region_count, "cursor": None, "hasMore": False}
    snap_extra = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    meta = _meta({"sku": sku, "regionCount": region_count, **snap_extra})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


# ------------------------------------------------------------------
# Spot detail (composite)
# ------------------------------------------------------------------


@v1_router.get("/spot/detail")
async def v1_spot_detail(
    region: str = Query(..., description="Region (required)"),
    sku: str = Query(..., description="SKU name (required)"),
    osType: str | None = Query(None, description="OS type filter"),  # noqa: N803
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
) -> JSONResponse:
    """Composite spot detail: spot price + eviction rate + SKU catalog (v1)."""
    from az_scout_bdd_sku.db_api import spot_detail
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt

    try:
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        data, resolved = await spot_detail(
            region,
            sku,
            os_type=osType,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/spot/detail error")
        return _error_response(500, "INTERNAL", "Query failed")

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    page = {"limit": 1, "cursor": None, "hasMore": False}
    return JSONResponse(content={"item": data, "page": page, "meta": _meta(snap_meta)})


# ------------------------------------------------------------------
# Savings plans
# ------------------------------------------------------------------


@v1_router.get("/retail/savings-plans")
async def v1_savings_plans(
    region: str | None = Query(None, description="Filter by region"),
    sku: str | None = Query(None, description="Filter by SKU (substring)"),
    currency: str | None = Query(None, description="Currency code filter"),
    snapshotDate: str | None = Query(  # noqa: N803
        None,
        description="ISO datetime — scope to snapshot <= value (default: latest)",
    ),
    limit: int | None = Query(None, description="Page size (1-5000, default 1000)"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
) -> JSONResponse:
    """Retail prices with savings plan data (v1, paginated)."""
    from az_scout_bdd_sku.db_api import list_savings_plans
    from az_scout_bdd_sku.pagination import InvalidCursorError, build_page, decode_cursor
    from az_scout_bdd_sku.validation import ValidationError, parse_iso_dt, parse_limit

    try:
        lim = parse_limit(limit)
        snap = parse_iso_dt(snapshotDate, param_name="snapshotDate")
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_payload = decode_cursor(cursor)
        except InvalidCursorError as exc:
            return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items, resolved = await list_savings_plans(
            lim,
            cursor_payload,
            region=region,
            sku=sku,
            currency=currency,
            snapshot_date=snap,
        )
    except Exception:
        logger.exception("v1/retail/savings-plans error")
        return _error_response(500, "INTERNAL", "Query failed")

    def _cursor_builder(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "armRegionName": it["armRegionName"],
            "armSkuName": it["armSkuName"],
            "skuId": it["skuId"],
        }

    snap_meta = {"resolvedSnapshotDate": resolved.isoformat()} if resolved else {}
    trimmed, page = build_page(items, lim, cursor_builder=_cursor_builder)
    return JSONResponse(content={"items": trimmed, "page": page, "meta": _meta(snap_meta)})


# ------------------------------------------------------------------
# Pricing summary compare
# ------------------------------------------------------------------


@v1_router.get("/pricing/summary/compare")
async def v1_pricing_summary_compare(
    regions: list[str] = Query(  # noqa: B008
        ..., description="Regions to compare (multi-value, required)"
    ),
    priceType: str | None = Query(  # noqa: N803
        None, description="Price type: retail|spot"
    ),
    category: str | None = Query(None, description="Category filter"),
) -> JSONResponse:
    """Compare pricing summary across regions from the latest run (v1)."""
    from az_scout_bdd_sku.db_api import pricing_summary_compare
    from az_scout_bdd_sku.validation import (
        ValidationError,
        validate_price_type,
    )

    try:
        pt_val = validate_price_type(priceType) if priceType else None
    except ValidationError as exc:
        return _error_response(400, "BAD_REQUEST", str(exc))

    try:
        items = await pricing_summary_compare(regions, price_type=pt_val, category=category)
    except Exception:
        logger.exception("v1/pricing/summary/compare error")
        return _error_response(500, "INTERNAL", "Query failed")

    region_count = len(items)
    page = {"limit": region_count, "cursor": None, "hasMore": False}
    meta = _meta({"regionCount": region_count})
    return JSONResponse(content={"items": items, "page": page, "meta": meta})


# ------------------------------------------------------------------
# Global stats
# ------------------------------------------------------------------


@v1_router.get("/stats")
async def v1_stats() -> JSONResponse:
    """Global dashboard metrics across all tables (v1)."""
    from az_scout_bdd_sku.db_api import get_global_stats

    try:
        data = await get_global_stats()
    except Exception:
        logger.exception("v1/stats error")
        return _error_response(500, "INTERNAL", "Query failed")

    return JSONResponse(content={"item": data, "meta": _meta()})


# Include v1 sub-router
router.include_router(v1_router)
