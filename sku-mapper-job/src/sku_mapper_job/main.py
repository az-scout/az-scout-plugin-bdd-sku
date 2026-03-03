"""SKU Mapper Job — main entry point.

Reads distinct VM SKU names from PostgreSQL, parses naming conventions,
derives family / category / workload tags, and upserts into ``vm_sku_catalog``.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from sku_mapper_job.config import JobConfig
from sku_mapper_job.db import (
    complete_job_run,
    connect,
    create_job_run,
    ensure_schema,
    fail_job_run,
    fetch_distinct_skus,
    upsert_batch,
)
from sku_mapper_job.parser import SkuInfo, parse_sku

log = logging.getLogger("sku_mapper_job")


# ---------------------------------------------------------------------------
# Structured JSON log formatter
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line on stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge extra keys added via `extra={…}`
        for key in (
            "table",
            "chunk_start",
            "chunk_size",
            "total",
            "items_read",
            "items_written",
            "duration_ms",
            "errors_count",
            "parse_ok",
            "parse_skip",
            "examples",
            "config",
            "run_id",
            "dataset",
            "dry_run",
            "skus_count",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
        return json.dumps(payload, default=str)


def _setup_logging(level: str) -> None:
    """Configure structured JSON logging on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------


def _sku_info_to_row(info: SkuInfo) -> dict[str, Any]:
    """Convert a ``SkuInfo`` dataclass to a dict for the UPSERT query."""
    return {
        "sku_name": info.sku_name,
        "tier": info.tier,
        "family": info.family,
        "series": info.series,
        "version": info.version,
        "vcpus": info.vcpus,
        "sku_type": info.sku_type,
        "category": info.category,
        "workload_tags": info.workload_tags if info.workload_tags else None,
        "source": "naming",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run() -> None:
    """Execute the SKU mapper batch job end-to-end."""
    config = JobConfig.from_env()
    _setup_logging(config.log_level)

    log.info("job_started", extra={"config": config.safe_repr()})
    t0 = time.monotonic()

    conn = connect(config)
    run_id: str | None = None
    items_read = 0
    items_written = 0
    errors_count = 0

    try:
        ensure_schema(conn)
        run_id = create_job_run(conn, config.dataset_name)
        log.info("job_run_created", extra={"run_id": run_id, "dataset": config.dataset_name})

        # -- Fetch all distinct SKU names ------------------------------------
        skus = fetch_distinct_skus(conn)
        items_read = len(skus)
        log.info("skus_fetched", extra={"skus_count": items_read})

        # -- Parse each SKU --------------------------------------------------
        rows: list[dict[str, Any]] = []
        parse_ok = 0
        parse_skip = 0
        for sku_name in sorted(skus):
            info = parse_sku(sku_name)
            if info.tier is not None:
                parse_ok += 1
            else:
                parse_skip += 1
            rows.append(_sku_info_to_row(info))

        log.info(
            "parsing_complete",
            extra={"parse_ok": parse_ok, "parse_skip": parse_skip},
        )

        # -- Dry-run mode: log examples and exit -----------------------------
        if config.dry_run:
            examples = rows[:10]
            log.info(
                "dry_run_examples",
                extra={"dry_run": True, "examples": examples},
            )
            log.info(
                "dry_run_complete",
                extra={
                    "items_read": items_read,
                    "items_written": 0,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "errors_count": 0,
                },
            )
            complete_job_run(conn, run_id, items_read, 0)
            return

        # -- Upsert ----------------------------------------------------------
        items_written = upsert_batch(conn, rows, config.batch_size)
        complete_job_run(conn, run_id, items_read, items_written)

    except Exception:
        errors_count = 1
        if run_id:
            try:
                fail_job_run(conn, run_id, _exc_message())
            except Exception:
                log.exception("failed_to_update_job_run")
        log.exception("job_failed")
        raise
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "job_finished",
            extra={
                "items_read": items_read,
                "items_written": items_written,
                "duration_ms": duration_ms,
                "errors_count": errors_count,
            },
        )
        conn.close()


def _exc_message() -> str:
    """Return the current exception message (or empty string)."""
    exc = sys.exc_info()[1]
    return str(exc) if exc else ""
