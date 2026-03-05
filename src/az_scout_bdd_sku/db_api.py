"""Database access layer for the v1 read-only API.

All SQL lives here.  Routes and MCP tools import these functions
and never build SQL themselves.  Every query uses parameterised
placeholders — no string interpolation of user data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from az_scout_bdd_sku.db import get_conn, is_healthy
from az_scout_bdd_sku.pagination import keyset_clause

# ------------------------------------------------------------------
# Snapshot resolution helpers
# ------------------------------------------------------------------


async def _resolve_retail_snapshot(
    target_date: datetime | None = None,
) -> datetime | None:
    """Resolve the effective snapshot datetime for retail_prices_vm.

    - ``None`` → latest ``job_datetime`` in the table.
    - With date → latest ``job_datetime <= target_date``.

    Returns ``None`` when the table is empty or no snapshot matches.
    """
    if target_date is None:
        sql = "SELECT MAX(job_datetime) FROM retail_prices_vm"
        params: list[Any] = []
    else:
        sql = "SELECT MAX(job_datetime) FROM retail_prices_vm WHERE job_datetime <= %s"
        params = [target_date]

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def _resolve_spot_snapshot(
    target_date: datetime | None = None,
) -> datetime | None:
    """Resolve the effective snapshot datetime for spot_eviction_rates.

    - ``None`` → latest ``job_datetime`` in the table.
    - With date → latest ``job_datetime <= target_date``.

    Returns ``None`` when the table is empty or no snapshot matches.
    """
    if target_date is None:
        sql = "SELECT MAX(job_datetime) FROM spot_eviction_rates"
        params: list[Any] = []
    else:
        sql = "SELECT MAX(job_datetime) FROM spot_eviction_rates WHERE job_datetime <= %s"
        params = [target_date]

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return row[0] if row and row[0] else None


# ------------------------------------------------------------------
# /v1/status
# ------------------------------------------------------------------


async def get_status() -> dict[str, Any]:
    """Gather database health and per-table statistics."""
    db_ok = await is_healthy()
    datasets: dict[str, Any] = {
        "retail": {"rowCount": 0, "lastJobDatetimeUtc": None, "lastJobId": None},
        "spotPrices": {"rowCount": 0, "lastJobDatetimeUtc": None, "lastJobId": None},
        "evictionRates": {"rowCount": 0, "lastJobDatetimeUtc": None, "lastJobId": None},
    }

    if not db_ok:
        return {"dbConnected": False, "datasets": datasets}

    table_map: list[tuple[str, str]] = [
        ("retail", "retail_prices_vm"),
        ("spotPrices", "spot_price_history"),
        ("evictionRates", "spot_eviction_rates"),
    ]

    for key, table in table_map:
        try:
            async with get_conn() as conn:
                cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                row = await cur.fetchone()
                datasets[key]["rowCount"] = row[0] if row else 0

                cur = await conn.execute(
                    f"SELECT MAX(job_datetime) FROM {table}"  # noqa: S608
                )
                row = await cur.fetchone()
                if row and row[0] is not None:
                    datasets[key]["lastJobDatetimeUtc"] = row[0].isoformat()

                cur = await conn.execute(
                    f"SELECT job_id FROM {table} "  # noqa: S608
                    "ORDER BY job_datetime DESC NULLS LAST, job_id DESC "
                    "LIMIT 1"
                )
                row = await cur.fetchone()
                if row and row[0] is not None:
                    datasets[key]["lastJobId"] = str(row[0])
        except Exception:
            datasets[key]["rowCount"] = -1

    return {"dbConnected": True, "datasets": datasets}


# ------------------------------------------------------------------
# /v1/locations
# ------------------------------------------------------------------


async def list_locations(
    limit: int,
    cursor_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return distinct location names from all tables, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

    if cursor_payload is not None:
        ks_sql, ks_params = keyset_clause(["name"], cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        "SELECT name FROM ("
        "  SELECT DISTINCT arm_region_name AS name FROM retail_prices_vm"
        "  UNION"
        "  SELECT DISTINCT region AS name FROM spot_eviction_rates"
        "  UNION"
        "  SELECT DISTINCT region AS name FROM spot_price_history"
        f") AS u{where} ORDER BY name ASC LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [{"name": r[0]} for r in rows]


# ------------------------------------------------------------------
# /v1/skus
# ------------------------------------------------------------------


async def list_skus(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    search: str | None = None,
) -> list[dict[str, str]]:
    """Return distinct SKU names, optionally filtered by substring."""
    inner_clauses: list[str] = []
    inner_params: list[Any] = []

    outer_clauses: list[str] = []
    outer_params: list[Any] = []

    if search:
        inner_clauses.append('u."skuName" ILIKE %s')
        inner_params.append(f"%{search}%")

    if cursor_payload is not None:
        ks_sql, ks_params = keyset_clause(["skuName"], cursor_payload)
        outer_clauses.append(ks_sql.replace("skuName", '"skuName"'))
        outer_params.extend(ks_params)

    # search filter is applied on the outer query for consistency
    outer_filter_parts: list[str] = list(outer_clauses)
    all_outer_params: list[Any] = list(outer_params)

    if search:
        outer_filter_parts.append('"skuName" ILIKE %s')
        all_outer_params.append(f"%{search}%")

    outer_where = (" WHERE " + " AND ".join(outer_filter_parts)) if outer_filter_parts else ""

    sql = (
        'SELECT "skuName" FROM ('
        '  SELECT DISTINCT arm_sku_name AS "skuName" FROM retail_prices_vm'
        "    WHERE arm_sku_name IS NOT NULL"
        "  UNION"
        '  SELECT DISTINCT sku_name AS "skuName" FROM spot_eviction_rates'
        "  UNION"
        '  SELECT DISTINCT sku_name AS "skuName" FROM spot_price_history'
        f') AS u{outer_where} ORDER BY "skuName" ASC LIMIT %s'
    )
    all_outer_params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, all_outer_params)
        rows = await cur.fetchall()
    return [{"skuName": r[0]} for r in rows]


# ------------------------------------------------------------------
# /v1/currencies
# ------------------------------------------------------------------


async def list_currencies(
    limit: int,
    cursor_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return distinct currency codes from retail_prices_vm."""
    clauses: list[str] = []
    params: list[Any] = []

    if cursor_payload is not None:
        ks_sql, ks_params = keyset_clause(["currencyCode"], cursor_payload)
        clauses.append(ks_sql.replace("currencyCode", "currency_code"))
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        "SELECT DISTINCT currency_code FROM retail_prices_vm"
        f"{where} ORDER BY currency_code ASC LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [{"currencyCode": r[0]} for r in rows]


# ------------------------------------------------------------------
# /v1/os-types
# ------------------------------------------------------------------


async def list_os_types(
    limit: int,
    cursor_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return distinct OS types from spot_price_history."""
    clauses: list[str] = []
    params: list[Any] = []

    if cursor_payload is not None:
        ks_sql, ks_params = keyset_clause(["osType"], cursor_payload)
        clauses.append(ks_sql.replace("osType", "os_type"))
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"SELECT DISTINCT os_type FROM spot_price_history{where} ORDER BY os_type ASC LIMIT %s"
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [{"osType": r[0]} for r in rows]


# ------------------------------------------------------------------
# /v1/retail/prices
# ------------------------------------------------------------------

_RETAIL_SORT_COLS = [
    "currency_code",
    "arm_region_name",
    "arm_sku_name",
    "sku_id",
    "pricing_type",
    "reservation_term",
]

_RETAIL_CURSOR_MAP: dict[str, str] = {
    "currencyCode": "currency_code",
    "armRegionName": "arm_region_name",
    "armSkuName": "arm_sku_name",
    "skuId": "sku_id",
    "pricingType": "pricing_type",
    "reservationTerm": "reservation_term",
}


def _retail_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    """Map camelCase cursor keys to SQL columns and build keyset clause."""
    mapped: dict[str, Any] = {}
    for camel, col in _RETAIL_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_RETAIL_SORT_COLS, mapped)


async def list_retail_prices(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    region: str | None = None,
    sku: str | None = None,
    currency: str | None = None,
    effective_at: datetime | None = None,
    updated_since: datetime | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Return retail prices with optional filters, keyset-paginated.

    Returns ``(items, resolved_snapshot)`` where *resolved_snapshot* is the
    effective ``job_datetime`` used for scoping (or ``None`` when empty).
    """
    resolved = await _resolve_retail_snapshot(snapshot_date)

    clauses: list[str] = []
    params: list[Any] = []

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if region:
        clauses.append("arm_region_name = %s")
        params.append(region)
    if sku:
        clauses.append("arm_sku_name = %s")
        params.append(sku)
    if currency:
        clauses.append("currency_code = %s")
        params.append(currency)
    if effective_at:
        clauses.append("effective_start_date <= %s")
        params.append(effective_at)
    if updated_since:
        clauses.append("job_datetime >= %s")
        params.append(updated_since)

    if cursor_payload is not None:
        ks_sql, ks_params = _retail_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_RETAIL_SORT_COLS)

    sql = (
        "SELECT currency_code, arm_region_name, arm_sku_name, sku_id,"
        "  pricing_type, reservation_term, retail_price, unit_price,"
        "  unit_of_measure, effective_start_date, job_id, job_datetime"
        f" FROM retail_prices_vm{where}"
        f" ORDER BY {order} ASC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_retail_row_to_dict(r) for r in rows], resolved


def _retail_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "currencyCode": r[0],
        "armRegionName": r[1],
        "armSkuName": r[2],
        "skuId": r[3],
        "pricingType": r[4],
        "reservationTerm": r[5],
        "retailPrice": float(r[6]) if r[6] is not None else None,
        "unitPrice": float(r[7]) if r[7] is not None else None,
        "unitOfMeasure": r[8],
        "effectiveStartDate": r[9].isoformat() if r[9] else None,
        "jobId": r[10],
        "jobDatetime": r[11].isoformat() if r[11] else None,
    }


# ------------------------------------------------------------------
# /v1/retail/prices/latest
# ------------------------------------------------------------------

_LATEST_SORT_COLS = [
    "currency_code",
    "arm_region_name",
    "sku_id",
    "pricing_type",
    "reservation_term",
]

_LATEST_CURSOR_MAP: dict[str, str] = {
    "currencyCode": "currency_code",
    "armRegionName": "arm_region_name",
    "skuId": "sku_id",
    "pricingType": "pricing_type",
    "reservationTerm": "reservation_term",
}


def _latest_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _LATEST_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_LATEST_SORT_COLS, mapped)


async def list_retail_prices_latest(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    region: str | None = None,
    sku: str | None = None,
    currency: str | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Return the latest snapshot per unique retail key, keyset-paginated.

    Returns ``(items, resolved_snapshot)``.
    """
    resolved = await _resolve_retail_snapshot(snapshot_date)

    clauses: list[str] = []
    params: list[Any] = []

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if region:
        clauses.append("arm_region_name = %s")
        params.append(region)
    if sku:
        clauses.append("arm_sku_name = %s")
        params.append(sku)
    if currency:
        clauses.append("currency_code = %s")
        params.append(currency)

    if cursor_payload is not None:
        ks_sql, ks_params = _latest_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_LATEST_SORT_COLS)

    sql = (
        "SELECT DISTINCT ON (currency_code, arm_region_name, sku_id,"
        "  pricing_type, reservation_term)"
        "  currency_code, arm_region_name, arm_sku_name, sku_id,"
        "  pricing_type, reservation_term, retail_price, unit_price,"
        "  unit_of_measure, effective_start_date, job_id, job_datetime"
        f" FROM retail_prices_vm{where}"
        f" ORDER BY {order} ASC, job_datetime DESC NULLS LAST, job_id DESC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_retail_row_to_dict(r) for r in rows], resolved


# ------------------------------------------------------------------
# /v1/spot/prices
# ------------------------------------------------------------------

_SPOT_PRICE_SORT_COLS = ["region", "sku_name", "os_type"]

_SPOT_PRICE_CURSOR_MAP: dict[str, str] = {
    "region": "region",
    "skuName": "sku_name",
    "osType": "os_type",
}


def _spot_price_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _SPOT_PRICE_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_SPOT_PRICE_SORT_COLS, mapped)


async def list_spot_prices(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    region: str | None = None,
    sku: str | None = None,
    os_type: str | None = None,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return spot price history rows, keyset-paginated.

    ``dt_from`` / ``dt_to`` filter on ``job_datetime`` (snapshot time).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if region:
        clauses.append("region = %s")
        params.append(region)
    if sku:
        clauses.append("sku_name = %s")
        params.append(sku)
    if os_type:
        clauses.append("os_type = %s")
        params.append(os_type)
    if dt_from:
        clauses.append("job_datetime >= %s")
        params.append(dt_from)
    if dt_to:
        clauses.append("job_datetime <= %s")
        params.append(dt_to)

    if cursor_payload is not None:
        ks_sql, ks_params = _spot_price_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_SPOT_PRICE_SORT_COLS)

    sql = (
        "SELECT region, sku_name, os_type, job_id, job_datetime, price_history"
        f" FROM spot_price_history{where}"
        f" ORDER BY {order} ASC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "region": r[0],
            "skuName": r[1],
            "osType": r[2],
            "jobId": r[3],
            "jobDatetime": r[4].isoformat() if r[4] else None,
            "priceHistory": r[5],
        }
        for r in rows
    ]


# ------------------------------------------------------------------
# /v1/spot/eviction-rates
# ------------------------------------------------------------------

_EVICTION_SORT_COLS = ["job_datetime", "region", "sku_name", "job_id"]

_EVICTION_CURSOR_MAP: dict[str, str] = {
    "jobDatetimeUtc": "job_datetime",
    "region": "region",
    "skuName": "sku_name",
    "jobId": "job_id",
}


def _eviction_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _EVICTION_CURSOR_MAP.items():
        if camel in payload:
            val = payload[camel]
            # job_datetime may arrive as ISO string from cursor
            if col == "job_datetime" and isinstance(val, str):
                from datetime import datetime as _dt

                try:
                    parsed = _dt.fromisoformat(val)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    val = parsed
                except ValueError:
                    pass
            mapped[col] = val
    return keyset_clause(_EVICTION_SORT_COLS, mapped)


async def list_eviction_rates(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    region: str | None = None,
    sku: str | None = None,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
    updated_since: datetime | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Return spot eviction rates, keyset-paginated.

    Returns ``(items, resolved_snapshot)``.
    """
    resolved = await _resolve_spot_snapshot(snapshot_date)

    clauses: list[str] = []
    params: list[Any] = []

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if region:
        clauses.append("region = %s")
        params.append(region)
    if sku:
        clauses.append("sku_name = %s")
        params.append(sku)
    if dt_from:
        clauses.append("job_datetime >= %s")
        params.append(dt_from)
    if dt_to:
        clauses.append("job_datetime <= %s")
        params.append(dt_to)
    if updated_since:
        clauses.append("job_datetime >= %s")
        params.append(updated_since)

    if cursor_payload is not None:
        ks_sql, ks_params = _eviction_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_EVICTION_SORT_COLS)

    sql = (
        "SELECT region, sku_name, job_id, job_datetime,"
        "  eviction_rate,"
        "  CASE WHEN eviction_rate ~ '^[0-9]+(\\.[0-9]+)?$'"
        "    THEN eviction_rate::numeric ELSE NULL END"
        f" FROM spot_eviction_rates{where}"
        f" ORDER BY {order} ASC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "region": r[0],
            "skuName": r[1],
            "jobId": r[2],
            "jobDatetimeUtc": r[3].isoformat() if r[3] else None,
            "evictionRateRaw": r[4],
            "evictionRate": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ], resolved


# ------------------------------------------------------------------
# /v1/spot/eviction-rates/series
# ------------------------------------------------------------------

_VALID_AGG_FUNCS = {"avg", "min", "max"}
_VALID_BUCKETS = {"hour", "day", "week"}


async def eviction_rate_series(
    region: str,
    sku: str,
    bucket: str,
    *,
    agg: str = "avg",
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return time-bucketed aggregation of eviction rates."""
    clauses = ["region = %s", "sku_name = %s"]
    params: list[Any] = [region, sku]

    # Only numeric eviction rates
    clauses.append("eviction_rate ~ '^[0-9]+(\\.[0-9]+)?$'")

    if dt_from:
        clauses.append("job_datetime >= %s")
        params.append(dt_from)
    if dt_to:
        clauses.append("job_datetime <= %s")
        params.append(dt_to)

    where = " WHERE " + " AND ".join(clauses)

    # bucket and agg are validated by caller so safe to interpolate
    sql = (
        f"SELECT date_trunc('{bucket}', job_datetime) AS bucket_ts,"
        f"  {agg}(eviction_rate::numeric) AS value,"
        "  count(*) AS points"
        f" FROM spot_eviction_rates{where}"
        " GROUP BY bucket_ts ORDER BY bucket_ts ASC"
    )

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "bucketTs": r[0].isoformat() if r[0] else None,
            "value": float(r[1]) if r[1] is not None else None,
            "points": r[2],
        }
        for r in rows
    ]


# ------------------------------------------------------------------
# /v1/spot/eviction-rates/latest
# ------------------------------------------------------------------


async def list_eviction_rates_latest(
    limit: int,
    *,
    region: str | None = None,
    sku: str | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Return latest eviction rate per (region, sku_name).

    Returns ``(items, resolved_snapshot)``.
    """
    resolved = await _resolve_spot_snapshot(snapshot_date)

    clauses: list[str] = []
    params: list[Any] = []

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if region:
        clauses.append("region = %s")
        params.append(region)
    if sku:
        clauses.append("sku_name = %s")
        params.append(sku)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        "SELECT DISTINCT ON (region, sku_name)"
        "  region, sku_name, job_id, job_datetime,"
        "  eviction_rate,"
        "  CASE WHEN eviction_rate ~ '^[0-9]+(\\.[0-9]+)?$'"
        "    THEN eviction_rate::numeric ELSE NULL END"
        f" FROM spot_eviction_rates{where}"
        " ORDER BY region, sku_name, job_datetime DESC, job_id DESC"
        " LIMIT %s"
    )
    params.append(limit)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "region": r[0],
            "skuName": r[1],
            "jobId": r[2],
            "jobDatetimeUtc": r[3].isoformat() if r[3] else None,
            "evictionRateRaw": r[4],
            "evictionRate": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ], resolved


# ==================================================================
# Pricing summary (price_summary table)
# ==================================================================


def _price_summary_row_to_dict(r: Any) -> dict[str, Any]:
    """Map a price_summary row tuple to a camelCase dict."""
    return {
        "id": r[0],
        "runId": str(r[1]),
        "snapshotUtc": r[2].isoformat() if r[2] else None,
        "region": r[3],
        "category": r[4],
        "priceType": r[5],
        "currencyCode": r[6],
        "avgPrice": float(r[7]) if r[7] is not None else None,
        "medianPrice": float(r[8]) if r[8] is not None else None,
        "minPrice": float(r[9]) if r[9] is not None else None,
        "maxPrice": float(r[10]) if r[10] is not None else None,
        "p10Price": float(r[11]) if r[11] is not None else None,
        "p25Price": float(r[12]) if r[12] is not None else None,
        "p75Price": float(r[13]) if r[13] is not None else None,
        "p90Price": float(r[14]) if r[14] is not None else None,
        "skuCount": r[15],
    }


_PRICE_SUMMARY_COLS = (
    "id, run_id, snapshot_utc, region, category, price_type, currency_code,"
    " avg_price, median_price, min_price, max_price,"
    " p10_price, p25_price, p75_price, p90_price, sku_count"
)

_PRICING_SORT_COLS = ["region", "COALESCE(category,'')", "price_type", "id"]

_PRICING_CURSOR_MAP: dict[str, str] = {
    "region": "region",
    "category": "COALESCE(category,'')",
    "priceType": "price_type",
    "id": "id",
}


def _pricing_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    """Map camelCase cursor keys to SQL columns and build keyset clause."""
    mapped: dict[str, Any] = {}
    for camel, col in _PRICING_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_PRICING_SORT_COLS, mapped)


# ------------------------------------------------------------------
# /v1/pricing/categories
# ------------------------------------------------------------------


async def list_pricing_categories(
    limit: int,
    cursor_payload: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
    """Return distinct category values from price_summary, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

    if cursor_payload is not None:
        ks_sql, ks_params = keyset_clause(
            ["category_sort"],
            {"category_sort": cursor_payload.get("category", "")},
        )
        clauses.append(ks_sql.replace("category_sort", "COALESCE(category, '')"))
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        "SELECT DISTINCT category FROM price_summary"
        f"{where} ORDER BY COALESCE(category, '') ASC LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [{"category": r[0]} for r in rows]


# ------------------------------------------------------------------
# /v1/pricing/summary
# ------------------------------------------------------------------


async def list_pricing_summary(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    regions: list[str] | None = None,
    categories: list[str] | None = None,
    price_types: list[str] | None = None,
    snapshot_since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return price_summary rows with multi-value filters, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

    if regions:
        clauses.append("region = ANY(%s::text[])")
        params.append(regions)
    if categories:
        clauses.append("COALESCE(category, '') = ANY(%s::text[])")
        params.append(categories)
    if price_types:
        clauses.append("price_type = ANY(%s::text[])")
        params.append(price_types)
    if snapshot_since:
        clauses.append("snapshot_utc >= %s")
        params.append(snapshot_since)

    if cursor_payload is not None:
        ks_sql, ks_params = _pricing_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_PRICING_SORT_COLS)

    sql = f"SELECT {_PRICE_SUMMARY_COLS} FROM price_summary{where} ORDER BY {order} ASC LIMIT %s"
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_price_summary_row_to_dict(r) for r in rows]


# ------------------------------------------------------------------
# /v1/pricing/summary/latest
# ------------------------------------------------------------------


async def list_pricing_summary_latest(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    regions: list[str] | None = None,
    categories: list[str] | None = None,
    price_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return price_summary rows from the most recent run_id only."""
    clauses = ["run_id = (SELECT run_id FROM price_summary ORDER BY snapshot_utc DESC LIMIT 1)"]
    params: list[Any] = []

    if regions:
        clauses.append("region = ANY(%s::text[])")
        params.append(regions)
    if categories:
        clauses.append("COALESCE(category, '') = ANY(%s::text[])")
        params.append(categories)
    if price_types:
        clauses.append("price_type = ANY(%s::text[])")
        params.append(price_types)

    if cursor_payload is not None:
        ks_sql, ks_params = _pricing_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = " WHERE " + " AND ".join(clauses)
    order = ", ".join(_PRICING_SORT_COLS)

    sql = f"SELECT {_PRICE_SUMMARY_COLS} FROM price_summary{where} ORDER BY {order} ASC LIMIT %s"
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_price_summary_row_to_dict(r) for r in rows]


# ------------------------------------------------------------------
# /v1/pricing/summary/series
# ------------------------------------------------------------------

_VALID_PRICING_BUCKETS = {"day", "week", "month"}
_VALID_METRICS = {
    "avg_price",
    "median_price",
    "min_price",
    "max_price",
    "p10_price",
    "p25_price",
    "p75_price",
    "p90_price",
}


async def pricing_summary_series(
    region: str,
    price_type: str,
    bucket: str,
    *,
    metric: str = "median_price",
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Return time-bucketed aggregation of a pricing metric over runs."""
    clauses = ["region = %s", "price_type = %s"]
    params: list[Any] = [region, price_type]

    if category is not None:
        clauses.append("COALESCE(category, '') = %s")
        params.append(category)
    else:
        clauses.append("category IS NULL")

    where = " WHERE " + " AND ".join(clauses)

    # bucket and metric are validated by caller so safe to interpolate
    sql = (
        f"SELECT date_trunc('{bucket}', snapshot_utc) AS bucket_ts,"
        f"  avg({metric}) AS value,"
        "  sum(sku_count) AS total_skus,"
        "  count(*) AS points"
        f" FROM price_summary{where}"
        " GROUP BY bucket_ts ORDER BY bucket_ts ASC"
    )

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "bucketTs": r[0].isoformat() if r[0] else None,
            "value": float(r[1]) if r[1] is not None else None,
            "totalSkus": r[2],
            "points": r[3],
        }
        for r in rows
    ]


# ------------------------------------------------------------------
# /v1/pricing/summary/cheapest
# ------------------------------------------------------------------


async def list_pricing_cheapest(
    limit: int,
    *,
    price_type: str = "retail",
    metric: str = "median_price",
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Return the N cheapest regions from the latest run, ranked by *metric*."""
    clauses = [
        "run_id = (SELECT run_id FROM price_summary ORDER BY snapshot_utc DESC LIMIT 1)",
        "price_type = %s",
    ]
    params: list[Any] = [price_type]

    if category is not None:
        clauses.append("COALESCE(category, '') = %s")
        params.append(category)
    else:
        clauses.append("category IS NULL")

    where = " WHERE " + " AND ".join(clauses)

    # metric is validated by caller so safe to interpolate
    sql = f"SELECT {_PRICE_SUMMARY_COLS} FROM price_summary{where} ORDER BY {metric} ASC LIMIT %s"
    params.append(limit)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_price_summary_row_to_dict(r) for r in rows]


# ==================================================================
# /v1/skus/catalog
# ==================================================================

_SKU_COLS = (
    "sku_name, tier, family, series, version, vcpus,"
    " sku_type, category, workload_tags, source,"
    " first_seen_utc, last_seen_utc, updated_at_utc"
)

_SKU_SORT_COLS = ["sku_name"]

_SKU_CURSOR_MAP: dict[str, str] = {"skuName": "sku_name"}


def _sku_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _SKU_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_SKU_SORT_COLS, mapped)


def _sku_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "skuName": r[0],
        "tier": r[1],
        "family": r[2],
        "series": r[3],
        "version": r[4],
        "vcpus": r[5],
        "skuType": r[6],
        "category": r[7],
        "workloadTags": list(r[8]) if r[8] else [],
        "source": r[9],
        "firstSeenUtc": r[10].isoformat() if r[10] else None,
        "lastSeenUtc": r[11].isoformat() if r[11] else None,
        "updatedAtUtc": r[12].isoformat() if r[12] else None,
    }


async def list_sku_catalog(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    search: str | None = None,
    category: str | None = None,
    family: str | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
) -> list[dict[str, Any]]:
    """Return VM SKU catalog entries, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

    if search:
        clauses.append("sku_name ILIKE %s")
        params.append(f"%{search}%")
    if category:
        clauses.append("category = %s")
        params.append(category)
    if family:
        clauses.append("family = %s")
        params.append(family)
    if min_vcpus is not None:
        clauses.append("vcpus >= %s")
        params.append(min_vcpus)
    if max_vcpus is not None:
        clauses.append("vcpus <= %s")
        params.append(max_vcpus)

    if cursor_payload is not None:
        ks_sql, ks_params = _sku_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = ", ".join(_SKU_SORT_COLS)

    sql = f"SELECT {_SKU_COLS} FROM vm_sku_catalog{where} ORDER BY {order} ASC LIMIT %s"
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_sku_row_to_dict(r) for r in rows]


# ==================================================================
# /v1/jobs
# ==================================================================

_JOB_COLS = (
    "run_id, dataset, status, started_at_utc, finished_at_utc,"
    " items_read, items_written, error_message, details"
)

_JOB_SORT_COLS = ["started_at_utc", "run_id"]

_JOB_CURSOR_MAP: dict[str, str] = {
    "startedAtUtc": "started_at_utc",
    "runId": "run_id",
}


def _job_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _JOB_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_JOB_SORT_COLS, mapped)


def _job_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "runId": str(r[0]) if r[0] else None,
        "dataset": r[1],
        "status": r[2],
        "startedAtUtc": r[3].isoformat() if r[3] else None,
        "finishedAtUtc": r[4].isoformat() if r[4] else None,
        "itemsRead": r[5],
        "itemsWritten": r[6],
        "errorMessage": r[7],
        "details": r[8],
    }


async def list_jobs(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    dataset: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return job runs, keyset-paginated (newest first)."""
    clauses: list[str] = []
    params: list[Any] = []

    if dataset:
        clauses.append("dataset = %s")
        params.append(dataset)
    if status:
        clauses.append("status = %s")
        params.append(status)

    if cursor_payload is not None:
        ks_sql, ks_params = _job_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        f"SELECT {_JOB_COLS} FROM job_runs{where}"
        " ORDER BY started_at_utc DESC, run_id DESC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_job_row_to_dict(r) for r in rows]


# ==================================================================
# /v1/jobs/{run_id}/logs
# ==================================================================

_LOG_COLS = "id, run_id, ts_utc, level, message, context"

_LOG_SORT_COLS = ["ts_utc", "id"]

_LOG_CURSOR_MAP: dict[str, str] = {
    "tsUtc": "ts_utc",
    "id": "id",
}


def _log_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _LOG_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_LOG_SORT_COLS, mapped)


def _log_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "runId": str(r[1]) if r[1] else None,
        "tsUtc": r[2].isoformat() if r[2] else None,
        "level": r[3],
        "message": r[4],
        "context": r[5],
    }


async def list_job_logs(
    run_id: str,
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    level: str | None = None,
) -> list[dict[str, Any]]:
    """Return logs for a specific job run, keyset-paginated (newest first)."""
    clauses: list[str] = ["run_id = %s"]
    params: list[Any] = [run_id]

    if level:
        clauses.append("level = %s")
        params.append(level)

    if cursor_payload is not None:
        ks_sql, ks_params = _log_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = " WHERE " + " AND ".join(clauses)

    sql = f"SELECT {_LOG_COLS} FROM job_logs{where} ORDER BY ts_utc DESC, id DESC LIMIT %s"
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_log_row_to_dict(r) for r in rows]


# ==================================================================
# /v1/spot/prices/series
# ==================================================================


async def spot_price_series(
    region: str,
    sku: str,
    bucket: str,
    *,
    os_type: str | None = None,
) -> list[dict[str, Any]]:
    """Denormalise JSONB price_history and aggregate by time bucket.

    Returns time-bucketed average spot price series.
    """
    clauses = ["region = %s", "sku_name = %s"]
    params: list[Any] = [region, sku]

    if os_type:
        clauses.append("os_type = %s")
        params.append(os_type)

    where = " WHERE " + " AND ".join(clauses)

    # bucket is passed as a parameter, not interpolated
    sql = (
        "SELECT date_trunc(%s, (elem->>'timestamp')::timestamptz) AS bucket_ts,"
        "  avg((elem->>'spotPrice')::numeric) AS avg_price,"
        "  min((elem->>'spotPrice')::numeric) AS min_price,"
        "  max((elem->>'spotPrice')::numeric) AS max_price,"
        "  count(*) AS data_points"
        f" FROM spot_price_history{where},"
        "  jsonb_array_elements(price_history) AS elem"
        " GROUP BY bucket_ts ORDER BY bucket_ts ASC"
    )
    params.insert(0, bucket)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {
            "bucketTs": r[0].isoformat() if r[0] else None,
            "avgPrice": float(r[1]) if r[1] is not None else None,
            "minPrice": float(r[2]) if r[2] is not None else None,
            "maxPrice": float(r[3]) if r[3] is not None else None,
            "dataPoints": r[4],
        }
        for r in rows
    ]


# ==================================================================
# /v1/retail/prices/compare
# ==================================================================


async def retail_prices_compare(
    sku: str,
    *,
    currency: str | None = None,
    pricing_type: str | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[dict[str, Any], datetime | None]:
    """Compare a SKU's retail price across regions.

    Returns ``(region_dict, resolved_snapshot)`` where *region_dict* is
    keyed by region with price info per region.
    """
    resolved = await _resolve_retail_snapshot(snapshot_date)

    clauses = ["arm_sku_name ILIKE %s"]
    params: list[Any] = [f"%{sku}%"]

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if currency:
        clauses.append("currency_code = %s")
        params.append(currency)
    if pricing_type:
        clauses.append("pricing_type = %s")
        params.append(pricing_type)

    where = " WHERE " + " AND ".join(clauses)

    sql = (
        "SELECT arm_region_name, arm_sku_name, sku_id, currency_code,"
        "  pricing_type, reservation_term, retail_price, unit_price,"
        "  unit_of_measure, effective_start_date"
        f" FROM retail_prices_vm{where}"
        " ORDER BY arm_region_name ASC, retail_price ASC"
    )

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        region_name = r[0]
        entry = {
            "armSkuName": r[1],
            "skuId": r[2],
            "currencyCode": r[3],
            "pricingType": r[4],
            "reservationTerm": r[5],
            "retailPrice": float(r[6]) if r[6] is not None else None,
            "unitPrice": float(r[7]) if r[7] is not None else None,
            "unitOfMeasure": r[8],
            "effectiveStartDate": r[9].isoformat() if r[9] else None,
        }
        result.setdefault(region_name, []).append(entry)
    return result, resolved


# ==================================================================
# /v1/spot/detail
# ==================================================================


async def spot_detail(
    region: str,
    sku: str,
    *,
    os_type: str | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[dict[str, Any], datetime | None]:
    """Combine spot price, eviction rate, and SKU catalog data.

    Returns ``(data, resolved_snapshot)`` with raw data from all three
    sources for a specific SKU/region.
    """
    resolved = await _resolve_spot_snapshot(snapshot_date)

    result: dict[str, Any] = {"region": region, "sku": sku}

    # 1. Spot price history (latest record)
    spot_clauses = ["region = %s", "sku_name = %s"]
    spot_params: list[Any] = [region, sku]
    if os_type:
        spot_clauses.append("os_type = %s")
        spot_params.append(os_type)

    spot_sql = (
        "SELECT sku_name, os_type, region, price_history, job_id, job_datetime"
        " FROM spot_price_history"
        f" WHERE {' AND '.join(spot_clauses)}"
        " LIMIT 1"
    )
    async with get_conn() as conn:
        cur = await conn.execute(spot_sql, spot_params)
        row = await cur.fetchone()
    if row:
        result["spotPrice"] = {
            "skuName": row[0],
            "osType": row[1],
            "region": row[2],
            "priceHistory": row[3],
            "jobId": row[4],
            "jobDatetime": row[5].isoformat() if row[5] else None,
        }
    else:
        result["spotPrice"] = None

    # 2. Eviction rate (scoped to resolved snapshot)
    eviction_clauses = ["region = %s", "sku_name = %s"]
    eviction_params: list[Any] = [region, sku]
    if resolved is not None:
        eviction_clauses.append("job_datetime = %s")
        eviction_params.append(resolved)

    eviction_sql = (
        "SELECT sku_name, region, eviction_rate, job_id, job_datetime"
        " FROM spot_eviction_rates"
        f" WHERE {' AND '.join(eviction_clauses)}"
        " ORDER BY job_datetime DESC NULLS LAST"
        " LIMIT 1"
    )
    async with get_conn() as conn:
        cur = await conn.execute(eviction_sql, eviction_params)
        row = await cur.fetchone()
    if row:
        result["evictionRate"] = {
            "skuName": row[0],
            "region": row[1],
            "evictionRate": row[2],
            "jobId": row[3],
            "jobDatetime": row[4].isoformat() if row[4] else None,
        }
    else:
        result["evictionRate"] = None

    # 3. SKU catalog enrichment
    catalog_sql = f"SELECT {_SKU_COLS} FROM vm_sku_catalog WHERE sku_name = %s"
    async with get_conn() as conn:
        cur = await conn.execute(catalog_sql, [sku])
        row = await cur.fetchone()
    result["catalog"] = _sku_row_to_dict(row) if row else None

    return result, resolved


# ==================================================================
# /v1/retail/savings-plans
# ==================================================================

_SAVINGS_SORT_COLS = ["arm_region_name", "arm_sku_name", "sku_id"]

_SAVINGS_CURSOR_MAP: dict[str, str] = {
    "armRegionName": "arm_region_name",
    "armSkuName": "arm_sku_name",
    "skuId": "sku_id",
}


def _savings_cursor_to_sql(payload: dict[str, Any]) -> tuple[str, list[Any]]:
    mapped: dict[str, Any] = {}
    for camel, col in _SAVINGS_CURSOR_MAP.items():
        if camel in payload:
            mapped[col] = payload[camel]
    return keyset_clause(_SAVINGS_SORT_COLS, mapped)


def _savings_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "armRegionName": r[0],
        "armSkuName": r[1],
        "skuId": r[2],
        "currencyCode": r[3],
        "pricingType": r[4],
        "retailPrice": float(r[5]) if r[5] is not None else None,
        "unitPrice": float(r[6]) if r[6] is not None else None,
        "savingsPlan": r[7],
    }


async def list_savings_plans(
    limit: int,
    cursor_payload: dict[str, Any] | None,
    *,
    region: str | None = None,
    sku: str | None = None,
    currency: str | None = None,
    snapshot_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Return retail prices that have savings plan data, keyset-paginated.

    Returns ``(items, resolved_snapshot)``.
    """
    resolved = await _resolve_retail_snapshot(snapshot_date)

    clauses: list[str] = ["savings_plan IS NOT NULL"]
    params: list[Any] = []

    if resolved is not None:
        clauses.append("job_datetime = %s")
        params.append(resolved)

    if region:
        clauses.append("arm_region_name = %s")
        params.append(region)
    if sku:
        clauses.append("arm_sku_name ILIKE %s")
        params.append(f"%{sku}%")
    if currency:
        clauses.append("currency_code = %s")
        params.append(currency)

    if cursor_payload is not None:
        ks_sql, ks_params = _savings_cursor_to_sql(cursor_payload)
        clauses.append(ks_sql)
        params.extend(ks_params)

    where = " WHERE " + " AND ".join(clauses)
    order = ", ".join(_SAVINGS_SORT_COLS)

    sql = (
        "SELECT arm_region_name, arm_sku_name, sku_id, currency_code,"
        "  pricing_type, retail_price, unit_price, savings_plan"
        f" FROM retail_prices_vm{where}"
        f" ORDER BY {order} ASC"
        " LIMIT %s"
    )
    params.append(limit + 1)

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [_savings_row_to_dict(r) for r in rows], resolved


# ==================================================================
# /v1/pricing/summary/compare
# ==================================================================


async def pricing_summary_compare(
    regions: list[str],
    *,
    price_type: str | None = None,
    metric: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Compare pricing summary across regions from the latest run.

    Returns a dict keyed by region with aggregated pricing stats.
    """
    clauses = [
        "run_id = (SELECT run_id FROM price_summary ORDER BY snapshot_utc DESC LIMIT 1)",
        "region = ANY(%s::text[])",
    ]
    params: list[Any] = [regions]

    if price_type:
        clauses.append("price_type = %s")
        params.append(price_type)
    if category is not None:
        clauses.append("COALESCE(category, '') = %s")
        params.append(category)

    where = " WHERE " + " AND ".join(clauses)

    sql = f"SELECT {_PRICE_SUMMARY_COLS} FROM price_summary{where} ORDER BY region ASC"

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        item = _price_summary_row_to_dict(r)
        region_name = item["region"]
        result.setdefault(region_name, []).append(item)
    return result


# ==================================================================
# /v1/stats
# ==================================================================


async def get_global_stats() -> dict[str, Any]:
    """Gather global dashboard metrics across all tables."""
    stats: dict[str, Any] = {}

    async with get_conn() as conn:
        # Row counts
        tables = [
            ("retailPrices", "retail_prices_vm"),
            ("spotPrices", "spot_price_history"),
            ("evictionRates", "spot_eviction_rates"),
            ("priceSummary", "price_summary"),
            ("skuCatalog", "vm_sku_catalog"),
            ("jobRuns", "job_runs"),
            ("jobLogs", "job_logs"),
        ]
        counts: dict[str, int] = {}
        for key, table in tables:
            cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            row = await cur.fetchone()
            counts[key] = row[0] if row else 0
        stats["rowCounts"] = counts

        # Distinct regions
        cur = await conn.execute("SELECT COUNT(DISTINCT arm_region_name) FROM retail_prices_vm")
        row = await cur.fetchone()
        stats["distinctRegions"] = row[0] if row else 0

        # Distinct SKUs
        cur = await conn.execute("SELECT COUNT(*) FROM vm_sku_catalog")
        row = await cur.fetchone()
        stats["distinctSkus"] = row[0] if row else 0

        # Latest job per dataset
        cur = await conn.execute(
            "SELECT dataset, MAX(started_at_utc) AS last_run,"
            "  COUNT(*) AS total_runs"
            " FROM job_runs"
            " GROUP BY dataset ORDER BY dataset"
        )
        rows = await cur.fetchall()
        stats["latestJobs"] = {
            r[0]: {
                "lastRun": r[1].isoformat() if r[1] else None,
                "totalRuns": r[2],
            }
            for r in rows
        }

        # Data freshness (newest job_datetime per source table)
        freshness: dict[str, str | None] = {}
        for key, table in [
            ("retail", "retail_prices_vm"),
            ("spotPrices", "spot_price_history"),
            ("evictionRates", "spot_eviction_rates"),
        ]:
            cur = await conn.execute(
                f"SELECT MAX(job_datetime) FROM {table}"  # noqa: S608
            )
            row = await cur.fetchone()
            freshness[key] = row[0].isoformat() if row and row[0] else None
        stats["dataFreshness"] = freshness

    return stats
