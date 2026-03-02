"""Tests for retail pricing service (mocked DB + HTTP)."""

from unittest.mock import MagicMock, patch

from az_scout_plugin_bdd_sku.service.retail_pricing import (
    DEFAULT_SKU_SAMPLE,
    _fetch_sku_price,
    warm_retail_pricing,
)


class TestDefaultSkuSample:
    """Verify the curated SKU sample."""

    def test_contains_expected_skus(self) -> None:
        assert "standard_d2s_v5" in DEFAULT_SKU_SAMPLE
        assert "standard_f4s_v2" in DEFAULT_SKU_SAMPLE
        assert len(DEFAULT_SKU_SAMPLE) >= 5

    def test_all_lowercase(self) -> None:
        for sku in DEFAULT_SKU_SAMPLE:
            assert sku == sku.lower()


class TestFetchSkuPrice:
    """Tests for _fetch_sku_price (mocked core pricing)."""

    @patch(
        "az_scout.azure_api.pricing.get_sku_pricing_detail",
        return_value={"paygo": 0.096, "spot": 0.012},
    )
    def test_returns_pricing_data(self, mock_detail: MagicMock) -> None:
        result = _fetch_sku_price("eastus", "standard_d2s_v5", "USD")
        assert result is not None
        assert result["paygo"] == 0.096

    @patch(
        "az_scout.azure_api.pricing.get_sku_pricing_detail",
        side_effect=Exception("Connection error"),
    )
    def test_returns_none_on_error(self, mock_detail: MagicMock) -> None:
        result = _fetch_sku_price("eastus", "standard_d2s_v5", "USD")
        assert result is None


class TestWarmRetailPricing:
    """Tests for warm_retail_pricing (mocked DB + HTTP)."""

    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.release_advisory_lock")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.try_advisory_lock", return_value=True)
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.create_ingest_run")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.finish_ingest_run")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.get_conn")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.is_retail_fresh", return_value=False)
    @patch(
        "az_scout_plugin_bdd_sku.service.retail_pricing.upsert_retail_price",
        return_value="inserted",
    )
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing._fetch_sku_price")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing._fetch_regions")
    def test_warm_inserts_prices(
        self,
        mock_regions: MagicMock,
        mock_price: MagicMock,
        mock_upsert: MagicMock,
        mock_fresh: MagicMock,
        mock_conn: MagicMock,
        mock_finish: MagicMock,
        mock_create: MagicMock,
        mock_lock: MagicMock,
        mock_unlock: MagicMock,
    ) -> None:
        import uuid

        mock_regions.return_value = [
            {"name": "eastus", "displayName": "East US"},
        ]
        mock_price.return_value = {"paygo": 0.096}
        mock_create.return_value = uuid.uuid4()

        # Mock context manager for get_conn
        mock_connection = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_connection)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = warm_retail_pricing(
            currency="USD",
            sku_sample=["standard_d2s_v5"],
            ttl_hours=12,
        )

        assert result.dataset == "retail"
        assert result.regions_count == 1
        assert result.skus_count == 1

    @patch("az_scout_plugin_bdd_sku.service.retail_pricing.get_conn")
    @patch("az_scout_plugin_bdd_sku.service.retail_pricing._fetch_regions")
    def test_warm_handles_region_fetch_failure(
        self,
        mock_regions: MagicMock,
        mock_conn: MagicMock,
    ) -> None:
        mock_regions.side_effect = Exception("Network error")
        mock_connection = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_connection)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = warm_retail_pricing()
        assert result.ok is False
        assert len(result.errors) > 0
        assert result.errors[0]["source"] == "regions"
