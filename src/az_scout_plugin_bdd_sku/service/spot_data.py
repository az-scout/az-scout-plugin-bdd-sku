"""Spot data ingestion via Azure Resource Graph.

Queries ARG ``SpotResources`` table for:
- Spot price history (``microsoft.compute/skuspotpricehistory/ostype/location``)
- Spot eviction rates (``microsoft.compute/skuspotevictionrate/location``)
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

import requests
from azure.identity import DefaultAzureCredential

from az_scout_plugin_bdd_sku.config import get_config
from az_scout_plugin_bdd_sku.db import (
    create_ingest_run,
    finish_ingest_run,
    get_conn,
    release_advisory_lock,
    try_advisory_lock,
    upsert_spot_eviction_rates,
    upsert_spot_price_points,
)
from az_scout_plugin_bdd_sku.models import WarmResult
from az_scout_plugin_bdd_sku.service.locks import compute_lock_key
from az_scout_plugin_bdd_sku.service.retail_pricing import DEFAULT_SKU_SAMPLE

logger = logging.getLogger(__name__)

_ARG_API = (
    "https://management.azure.com/providers/"
    "Microsoft.ResourceGraph/resources?api-version=2022-10-01"
)


def _get_token(tenant_id: str | None = None) -> str:
    """Acquire a Bearer token for ARM using DefaultAzureCredential."""
    logger.info("Acquiring ARM token (tenant=%s)", tenant_id or "default")
    kwargs: dict[str, str] = {}
    if tenant_id:
        kwargs["tenant_id"] = tenant_id
    cred = DefaultAzureCredential(**kwargs)
    token = cred.get_token("https://management.azure.com/.default")
    logger.info("ARM token acquired successfully")
    return token.token


def _fetch_subscriptions(tenant_id: str | None = None) -> list[str]:
    """Retrieve subscriptions from core az_scout directly (in-process)."""
    from az_scout.azure_api.discovery import list_subscriptions

    subs = list_subscriptions(tenant_id=tenant_id)
    return [s["id"] for s in subs if s.get("id")]


def _run_arg_query(
    token: str,
    subscriptions: list[str],
    query: str,
) -> list[dict[str, Any]]:
    """Execute a Resource Graph query and return result rows."""
    body = {
        "subscriptions": subscriptions,
        "query": query,
        "options": {"resultFormat": "objectArray"},
    }
    logger.info(
        "ARG query: subs=%d, query=%.120s...",
        len(subscriptions),
        query.strip().replace("\n", " "),
    )
    resp = requests.post(
        _ARG_API,
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    logger.info("ARG response: status=%d", resp.status_code)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    data: list[dict[str, Any]] = result.get("data", [])
    logger.info("ARG returned %d rows", len(data))
    return data


_KQL_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _sanitize_kql_value(value: str) -> str:
    """Validate a value is safe for KQL string interpolation.

    Only allows alphanumeric characters, underscores, and hyphens.
    Raises ValueError for values containing special characters.
    """
    if not _KQL_SAFE_RE.match(value):
        raise ValueError(f"Unsafe KQL value rejected: {value!r}")
    return value


def _build_location_list(locations: list[str]) -> str:
    """Build a KQL in~ clause value with sanitized inputs."""
    safe = [_sanitize_kql_value(loc) for loc in locations]
    return ", ".join(f"'{loc}'" for loc in safe)


def _build_sku_list(skus: list[str]) -> str:
    """Build a KQL in~ clause value with sanitized inputs."""
    safe = [_sanitize_kql_value(s) for s in skus]
    return ", ".join(f"'{s}'" for s in safe)


def _query_spot_history(
    token: str,
    subscriptions: list[str],
    skus: list[str],
    locations: list[str],
    os_type: str = "linux",
) -> list[dict[str, Any]]:
    """Query ARG for spot price history."""
    sku_clause = _build_sku_list(skus)
    loc_clause = _build_location_list(locations)
    logger.info(
        "Querying spot history: %d SKUs, %d locations, os=%s",
        len(skus),
        len(locations),
        os_type,
    )
    kql = f"""
SpotResources
| where type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'
| where sku.name in~ ({sku_clause})
| where properties.osType =~ '{_sanitize_kql_value(os_type)}'
| where location in~ ({loc_clause})
| project skuName=tostring(sku.name),
          osType=tostring(properties.osType),
          location,
          spotPrices=properties.spotPrices
"""
    rows = _run_arg_query(token, subscriptions, kql)
    if not rows:
        logger.warning("Spot history query returned 0 rows")
    return rows


def _query_eviction_rates(
    token: str,
    subscriptions: list[str],
    skus: list[str],
    locations: list[str],
) -> list[dict[str, Any]]:
    """Query ARG for spot eviction rates."""
    sku_clause = _build_sku_list(skus)
    loc_clause = _build_location_list(locations)
    logger.info(
        "Querying eviction rates: %d SKUs, %d locations",
        len(skus),
        len(locations),
    )
    kql = f"""
SpotResources
| where type =~ 'microsoft.compute/skuspotevictionrate/location'
| where sku.name in~ ({sku_clause})
| where location in~ ({loc_clause})
| project skuName=tostring(sku.name),
          location,
          spotEvictionRate=tostring(properties.evictionRate)
"""
    rows = _run_arg_query(token, subscriptions, kql)
    if not rows:
        logger.warning("Eviction rates query returned 0 rows")
    return rows


def warm_spot_data(
    subscriptions: list[str] | None = None,
    locations: list[str] | None = None,
    sku_sample: list[str] | None = None,
    os_type: str = "linux",
    force: bool = False,
    tenant_id: str | None = None,
) -> WarmResult:
    """Warm spot history and eviction rate caches via ARG.

    Executes bulk KQL queries against Azure Resource Graph SpotResources
    and stores all returned data points in PostgreSQL.
    """
    cfg = get_config()  # noqa: F841 — may be used for future config
    started = datetime.now(UTC).isoformat()
    skus = sku_sample or DEFAULT_SKU_SAMPLE

    result = WarmResult(ok=False, dataset="spot", started_at=started)
    result.skus_count = len(skus)

    # Resolve subscriptions
    try:
        subs = subscriptions or _fetch_subscriptions(tenant_id)
        logger.info("Resolved %d subscriptions for ARG queries", len(subs))
    except Exception as exc:
        result.errors.append({"source": "subscriptions", "message": str(exc)})
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    if not subs:
        result.errors.append(
            {
                "source": "subscriptions",
                "message": "No subscriptions available for ARG queries",
            }
        )
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    # Resolve locations
    if not locations:
        try:
            from az_scout_plugin_bdd_sku.service.retail_pricing import _fetch_regions

            regions = _fetch_regions(tenant_id)
            locations = [r["name"] for r in regions]
        except Exception as exc:
            result.errors.append({"source": "locations", "message": str(exc)})
            result.finished_at = datetime.now(UTC).isoformat()
            return result

    result.regions_count = len(locations)
    logger.info(
        "Spot warm: %d subs, %d locations, %d SKUs",
        len(subs),
        len(locations),
        len(skus),
    )

    # Auth
    try:
        token = _get_token(tenant_id)
    except Exception as exc:
        logger.error("Failed to acquire ARM token: %s (type=%s)", exc, type(exc).__name__)
        result.errors.append({"source": "auth", "message": str(exc)})
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    # Create ingest runs
    with get_conn() as conn:
        run_id_history = create_ingest_run(conn, "spot_history")
        run_id_eviction = create_ingest_run(conn, "spot_eviction")
        conn.commit()
    result.run_id = str(run_id_history)

    # Advisory lock
    lock_key = compute_lock_key("spot", os_type, tenant_id or "")
    with get_conn() as lock_conn:
        if not force and not try_advisory_lock(lock_conn, lock_key):
            result.errors.append(
                {
                    "source": "lock",
                    "message": "Another spot warm run is in progress",
                }
            )
            result.finished_at = datetime.now(UTC).isoformat()
            with get_conn() as conn:
                finish_ingest_run(conn, run_id_history, "skipped", {"reason": "lock_held"})
                finish_ingest_run(conn, run_id_eviction, "skipped", {"reason": "lock_held"})
                conn.commit()
            return result

        try:
            _do_spot_warm(
                result,
                token,
                subs,
                skus,
                locations,
                os_type,
                tenant_id,
                run_id_history,
                run_id_eviction,
            )
        finally:
            release_advisory_lock(lock_conn, lock_key)

    result.ok = len(result.errors) == 0
    result.finished_at = datetime.now(UTC).isoformat()
    logger.info(
        "Spot warm finished: ok=%s, inserted=%d, updated=%d, errors=%d",
        result.ok,
        result.inserted,
        result.updated,
        len(result.errors),
    )
    return result


def _do_spot_warm(
    result: WarmResult,
    token: str,
    subs: list[str],
    skus: list[str],
    locations: list[str],
    os_type: str,
    tenant_id: str | None,
    run_id_history: Any,
    run_id_eviction: Any,
) -> None:
    """Execute ARG queries and ingest results."""
    # --- Spot price history ---
    history_inserted = 0
    try:
        history_rows = _query_spot_history(token, subs, skus, locations, os_type)
        points: list[dict[str, Any]] = []

        for row in history_rows:
            sku = row.get("skuName", "")
            region = row.get("location", "")
            row_os = row.get("osType", os_type)
            spot_prices = row.get("spotPrices", [])

            if not isinstance(spot_prices, list):
                logger.warning(
                    "spotPrices is not a list for %s/%s: type=%s",
                    sku,
                    region,
                    type(spot_prices).__name__,
                )
                continue

            skipped_points = 0
            for point in spot_prices:
                ts = point.get("timestamp")
                price = point.get("unitPrice")
                if ts is None or price is None:
                    skipped_points += 1
                    continue
                points.append(
                    {
                        "tenant_id": tenant_id,
                        "subscription_id": None,
                        "sku_name": sku.lower(),
                        "region": region.lower(),
                        "os_type": row_os.lower(),
                        "price_usd": float(price),
                        "timestamp_utc": ts,
                        "raw": point,
                    }
                )

        total_skipped = sum(
            1
            for row in history_rows
            for point in (row.get("spotPrices") or [])
            if isinstance(row.get("spotPrices"), list)
            and (point.get("timestamp") is None or point.get("unitPrice") is None)
        )
        if total_skipped:
            logger.warning(
                "Spot history: %d data points skipped (missing timestamp or unitPrice)",
                total_skipped,
            )

        with get_conn() as conn:
            history_inserted = upsert_spot_price_points(conn, points)
            conn.commit()

        result.inserted += history_inserted
        logger.info(
            "Spot history: %d ARG rows → %d points parsed → %d inserted, %d skipped",
            len(history_rows),
            len(points),
            history_inserted,
            total_skipped,
        )

    except Exception as exc:
        logger.exception("Spot history ingestion failed")
        result.errors.append({"source": "spot_history", "message": str(exc)})

    with get_conn() as conn:
        status_h = (
            "ok" if not any(e.get("source") == "spot_history" for e in result.errors) else "error"
        )
        finish_ingest_run(
            conn,
            run_id_history,
            status_h,
            {"inserted": history_inserted},
        )
        conn.commit()

    # --- Eviction rates ---
    eviction_inserted = 0
    try:
        eviction_rows = _query_eviction_rates(token, subs, skus, locations)
        rates: list[dict[str, Any]] = []

        skipped_eviction = 0
        for row in eviction_rows:
            sku = row.get("skuName", "")
            region = row.get("location", "")
            rate = row.get("spotEvictionRate", "")
            if not sku or not region or not rate:
                skipped_eviction += 1
                continue
            rates.append(
                {
                    "tenant_id": tenant_id,
                    "subscription_id": None,
                    "sku_name": sku.lower(),
                    "region": region.lower(),
                    "eviction_rate": rate,
                    "raw": row,
                }
            )

        with get_conn() as conn:
            eviction_inserted = upsert_spot_eviction_rates(conn, rates)
            conn.commit()

        if skipped_eviction:
            logger.warning(
                "Eviction rates: %d rows skipped (missing skuName, location, or rate)",
                skipped_eviction,
            )

        result.updated += eviction_inserted
        logger.info(
            "Eviction rates: %d ARG rows → %d parsed → %d inserted, %d skipped",
            len(eviction_rows),
            len(rates),
            eviction_inserted,
            skipped_eviction,
        )

    except Exception as exc:
        logger.exception("Eviction rate ingestion failed")
        result.errors.append({"source": "spot_eviction", "message": str(exc)})

    with get_conn() as conn:
        status_e = (
            "ok" if not any(e.get("source") == "spot_eviction" for e in result.errors) else "error"
        )
        finish_ingest_run(
            conn,
            run_id_eviction,
            status_e,
            {"inserted": eviction_inserted},
        )
        conn.commit()
