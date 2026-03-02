"""Tests for the az-scout-plugin-bdd-sku plugin routes and tools."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from az_scout_bdd_sku.routes import router


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    _app.include_router(router, prefix="/plugins/bdd-sku")
    return _app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestStatus:
    """Tests for GET /plugins/bdd-sku/status."""

    @patch("az_scout_bdd_sku.routes.is_healthy", new_callable=AsyncMock, return_value=True)
    @patch("az_scout_bdd_sku.routes.get_conn")
    def test_status_returns_count_and_last_run(
        self,
        mock_conn_ctx: MagicMock,
        mock_healthy: AsyncMock,
        client: TestClient,
    ) -> None:
        run_id = uuid4()
        started = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        finished = datetime(2026, 1, 15, 10, 5, 0, tzinfo=UTC)

        mock_conn = AsyncMock()
        call_count = 0

        async def fake_execute(sql: str, *args: object) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            cursor = AsyncMock()
            if call_count <= 4:
                # COUNT(*) queries (3 tables) + regions/skus count
                cursor.fetchone = AsyncMock(return_value=(42,))
            elif "job_runs" in sql:
                # _last_run_for → job_runs query
                cursor.fetchone = AsyncMock(
                    return_value=(run_id, "ok", started, finished, 1000, 950, None)
                )
            else:
                cursor.fetchone = AsyncMock(return_value=None)
            return cursor

        mock_conn.execute = fake_execute
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn_ctx.return_value = ctx

        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_connected"] is True
        assert data["retail_prices_count"] == 42
        assert data["last_run"]["status"] == "ok"
        assert data["last_run"]["items_written"] == 950

    @patch("az_scout_bdd_sku.routes.is_healthy", new_callable=AsyncMock, return_value=False)
    def test_status_db_down(
        self,
        mock_healthy: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_connected"] is False
        assert data["retail_prices_count"] == -1
        assert data["last_run"] is None

    @patch("az_scout_bdd_sku.routes.is_healthy", new_callable=AsyncMock, return_value=True)
    @patch("az_scout_bdd_sku.routes.get_conn")
    def test_status_no_runs_yet(
        self,
        mock_conn_ctx: MagicMock,
        mock_healthy: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_conn = AsyncMock()

        async def fake_execute(sql: str, *args: object) -> AsyncMock:
            cursor = AsyncMock()
            if "COUNT(*)" in sql and "job_runs" not in sql and "MAX" not in sql:
                cursor.fetchone = AsyncMock(return_value=(0,))
            elif "COUNT(DISTINCT" in sql:
                cursor.fetchone = AsyncMock(return_value=(0, 0))
            else:
                # job_runs → no rows; fallback MAX → (None, 0)
                cursor.fetchone = AsyncMock(return_value=None)
            return cursor

        mock_conn.execute = fake_execute
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn_ctx.return_value = ctx

        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_connected"] is True
        assert data["retail_prices_count"] == 0
        assert data["last_run"] is None

    @patch("az_scout_bdd_sku.routes.is_healthy", new_callable=AsyncMock, return_value=True)
    @patch("az_scout_bdd_sku.routes.get_conn")
    def test_status_fallback_from_data_table(
        self,
        mock_conn_ctx: MagicMock,
        mock_healthy: AsyncMock,
        client: TestClient,
    ) -> None:
        """When job_runs is empty, last_run falls back to MAX(job_datetime)."""
        fallback_dt = datetime(2026, 2, 20, 8, 0, 0, tzinfo=UTC)
        mock_conn = AsyncMock()

        async def fake_execute(sql: str, *args: object) -> AsyncMock:
            cursor = AsyncMock()
            if "job_runs" in sql:
                # No job_runs rows
                cursor.fetchone = AsyncMock(return_value=None)
            elif "MAX(job_datetime)" in sql:
                # Fallback: data table has rows
                cursor.fetchone = AsyncMock(return_value=(fallback_dt, 500))
            elif "COUNT(DISTINCT" in sql:
                cursor.fetchone = AsyncMock(return_value=(10, 200))
            else:
                # COUNT(*) queries
                cursor.fetchone = AsyncMock(return_value=(500,))
            return cursor

        mock_conn.execute = fake_execute
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn_ctx.return_value = ctx

        resp = client.get("/plugins/bdd-sku/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_run"] is not None
        assert data["last_run"]["status"] == "ok"
        assert data["last_run"]["items_written"] == 500
        assert data["last_run"]["run_id"] is None
        assert data["last_run_spot"] is not None


class TestCacheStatusTool:
    """Tests for the MCP cache_status tool."""

    @patch("az_scout_bdd_sku.tools.is_healthy", new_callable=AsyncMock, return_value=True)
    @patch("az_scout_bdd_sku.tools.get_conn")
    def test_cache_status_returns_data(
        self,
        mock_conn_ctx: MagicMock,
        mock_healthy: AsyncMock,
    ) -> None:
        from az_scout_bdd_sku.tools import cache_status

        mock_conn = AsyncMock()
        call_count = 0

        async def fake_execute(sql: str, *args: object) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            cursor = AsyncMock()
            if call_count == 1:
                cursor.fetchone = AsyncMock(return_value=(100,))
            else:
                cursor.fetchone = AsyncMock(return_value=None)
            return cursor

        mock_conn.execute = fake_execute
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn_ctx.return_value = ctx

        result = cache_status()
        assert result["db_connected"] is True
        assert result["retail_prices_count"] == 100

    @patch("az_scout_bdd_sku.tools.is_healthy", new_callable=AsyncMock, return_value=False)
    def test_cache_status_db_down(self, mock_healthy: AsyncMock) -> None:
        from az_scout_bdd_sku.tools import cache_status

        result = cache_status()
        assert result["db_connected"] is False
        assert result["retail_prices_count"] == -1
