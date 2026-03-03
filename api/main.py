"""Standalone FastAPI application for the BDD-SKU API.

Serves all 15 endpoints (11 v1 + 4 legacy) from
``az_scout_bdd_sku.routes`` in its own Container App,
independently of the az-scout plugin host.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from az_scout_bdd_sku.db import close_pool, ensure_pool, is_healthy
from az_scout_bdd_sku.routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open the DB pool on startup, close it on shutdown."""
    await ensure_pool()
    logger.info("Database pool ready")
    yield
    await close_pool()
    logger.info("Database pool closed")


app = FastAPI(
    title="BDD-SKU API",
    description="Azure VM SKU pricing & spot eviction data",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["infra"])
async def health() -> JSONResponse:
    """Liveness / readiness probe."""
    healthy = await is_healthy()
    status = 200 if healthy else 503
    return JSONResponse(
        status_code=status,
        content={"status": "ok" if healthy else "degraded"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
