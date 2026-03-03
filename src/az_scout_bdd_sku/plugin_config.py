"""Plugin configuration loader.

Reads from the TOML file pointed to by ``AZ_SCOUT_BDD_SKU_CONFIG`` or
from ``~/.config/az-scout/bdd-sku.toml``.  Returns typed dataclasses.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".config" / "az-scout" / "bdd-sku.toml"


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "azscout"
    user: str = "azscout"
    password: str = "azscout"
    sslmode: str = "disable"
    auth_method: str = "password"  # "password" or "msi"
    client_id: str = ""  # MSI client ID (optional, for user-assigned identity)

    @property
    def dsn(self) -> str:
        from urllib.parse import quote

        if self.auth_method == "msi":
            # Token-based auth: no password in DSN, supplied via callback
            return (
                f"host={self.host} port={self.port} dbname={self.dbname}"
                f" user={self.user} sslmode={self.sslmode}"
            )
        return (
            f"postgresql://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{self.dbname}"
            f"?sslmode={self.sslmode}"
        )


@dataclass
class PluginConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


_config: PluginConfig | None = None


def _load_from_env() -> PluginConfig | None:
    """Build config from ``POSTGRES_*`` environment variables.

    Returns ``None`` when the env vars are not set so the caller
    can fall back to TOML / defaults.
    """
    host = os.environ.get("POSTGRES_HOST")
    if not host:
        return None

    db_cfg = DatabaseConfig(
        host=host,
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "azscout"),
        user=os.environ.get("POSTGRES_USER", "azscout"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
        auth_method=os.environ.get("POSTGRES_AUTH_METHOD", "password"),
        client_id=os.environ.get("AZURE_CLIENT_ID", ""),
    )
    logger.info("Loaded plugin config from POSTGRES_* environment variables")
    return PluginConfig(database=db_cfg)


def load_config() -> PluginConfig:
    """Load configuration from env vars, TOML file, or defaults (in that order)."""
    # 1. Environment variables (Container Apps, Docker, CI)
    env_cfg = _load_from_env()
    if env_cfg is not None:
        return env_cfg

    # 2. TOML config file
    path_str = os.environ.get("AZ_SCOUT_BDD_SKU_CONFIG", "")
    path = Path(path_str) if path_str else _DEFAULT_PATH

    if not path.is_file():
        logger.debug("Config file not found at %s – using defaults", path)
        return PluginConfig()

    import tomllib

    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    db_raw = raw.get("database", {})

    db_cfg = DatabaseConfig(
        host=db_raw.get("host", "localhost"),
        port=int(db_raw.get("port", 5432)),
        dbname=db_raw.get("dbname", "azscout"),
        user=db_raw.get("user", "azscout"),
        password=db_raw.get("password", "azscout"),
        sslmode=db_raw.get("sslmode", "disable"),
    )

    logger.info("Loaded plugin config from %s", path)
    return PluginConfig(database=db_cfg)


def get_config() -> PluginConfig:
    """Return cached config, loading on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
