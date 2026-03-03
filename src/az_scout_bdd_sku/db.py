"""Lightweight async DB helper for the plugin (query-only).

The plugin needs to read from Postgres for status / cached data queries
and proxy HTTP to the ingestion service for triggering runs.

Supports two auth modes:
- ``password``: classic user/password DSN.
- ``msi``: Azure Managed Identity — acquires an OAuth2 token from
  ``azure.identity.DefaultAzureCredential`` and passes it as the
  password on every new connection.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import psycopg_pool

from az_scout_bdd_sku.plugin_config import DatabaseConfig, get_config

logger = logging.getLogger(__name__)

_pool: psycopg_pool.AsyncConnectionPool | None = None

_PG_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def _make_token_password(cfg: DatabaseConfig) -> str:
    """Acquire a fresh Entra ID token for PostgreSQL."""
    from azure.identity import DefaultAzureCredential

    kwargs: dict[str, str] = {}
    if cfg.client_id:
        kwargs["managed_identity_client_id"] = cfg.client_id

    credential = DefaultAzureCredential(**kwargs)
    token = credential.get_token(_PG_ENTRA_SCOPE)
    return token.token


async def ensure_pool() -> psycopg_pool.AsyncConnectionPool:
    global _pool
    if _pool is None:
        cfg = get_config().database

        kwargs: dict[str, Any] = {
            "conninfo": cfg.dsn,
            "min_size": 1,
            "max_size": 5,
            "open": False,
        }

        if cfg.auth_method == "msi":
            # Supply a fresh token as password on each connection
            kwargs["kwargs"] = {"password": _make_token_password(cfg)}
            logger.info("DB pool configured with Managed Identity auth")
        else:
            logger.info("DB pool configured with password auth")

        _pool = psycopg_pool.AsyncConnectionPool(**kwargs)
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    pool = await ensure_pool()
    async with pool.connection() as conn:
        yield conn


async def is_healthy() -> bool:
    try:
        async with get_conn() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
