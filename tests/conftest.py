"""Shared test fixtures for bdd-sku plugin tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import az_scout_bdd_sku.api_client as _api_client


@pytest.fixture(autouse=True)
def _reset_client() -> None:  # type: ignore[misc]
    """Reset the singleton httpx client between tests."""
    _api_client._client = None  # noqa: SLF001


@pytest.fixture(autouse=True)
def _no_retry_delay() -> None:  # type: ignore[misc]
    """Disable retry sleep in tests to avoid hanging."""
    with patch("az_scout_bdd_sku.api_client.asyncio.sleep", return_value=None):
        yield
