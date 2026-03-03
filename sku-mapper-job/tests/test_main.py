"""Integration-style tests for the main job (mocked DB)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sku_mapper_job.config import JobConfig
from sku_mapper_job.main import _sku_info_to_row, run
from sku_mapper_job.parser import parse_sku


class TestSkuInfoToRow:
    """Test the helper that converts SkuInfo → dict for upsert."""

    def test_standard_sku(self) -> None:
        info = parse_sku("Standard_D2s_v5")
        row = _sku_info_to_row(info)
        assert row["sku_name"] == "Standard_D2s_v5"
        assert row["tier"] == "Standard"
        assert row["family"] == "D"
        assert row["vcpus"] == 2
        assert row["version"] == "v5"
        assert row["category"] == "general"
        assert row["source"] == "naming"
        assert isinstance(row["workload_tags"], list)

    def test_non_standard_sku(self) -> None:
        info = parse_sku("Basic_A1")
        row = _sku_info_to_row(info)
        assert row["sku_name"] == "Basic_A1"
        assert row["tier"] is None
        assert row["family"] is None
        assert row["category"] == "other"
        assert row["workload_tags"] is None


class TestJobConfig:
    """Test configuration from env vars."""

    def test_from_env_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = JobConfig.from_env()
        assert cfg.pg_host == "localhost"
        assert cfg.pg_port == 5432
        assert cfg.pg_database == "az_scout"
        assert cfg.dry_run is False
        assert cfg.batch_size == 1000
        assert cfg.dry_run is False

    def test_from_env_custom(self) -> None:
        env = {
            "PGHOST": "db.example.com",
            "PGPORT": "5433",
            "PGDATABASE": "testdb",
            "PGUSER": "testuser",
            "PGPASSWORD": "secret",
            "PGSSLMODE": "require",
            "DRY_RUN": "true",
            "LOG_LEVEL": "debug",
            "BATCH_SIZE": "500",
            "JOB_DATASET_NAME": "custom_ds",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = JobConfig.from_env()
        assert cfg.pg_host == "db.example.com"
        assert cfg.pg_port == 5433
        assert cfg.dry_run is True
        assert cfg.log_level == "DEBUG"
        assert cfg.batch_size == 500
        assert cfg.dataset_name == "custom_ds"

    def test_safe_repr_masks_password(self) -> None:
        env = {"PGPASSWORD": "supersecret"}
        with patch.dict("os.environ", env, clear=True):
            cfg = JobConfig.from_env()
        safe = cfg.safe_repr()
        assert safe["pg_password"] == "***"


class TestDryRun:
    """Test the dry-run path (no real DB writes)."""

    @patch("sku_mapper_job.main.connect")
    @patch("sku_mapper_job.main.ensure_schema")
    @patch("sku_mapper_job.main.create_job_run", return_value="test-run-id")
    @patch("sku_mapper_job.main.fetch_distinct_skus", return_value={"Standard_D2s_v5", "Basic_A1"})
    @patch("sku_mapper_job.main.upsert_batch")
    @patch("sku_mapper_job.main.complete_job_run")
    def test_dry_run_skips_upsert(
        self,
        mock_complete: MagicMock,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
        mock_create_run: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"DRY_RUN": "true", "LOG_LEVEL": "warning"}
        with patch.dict("os.environ", env, clear=True):
            run()

        mock_upsert.assert_not_called()
        mock_complete.assert_called_once()
        # complete_job_run(conn, run_id, items_read, items_written=0)
        call_args = mock_complete.call_args
        assert call_args[0][3] == 0  # items_written is the 4th positional arg

    @patch("sku_mapper_job.main.connect")
    @patch("sku_mapper_job.main.ensure_schema")
    @patch("sku_mapper_job.main.create_job_run", return_value="test-run-id")
    @patch(
        "sku_mapper_job.main.fetch_distinct_skus",
        return_value={"Standard_D2s_v5", "Standard_E8ds_v5"},
    )
    @patch("sku_mapper_job.main.upsert_batch", return_value=2)
    @patch("sku_mapper_job.main.complete_job_run")
    def test_normal_run_calls_upsert(
        self,
        mock_complete: MagicMock,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
        mock_create_run: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"DRY_RUN": "false", "LOG_LEVEL": "warning"}
        with patch.dict("os.environ", env, clear=True):
            run()

        mock_upsert.assert_called_once()
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args
        assert call_args[0][3] == 2  # items_written is the 4th positional arg

    @patch("sku_mapper_job.main.connect")
    @patch("sku_mapper_job.main.ensure_schema", side_effect=RuntimeError("DB down"))
    @patch("sku_mapper_job.main.fail_job_run")
    def test_error_path(
        self,
        mock_fail: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"LOG_LEVEL": "warning"}
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(RuntimeError, match="DB down"),
        ):
            run()
