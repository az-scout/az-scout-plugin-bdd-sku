"""az-scout SKU DB Cache plugin.

Provides a UI tab, MCP tools and (formerly) API routes for querying
VM retail pricing data cached in a PostgreSQL database by
the companion ingestion CLI.

API routes are now served by a standalone Container App.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from az_scout.plugin_api import ChatMode, TabDefinition
    from fastapi import APIRouter

_STATIC_DIR = Path(__file__).parent / "static"

try:
    __version__ = _pkg_version("az-scout-bdd-sku")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"


class BddSkuPlugin:
    """SKU DB Cache az-scout plugin."""

    name = "bdd-sku"
    version = __version__

    def get_router(self) -> APIRouter | None:
        # Routes are served by the standalone API container.
        return None

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        from az_scout_bdd_sku.tools import (
            cache_status,
            get_spot_eviction_history,
            get_spot_eviction_rates,
            get_spot_price_history,
            v1_eviction_rates,
            v1_eviction_rates_latest,
            v1_list_locations,
            v1_list_skus,
            v1_retail_prices,
            v1_status,
        )

        return [
            cache_status,
            get_spot_eviction_rates,
            get_spot_eviction_history,
            get_spot_price_history,
            v1_status,
            v1_list_locations,
            v1_list_skus,
            v1_retail_prices,
            v1_eviction_rates,
            v1_eviction_rates_latest,
        ]

    def get_static_dir(self) -> Path | None:
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        from az_scout.plugin_api import TabDefinition as _TabDef

        return [
            _TabDef(
                id="bdd-sku",
                label="SKU DB Cache",
                icon="bi bi-database",
                js_entry="js/bdd-sku-tab.js",
                css_entry="css/bdd-sku.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        return None


# Module-level instance — referenced by the entry point
plugin = BddSkuPlugin()
