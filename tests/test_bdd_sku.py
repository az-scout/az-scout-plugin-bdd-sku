"""Tests for the az-scout-plugin-bdd-sku plugin routes and tools."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from az_scout_bdd_sku.plugin_routes import router


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine in a fresh event loop (avoids conflicts with anyio)."""
    return asyncio.run(coro)


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    _app.include_router(router, prefix="/plugins/bdd-sku")
    return _app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> httpx.Response:  # type: ignore[type-arg]
    """Build a fake httpx.Response for mocking."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.example.com"),
    )
    return resp


def _mock_client(response: httpx.Response | None = None, *, side_effect: Exception | None = None) -> AsyncMock:
    """Return a mock httpx.AsyncClient with a pre-configured `.get()` method."""
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.is_closed = False
    if side_effect:
        mock.get.side_effect = side_effect
    elif response:
        mock.get.return_value = response
    return mock


# ------------------------------------------------------------------
# Plugin routes: GET /status
# ------------------------------------------------------------------


class TestPluginStatus:
    """Tests for GET /plugins/bdd-sku/status."""

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.api_client.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.api_client.get_config")
    @patch("az_scout_bdd_sku.api_client._get_client")
    def test_status_returns_api_data(
        self,
        mock_get_client: MagicMock,
        mock_cfg: MagicMock,
        mock_is_cfg_client: MagicMock,
        mock_is_cfg_routes: MagicMock,
        client: TestClient,
    ) -> None:
        mock_cfg.return_value.api_base_url = "https://api.example.com"
        json_data = {
            "db_connected": True,
            "retail_prices_count": 42,
            "spot_eviction_rates_count": 10,
            "spot_price_history_count": 5,
            "regions_count": 3,
            "spot_skus_count": 20,
            "last_run": {"status": "ok", "items_written": 950},
            "last_run_spot": None,
        }
        mock_get_client.return_value = _mock_client(_mock_httpx_response(json_data))

        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["retail_prices_count"] == 42
        assert data["last_run"]["status"] == "ok"

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=False)
    def test_status_not_configured(
        self,
        mock_is_cfg: MagicMock,
        client: TestClient,
    ) -> None:
        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
        assert data["retail_prices_count"] == -1

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.api_client.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.api_client.get_config")
    @patch("az_scout_bdd_sku.api_client._get_client")
    def test_status_api_error_returns_502(
        self,
        mock_get_client: MagicMock,
        mock_cfg: MagicMock,
        mock_is_cfg_client: MagicMock,
        mock_is_cfg_routes: MagicMock,
        client: TestClient,
    ) -> None:
        mock_cfg.return_value.api_base_url = "https://api.example.com"
        mock_get_client.return_value = _mock_client(side_effect=httpx.ConnectError("refused"))

        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 502
        data = resp.json()
        assert data["configured"] is True
        assert "error" in data


# ------------------------------------------------------------------
# Plugin routes: GET/PUT /settings, POST /settings/test
# ------------------------------------------------------------------


class TestPluginSettings:
    """Tests for /plugins/bdd-sku/settings endpoints."""

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=False)
    @patch("az_scout_bdd_sku.plugin_routes.get_config")
    def test_get_settings(
        self,
        mock_cfg: MagicMock,
        mock_is_cfg: MagicMock,
        client: TestClient,
    ) -> None:
        mock_cfg.return_value.api_base_url = ""
        resp = client.get("/plugins/bdd-sku/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["api_base_url"] == ""
        assert data["is_configured"] is False

    @patch("az_scout_bdd_sku.plugin_routes.save_api_url")
    @patch("az_scout_bdd_sku.plugin_routes.get_config")
    def test_post_settings_valid_url(
        self,
        mock_cfg: MagicMock,
        mock_save: MagicMock,
        client: TestClient,
    ) -> None:
        mock_cfg.return_value.api_base_url = "https://api.example.com"
        resp = client.post(
            "/plugins/bdd-sku/settings/update",
            json={"api_base_url": "https://api.example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        mock_save.assert_called_once_with("https://api.example.com")

    def test_post_settings_empty_url(self, client: TestClient) -> None:
        resp = client.post("/plugins/bdd-sku/settings/update", json={"api_base_url": ""})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_post_settings_invalid_url(self, client: TestClient) -> None:
        resp = client.post("/plugins/bdd-sku/settings/update", json={"api_base_url": "ftp://bad"})
        assert resp.status_code == 400
        assert "http" in resp.json()["error"]

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.plugin_routes.get_config")
    @patch("az_scout_bdd_sku.api_client._get_client")
    def test_test_connection_ok(
        self,
        mock_get_client: MagicMock,
        mock_cfg: MagicMock,
        mock_is_cfg: MagicMock,
        client: TestClient,
    ) -> None:
        mock_cfg.return_value.api_base_url = "https://api.example.com"
        mock_get_client.return_value = _mock_client(_mock_httpx_response({"status": "healthy"}))

        resp = client.post("/plugins/bdd-sku/settings/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    @patch("az_scout_bdd_sku.plugin_routes.is_configured", return_value=False)
    def test_test_connection_not_configured(
        self,
        mock_is_cfg: MagicMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/plugins/bdd-sku/settings/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not configured" in data["error"].lower()


# ------------------------------------------------------------------
# MCP tools
# ------------------------------------------------------------------


class TestCacheStatusTool:
    """Tests for the MCP cache_status tool."""

    @patch("az_scout_bdd_sku.api_client.is_configured", return_value=True)
    @patch("az_scout_bdd_sku.api_client.get_config")
    @patch("az_scout_bdd_sku.api_client._get_client")
    def test_cache_status_returns_data(
        self,
        mock_get_client: MagicMock,
        mock_cfg: MagicMock,
        mock_is_cfg: MagicMock,
    ) -> None:
        from az_scout_bdd_sku.tools import cache_status

        mock_cfg.return_value.api_base_url = "https://api.example.com"
        json_data = {
            "db_connected": True,
            "retail_prices_count": 100,
        }
        mock_get_client.return_value = _mock_client(_mock_httpx_response(json_data))

        result = _run_async(cache_status())
        assert result["db_connected"] is True
        assert result["retail_prices_count"] == 100

    @patch("az_scout_bdd_sku.api_client.is_configured", return_value=False)
    def test_cache_status_not_configured(self, mock_is_cfg: MagicMock) -> None:
        from az_scout_bdd_sku.tools import cache_status

        result = _run_async(cache_status())
        assert "error" in result
        assert "not configured" in result["error"].lower()
