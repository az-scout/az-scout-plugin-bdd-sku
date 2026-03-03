"""Database helpers using psycopg 3 (synchronous)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg

from sku_mapper_job.config import JobConfig
from sku_mapper_job.sql import (
    CREATE_VM_SKU_CATALOG,
    INSERT_JOB_RUN,
    SELECT_DISTINCT_SKUS,
    UPDATE_JOB_RUN_ERROR,
    UPDATE_JOB_RUN_OK,
    UPSERT_SKU_CATALOG,
)

log = logging.getLogger(__name__)


def connect(config: JobConfig) -> psycopg.Connection[Any]:
    """Open a synchronous psycopg 3 connection using password auth."""
    conn: psycopg.Connection[Any] = psycopg.connect(
        host=config.pg_host,
        port=config.pg_port,
        dbname=config.pg_database,
        user=config.pg_user,
        password=config.pg_password,
        sslmode=config.pg_sslmode,
        autocommit=False,
    )
    return conn


def ensure_schema(conn: psycopg.Connection[Any]) -> None:
    """Create the ``vm_sku_catalog`` table and indexes if they don't exist."""
    with conn.cursor() as cur:
        cur.execute(CREATE_VM_SKU_CATALOG)
    conn.commit()
    log.info("schema_ensured", extra={"table": "vm_sku_catalog"})


def fetch_distinct_skus(conn: psycopg.Connection[Any]) -> set[str]:
    """Return the deduplicated set of VM SKU names from all source tables."""
    with conn.cursor() as cur:
        cur.execute(SELECT_DISTINCT_SKUS)
        rows = cur.fetchall()
    return {row[0] for row in rows if row[0]}


def upsert_batch(
    conn: psycopg.Connection[Any],
    rows: list[dict[str, Any]],
    batch_size: int = 1000,
) -> int:
    """Upsert parsed SKU rows into ``vm_sku_catalog`` in chunks.

    Returns the total number of rows written.
    """
    total = 0
    with conn.cursor() as cur:
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            cur.executemany(UPSERT_SKU_CATALOG, chunk)
            conn.commit()
            total += len(chunk)
            log.debug(
                "upsert_chunk",
                extra={"chunk_start": start, "chunk_size": len(chunk), "total": total},
            )
    return total


# -- Job tracking helpers ------------------------------------------------------


def create_job_run(conn: psycopg.Connection[Any], dataset: str) -> str:
    """Insert a new ``job_runs`` record with status 'running'. Returns the run_id."""
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(INSERT_JOB_RUN, {"run_id": run_id, "dataset": dataset})
    conn.commit()
    return run_id


def complete_job_run(
    conn: psycopg.Connection[Any],
    run_id: str,
    items_read: int,
    items_written: int,
) -> None:
    """Mark a job run as 'ok'."""
    with conn.cursor() as cur:
        cur.execute(
            UPDATE_JOB_RUN_OK,
            {"run_id": run_id, "items_read": items_read, "items_written": items_written},
        )
    conn.commit()


def fail_job_run(
    conn: psycopg.Connection[Any],
    run_id: str,
    error_message: str,
) -> None:
    """Mark a job run as 'error'."""
    with conn.cursor() as cur:
        cur.execute(
            UPDATE_JOB_RUN_ERROR,
            {"run_id": run_id, "error_message": error_message[:4000]},
        )
    conn.commit()
