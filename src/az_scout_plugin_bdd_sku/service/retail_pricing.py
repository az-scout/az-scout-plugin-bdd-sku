"""Retail pricing warm + get functions.

Retrieves VM retail pricing from the core az-scout pricing module
and caches results in PostgreSQL.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from az_scout_plugin_bdd_sku.config import get_config
from az_scout_plugin_bdd_sku.db import (
    create_ingest_run,
    finish_ingest_run,
    get_conn,
    is_retail_fresh,
    release_advisory_lock,
    try_advisory_lock,
    upsert_retail_price,
)
from az_scout_plugin_bdd_sku.models import WarmResult
from az_scout_plugin_bdd_sku.service.locks import compute_lock_key

logger = logging.getLogger(__name__)

DEFAULT_SKU_SAMPLE = [
    "standard_d2s_v5",
    "standard_d4s_v5",
    "standard_d8s_v5",
    "standard_e2ds_v5",
    "standard_e4ds_v5",
    "standard_f4s_v2",
    "standard_f8s_v2",
    "standard_l8s_v3",
]


def _fetch_regions(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Retrieve region list by calling core az_scout directly (in-process)."""
    from az_scout.azure_api.discovery import list_regions

    logger.info("Fetching regions from core az_scout (tenant=%s)", tenant_id or "default")
    result: list[dict[str, Any]] = list_regions(tenant_id=tenant_id)
    region_names = [r.get("name", "?") for r in result[:5]]
    logger.info(
        "Got %d regions (first 5: %s)",
        len(result),
        ", ".join(region_names),
    )
    return result


def _fetch_sku_price(
    region: str,
    sku_name: str,
    currency: str,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Fetch pricing for one SKU+region via core pricing module (in-process)."""
    from az_scout.azure_api.pricing import get_sku_pricing_detail

    logger.debug("API call: get_sku_pricing_detail(%s, %s, %s)", region, sku_name, currency)
    try:
        result: dict[str, Any] = get_sku_pricing_detail(region, sku_name, currency)
        paygo = result.get("paygo")
        if paygo is None:
            logger.warning(
                "No paygo price returned for %s/%s — full response: %s",
                region,
                sku_name,
                result,
            )
        else:
            logger.debug("API result: %s/%s → paygo=%s", region, sku_name, paygo)
        return result
    except Exception as exc:
        logger.warning(
            "Failed to fetch price for %s in %s: %s (type=%s)",
            sku_name,
            region,
            exc,
            type(exc).__name__,
        )
        return None


def warm_retail_pricing(
    currency: str = "USD",
    sku_sample: list[str] | None = None,
    ttl_hours: int | None = None,
    force: bool = False,
    tenant_id: str | None = None,
) -> WarmResult:
    """Warm the retail pricing cache.

    For each region and each SKU in the sample, fetches pricing from the
    core /api/sku-pricing endpoint and stores it in PostgreSQL.
    """
    cfg = get_config()
    ttl = ttl_hours or cfg.cache.retail_ttl_hours
    skus = sku_sample or DEFAULT_SKU_SAMPLE
    started = datetime.now(UTC).isoformat()

    result = WarmResult(ok=False, dataset="retail", started_at=started)

    logger.info(
        "Starting retail warm: currency=%s, skus=%s, ttl=%dh, force=%s, tenant=%s",
        currency,
        skus,
        ttl,
        force,
        tenant_id or "default",
    )

    # Fetch regions
    try:
        regions = _fetch_regions(tenant_id)
    except Exception as exc:
        result.errors.append({"source": "regions", "message": str(exc)})
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    region_names = [r["name"] for r in regions]
    result.regions_count = len(region_names)
    result.skus_count = len(skus)

    # Create ingest run
    with get_conn() as conn:
        run_id = create_ingest_run(conn, "retail")
        conn.commit()
    result.run_id = str(run_id)

    # Advisory lock to prevent duplicate warm runs
    lock_key = compute_lock_key("retail", currency, tenant_id or "")
    with get_conn() as lock_conn:
        if not try_advisory_lock(lock_conn, lock_key):
            result.errors.append(
                {
                    "source": "lock",
                    "message": "Another retail warm run is in progress",
                }
            )
            result.finished_at = datetime.now(UTC).isoformat()
            with get_conn() as conn:
                finish_ingest_run(conn, run_id, "skipped", {"reason": "lock_held"})
                conn.commit()
            return result

        try:
            _do_retail_warm(
                result,
                region_names,
                skus,
                currency,
                ttl,
                force,
                tenant_id,
            )
        finally:
            release_advisory_lock(lock_conn, lock_key)

    result.ok = len(result.errors) == 0
    result.finished_at = datetime.now(UTC).isoformat()

    status = "ok" if result.ok else "error"
    logger.info(
        "Retail warm finished: status=%s, inserted=%d, updated=%d, "
        "no_price=%d, skipped_fresh=%d, errors=%d, run_id=%s",
        status,
        result.inserted,
        result.updated,
        result.no_price_count,
        result.skipped_fresh,
        len(result.errors),
        result.run_id,
    )
    with get_conn() as conn:
        finish_ingest_run(conn, run_id, status, result.to_dict())
        conn.commit()

    return result


def _do_retail_warm(
    result: WarmResult,
    region_names: list[str],
    skus: list[str],
    currency: str,
    ttl: int,
    force: bool,
    tenant_id: str | None,
) -> None:
    """Fetch and upsert retail prices concurrently."""
    cfg = get_config()
    concurrency = cfg.cache.concurrency_limit
    # Reserve 1 pool slot for the advisory lock connection held by the caller.
    max_workers = max(1, concurrency - 1)

    tasks: list[tuple[str, str]] = []
    # Pre-check freshness to skip
    if not force:
        with get_conn() as conn:
            for region in region_names:
                for sku in skus:
                    if is_retail_fresh(conn, currency, region, sku):
                        result.skipped_fresh += 1
                    else:
                        tasks.append((region, sku))
    else:
        tasks = [(r, s) for r in region_names for s in skus]

    logger.info(
        "Retail warm: %d tasks to fetch, %d skipped (fresh)",
        len(tasks),
        result.skipped_fresh,
    )

    def _fetch_and_store(region: str, sku: str) -> str:
        data = _fetch_sku_price(region, sku, currency, tenant_id)
        if data is None:
            logger.warning("Fetch returned None for %s/%s (exception upstream)", region, sku)
            return "error"

        paygo = data.get("paygo")
        if paygo is None:
            logger.warning(
                "No paygo in response for %s/%s — keys=%s",
                region,
                sku,
                list(data.keys()),
            )
            return "no_price"

        with get_conn() as conn:
            action = upsert_retail_price(
                conn,
                tenant_id=tenant_id,
                currency=currency,
                region=region,
                sku_name=sku,
                price_hourly=float(paygo),
                ttl_hours=ttl,
                raw=data,
            )
            conn.commit()
        return action

    total = len(tasks)
    done = 0
    with ThreadPoolExecutor(max_workers=min(total or 1, max_workers)) as pool:
        futures = {pool.submit(_fetch_and_store, r, s): (r, s) for r, s in tasks}
        for future in as_completed(futures):
            region, sku = futures[future]
            done += 1
            try:
                action = future.result()
                if action == "inserted":
                    result.inserted += 1
                elif action == "updated":
                    result.updated += 1
                elif action == "no_price":
                    result.no_price_count += 1
                elif action == "error":
                    result.errors.append(
                        {
                            "source": f"{region}/{sku}",
                            "message": "Failed to fetch price",
                        }
                    )
                if done % 50 == 0 or done == total:
                    logger.info(
                        "Progress: %d/%d done (%d ins, %d upd, %d no_price, %d err)",
                        done,
                        total,
                        result.inserted,
                        result.updated,
                        result.no_price_count,
                        len(result.errors),
                    )
            except Exception as exc:
                result.errors.append(
                    {
                        "source": f"{region}/{sku}",
                        "message": str(exc),
                    }
                )
