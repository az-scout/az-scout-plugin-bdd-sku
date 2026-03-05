"""Lightweight plugin routes for the BDD-SKU tab.

Mounted at ``/plugins/bdd-sku/`` by az-scout.  Provides:
- ``GET /status``         — proxy to the standalone API
- ``GET /settings``       — current plugin settings
- ``PUT /settings``       — update the API base URL
- ``POST /settings/test`` — test connectivity to the API
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from az_scout_bdd_sku.plugin_config import get_config, is_configured, save_api_url

router = APIRouter()


class SettingsPayload(BaseModel):
    api_base_url: str


@router.get("/status", response_model=None)
async def status() -> dict[str, Any] | JSONResponse:
    """Proxy status to the standalone API, or return unconfigured state."""
    if not is_configured():
        return JSONResponse(
            status_code=200,
            content={
                "configured": False,
                "db_connected": False,
                "retail_prices_count": -1,
                "spot_eviction_rates_count": -1,
                "spot_price_history_count": -1,
                "regions_count": 0,
                "spot_skus_count": 0,
                "last_run": None,
                "last_run_spot": None,
            },
        )
    from az_scout_bdd_sku.api_client import get_status

    try:
        data = get_status()
        data["configured"] = True
        return data
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"configured": True, "error": str(exc)},
        )


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """Return the current plugin settings."""
    cfg = get_config()
    return {
        "api_base_url": cfg.api_base_url,
        "is_configured": is_configured(),
    }


@router.post("/settings/update", response_model=None)
async def update_settings(payload: SettingsPayload) -> dict[str, Any] | JSONResponse:
    """Validate and persist the API base URL."""
    url = payload.api_base_url.strip()
    if not url:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "URL cannot be empty"},
        )
    if not url.startswith(("http://", "https://")):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "URL must start with http:// or https://"},
        )
    save_api_url(url)
    return {"ok": True, "api_base_url": get_config().api_base_url}


@router.post("/settings/test")
async def test_settings() -> dict[str, Any]:
    """Test connectivity to the configured API endpoint."""
    if not is_configured():
        return {"ok": False, "error": "API URL not configured"}

    from az_scout_bdd_sku.api_client import test_connection

    url = get_config().api_base_url
    result = test_connection(url)
    return result
