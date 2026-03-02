"""Tests for the v1 read-only API endpoints.

All ``db_api`` functions are mocked — no real PostgreSQL connection
is required.  Each test class covers one endpoint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from az_scout_bdd_sku.routes import router

PREFIX = "/plugins/bdd-sku/v1"


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    _app.include_router(router, prefix="/plugins/bdd-sku")
    return _app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _assert_list_shape(data: dict[str, Any]) -> None:
    """Verify common ListResponse envelope fields."""
    assert "items" in data
    assert "page" in data
    assert "meta" in data
    assert "limit" in data["page"]
    assert "hasMore" in data["page"]
    assert "cursor" in data["page"]
    assert "dataSource" in data["meta"]
    assert "generatedAt" in data["meta"]


# ==================================================================
# /v1/status
# ==================================================================


class TestV1Status:
    @patch(
        "az_scout_bdd_sku.db_api.get_status",
        new_callable=AsyncMock,
        return_value={
            "dbConnected": True,
            "datasets": {
                "retail": {"rowCount": 100, "lastJobDatetimeUtc": None, "lastJobId": None},
                "spotPrices": {"rowCount": 50, "lastJobDatetimeUtc": None, "lastJobId": None},
                "evictionRates": {"rowCount": 25, "lastJobDatetimeUtc": None, "lastJobId": None},
            },
        },
    )
    def test_status_ok(self, mock_get_status: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dbConnected"] is True
        assert data["datasets"]["retail"]["rowCount"] == 100
        assert "version" in data
        assert "meta" in data

    @patch(
        "az_scout_bdd_sku.db_api.get_status",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    )
    def test_status_error(self, mock_get_status: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/status")
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "INTERNAL"


# ==================================================================
# /v1/locations
# ==================================================================


class TestV1Locations:
    @patch(
        "az_scout_bdd_sku.db_api.list_locations",
        new_callable=AsyncMock,
        return_value=[{"name": "eastus"}, {"name": "westus"}],
    )
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/locations?limit=100")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert len(data["items"]) == 2
        assert data["page"]["hasMore"] is False

    @patch(
        "az_scout_bdd_sku.db_api.list_locations",
        new_callable=AsyncMock,
    )
    def test_pagination_has_more(self, mock_fn: AsyncMock, client: TestClient) -> None:
        # Return limit+1 items to trigger hasMore
        mock_fn.return_value = [{"name": f"region-{i}"} for i in range(4)]
        resp = client.get(f"{PREFIX}/locations?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"]["hasMore"] is True
        assert len(data["items"]) == 3
        assert data["page"]["cursor"] is not None

    def test_invalid_limit(self, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/locations?limit=99999")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"

    def test_invalid_cursor(self, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/locations?cursor=INVALID!!!")
        assert resp.status_code == 400


# ==================================================================
# /v1/skus
# ==================================================================


class TestV1Skus:
    @patch(
        "az_scout_bdd_sku.db_api.list_skus",
        new_callable=AsyncMock,
        return_value=[{"skuName": "Standard_D2s_v3"}, {"skuName": "Standard_D4s_v3"}],
    )
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/skus?search=D2s")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert len(data["items"]) == 2
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs.get("search") == "D2s" or mock_fn.call_args[0][2] == "D2s"


# ==================================================================
# /v1/currencies
# ==================================================================


class TestV1Currencies:
    @patch(
        "az_scout_bdd_sku.db_api.list_currencies",
        new_callable=AsyncMock,
        return_value=[{"currencyCode": "EUR"}, {"currencyCode": "USD"}],
    )
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/currencies")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert data["items"][0]["currencyCode"] == "EUR"


# ==================================================================
# /v1/os-types
# ==================================================================


class TestV1OsTypes:
    @patch(
        "az_scout_bdd_sku.db_api.list_os_types",
        new_callable=AsyncMock,
        return_value=[{"osType": "Linux"}, {"osType": "Windows"}],
    )
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/os-types")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert len(data["items"]) == 2


# ==================================================================
# /v1/retail/prices
# ==================================================================


class TestV1RetailPrices:
    @patch("az_scout_bdd_sku.db_api.list_retail_prices", new_callable=AsyncMock)
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {
                "currencyCode": "USD",
                "armRegionName": "eastus",
                "armSkuName": "Standard_D2s_v3",
                "skuId": "sku-1",
                "pricingType": "Consumption",
                "reservationTerm": "",
                "retailPrice": 0.096,
                "unitPrice": 0.096,
                "unitOfMeasure": "1 Hour",
                "effectiveStartDate": "2024-01-01T00:00:00+00:00",
                "jobId": "j-1",
                "jobDatetime": "2026-03-01T00:00:00+00:00",
            },
        ]
        resp = client.get(f"{PREFIX}/retail/prices?region=eastus&currency=USD&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert data["items"][0]["currencyCode"] == "USD"

    def test_invalid_effective_at(self, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/retail/prices?effectiveAt=not-a-date")
        assert resp.status_code == 400
        assert "effectiveAt" in resp.json()["error"]["message"]

    @patch("az_scout_bdd_sku.db_api.list_retail_prices", new_callable=AsyncMock)
    def test_cursor_round_trip(self, mock_fn: AsyncMock, client: TestClient) -> None:
        """Two-page cursor navigation."""
        item = {
            "currencyCode": "USD",
            "armRegionName": "eastus",
            "armSkuName": "Standard_D2s_v3",
            "skuId": "sku-1",
            "pricingType": "Consumption",
            "reservationTerm": "",
            "retailPrice": 0.096,
            "unitPrice": 0.096,
            "unitOfMeasure": "1 Hour",
            "effectiveStartDate": None,
            "jobId": "j-1",
            "jobDatetime": None,
        }
        # First request: return limit+1 items
        mock_fn.return_value = [item, item]
        resp1 = client.get(f"{PREFIX}/retail/prices?limit=1")
        data1 = resp1.json()
        assert data1["page"]["hasMore"] is True
        cursor = data1["page"]["cursor"]

        # Second request with cursor
        mock_fn.return_value = [item]
        resp2 = client.get(f"{PREFIX}/retail/prices?limit=1&cursor={cursor}")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["page"]["hasMore"] is False


# ==================================================================
# /v1/retail/prices/latest
# ==================================================================


class TestV1RetailPricesLatest:
    @patch("az_scout_bdd_sku.db_api.list_retail_prices_latest", new_callable=AsyncMock)
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {
                "currencyCode": "EUR",
                "armRegionName": "westeurope",
                "armSkuName": "Standard_B2s",
                "skuId": "sku-2",
                "pricingType": "Consumption",
                "reservationTerm": "",
                "retailPrice": 0.05,
                "unitPrice": 0.05,
                "unitOfMeasure": "1 Hour",
                "effectiveStartDate": None,
                "jobId": "j-2",
                "jobDatetime": None,
            },
        ]
        resp = client.get(f"{PREFIX}/retail/prices/latest")
        assert resp.status_code == 200
        _assert_list_shape(resp.json())


# ==================================================================
# /v1/spot/prices
# ==================================================================


class TestV1SpotPrices:
    @patch("az_scout_bdd_sku.db_api.list_spot_prices", new_callable=AsyncMock)
    def test_happy_path_raw(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {
                "region": "eastus",
                "skuName": "Standard_D2s_v3",
                "osType": "Linux",
                "jobId": "j-3",
                "jobDatetime": "2026-03-01T00:00:00+00:00",
                "priceHistory": [{"ts": "2026-01-01", "price": 0.01}],
            },
        ]
        resp = client.get(f"{PREFIX}/spot/prices?sample=raw")
        assert resp.status_code == 200
        _assert_list_shape(resp.json())

    def test_sample_hourly_not_implemented(self, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/spot/prices?sample=hourly")
        assert resp.status_code == 501
        assert resp.json()["error"]["code"] == "NOT_IMPLEMENTED"

    def test_sample_invalid(self, client: TestClient) -> None:
        resp = client.get(f"{PREFIX}/spot/prices?sample=invalid")
        assert resp.status_code == 400


# ==================================================================
# /v1/spot/eviction-rates
# ==================================================================


class TestV1EvictionRates:
    @patch("az_scout_bdd_sku.db_api.list_eviction_rates", new_callable=AsyncMock)
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {
                "region": "eastus",
                "skuName": "Standard_D2s_v3",
                "jobId": "j-4",
                "jobDatetimeUtc": "2026-03-01T00:00:00+00:00",
                "evictionRateRaw": "5-10%",
                "evictionRate": None,
            },
        ]
        resp = client.get(f"{PREFIX}/spot/eviction-rates?region=eastus")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert data["items"][0]["region"] == "eastus"


# ==================================================================
# /v1/spot/eviction-rates/series
# ==================================================================


class TestV1EvictionRatesSeries:
    @patch("az_scout_bdd_sku.db_api.eviction_rate_series", new_callable=AsyncMock)
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {"bucketTs": "2026-03-01T00:00:00+00:00", "value": 7.5, "points": 12},
        ]
        resp = client.get(
            f"{PREFIX}/spot/eviction-rates/series"
            "?region=eastus&sku=Standard_D2s_v3&bucket=day&agg=avg"
        )
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert data["meta"]["bucket"] == "day"
        assert data["meta"]["agg"] == "avg"

    def test_invalid_bucket(self, client: TestClient) -> None:
        resp = client.get(
            f"{PREFIX}/spot/eviction-rates/series?region=eastus&sku=Standard_D2s_v3&bucket=INVALID"
        )
        assert resp.status_code == 400

    def test_invalid_agg(self, client: TestClient) -> None:
        resp = client.get(
            f"{PREFIX}/spot/eviction-rates/series"
            "?region=eastus&sku=Standard_D2s_v3&bucket=day&agg=INVALID"
        )
        assert resp.status_code == 400


# ==================================================================
# /v1/spot/eviction-rates/latest
# ==================================================================


class TestV1EvictionRatesLatest:
    @patch("az_scout_bdd_sku.db_api.list_eviction_rates_latest", new_callable=AsyncMock)
    def test_happy_path(self, mock_fn: AsyncMock, client: TestClient) -> None:
        mock_fn.return_value = [
            {
                "region": "westeurope",
                "skuName": "Standard_D4s_v3",
                "jobId": "j-5",
                "jobDatetimeUtc": "2026-03-01T00:00:00+00:00",
                "evictionRateRaw": "10",
                "evictionRate": 10.0,
            },
        ]
        resp = client.get(f"{PREFIX}/spot/eviction-rates/latest?region=westeurope")
        assert resp.status_code == 200
        data = resp.json()
        _assert_list_shape(data)
        assert data["items"][0]["evictionRate"] == 10.0
