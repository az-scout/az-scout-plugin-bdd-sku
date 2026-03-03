"""Job configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobConfig:
    """Immutable configuration for the SKU mapper batch job."""

    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    pg_sslmode: str
    dataset_name: str
    log_level: str
    dry_run: bool
    batch_size: int

    @classmethod
    def from_env(cls) -> JobConfig:
        """Build configuration from environment variables."""
        return cls(
            pg_host=os.environ.get("PGHOST", "localhost"),
            pg_port=int(os.environ.get("PGPORT", "5432")),
            pg_database=os.environ.get("PGDATABASE", "az_scout"),
            pg_user=os.environ.get("PGUSER", "postgres"),
            pg_password=os.environ.get("PGPASSWORD", ""),
            pg_sslmode=os.environ.get("PGSSLMODE", "disable"),
            dataset_name=os.environ.get("JOB_DATASET_NAME", "sku_mapper"),
            log_level=os.environ.get("LOG_LEVEL", "info").upper(),
            dry_run=os.environ.get("DRY_RUN", "false").lower() in ("true", "1", "yes"),
            batch_size=int(os.environ.get("BATCH_SIZE", "1000")),
        )

    def safe_repr(self) -> dict[str, str | int | bool]:
        """Return a dict safe for logging (password masked)."""
        return {
            "pg_host": self.pg_host,
            "pg_port": self.pg_port,
            "pg_database": self.pg_database,
            "pg_user": self.pg_user,
            "pg_password": "***",
            "pg_sslmode": self.pg_sslmode,
            "dataset_name": self.dataset_name,
            "log_level": self.log_level,
            "dry_run": self.dry_run,
            "batch_size": self.batch_size,
        }
