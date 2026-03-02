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
) -> list[dict[str, Any]]:
    """Return retail prices with optional filters, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

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
    return [_retail_row_to_dict(r) for r in rows]


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
) -> list[dict[str, Any]]:
    """Return the latest snapshot per unique retail key, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

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
    return [_retail_row_to_dict(r) for r in rows]


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
) -> list[dict[str, Any]]:
    """Return spot eviction rates, keyset-paginated."""
    clauses: list[str] = []
    params: list[Any] = []

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
    ]


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
) -> list[dict[str, Any]]:
    """Return latest eviction rate per (region, sku_name)."""
    clauses: list[str] = []
    params: list[Any] = []

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
    ]
