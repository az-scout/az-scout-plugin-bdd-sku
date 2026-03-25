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
_FALLBACK_PATH = Path("/tmp/az-scout/bdd-sku.toml")


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

        # TCP keepalive + connect timeout prevent indefinite hangs
        # when Azure PG drops idle connections or firewall kills sockets.
        _CONN_OPTS = (
            "connect_timeout=10"
            " keepalives=1 keepalives_idle=30"
            " keepalives_interval=10 keepalives_count=5"
        )

        if self.auth_method == "msi":
            return (
                f"host={self.host} port={self.port} dbname={self.dbname}"
                f" user={self.user} sslmode={self.sslmode} {_CONN_OPTS}"
            )
        return (
            f"postgresql://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{self.dbname}"
            f"?sslmode={self.sslmode}&connect_timeout=10"
            f"&keepalives=1&keepalives_idle=30"
            f"&keepalives_interval=10&keepalives_count=5"
        )


@dataclass
class PluginConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    api_base_url: str = ""


_config: PluginConfig | None = None


def _load_from_env() -> PluginConfig | None:
    """Build config from ``POSTGRES_*`` environment variables.

    Returns ``None`` when the env vars are not set so the caller
    can fall back to TOML / defaults.
    """
    host = os.environ.get("POSTGRES_HOST")
    api_url = os.environ.get("BDD_SKU_API_URL", "")
    if not host and not api_url:
        return None

    db_cfg = DatabaseConfig(
        host=host or "localhost",
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "azscout"),
        user=os.environ.get("POSTGRES_USER", "azscout"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
        auth_method=os.environ.get("POSTGRES_AUTH_METHOD", "password"),
        client_id=os.environ.get("AZURE_CLIENT_ID", ""),
    )
    logger.info("Loaded plugin config from environment variables")
    return PluginConfig(database=db_cfg, api_base_url=api_url)


def load_config() -> PluginConfig:
    """Load configuration from env vars, TOML file, or defaults (in that order)."""
    # 1. Environment variables (Container Apps, Docker, CI)
    env_cfg = _load_from_env()
    if env_cfg is not None:
        return env_cfg

    # 2. TOML config file
    path_str = os.environ.get("AZ_SCOUT_BDD_SKU_CONFIG", "")
    candidates = [Path(path_str)] if path_str else [_DEFAULT_PATH, _FALLBACK_PATH]

    path: Path | None = None
    for candidate in candidates:
        if candidate.is_file():
            path = candidate
            break

    if path is None:
        logger.debug("Config file not found in %s – using defaults", candidates)
        return PluginConfig()

    import tomllib

    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    db_raw = raw.get("database", {})
    api_raw = raw.get("api", {})

    db_cfg = DatabaseConfig(
        host=db_raw.get("host", "localhost"),
        port=int(db_raw.get("port", 5432)),
        dbname=db_raw.get("dbname", "azscout"),
        user=db_raw.get("user", "azscout"),
        password=db_raw.get("password", "azscout"),
        sslmode=db_raw.get("sslmode", "disable"),
    )

    logger.info("Loaded plugin config from %s", path)
    return PluginConfig(database=db_cfg, api_base_url=api_raw.get("base_url", ""))


def get_config() -> PluginConfig:
    """Return cached config, loading on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def is_configured() -> bool:
    """Return True if the API base URL is set."""
    return bool(get_config().api_base_url)


def _build_toml_content(url: str, existing_lines: list[str]) -> str:
    """Return TOML content with the ``[api]`` section set to *url*."""
    new_lines: list[str] = []
    in_api_section = False
    api_written = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped == "[api]":
            in_api_section = True
            new_lines.append("[api]")
            new_lines.append(f'base_url = "{url}"')
            api_written = True
            continue
        if in_api_section:
            if stripped.startswith("[") and stripped != "[api]":
                in_api_section = False
                new_lines.append(line)
            continue
        new_lines.append(line)

    if not api_written:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("[api]")
        new_lines.append(f'base_url = "{url}"')

    return "\n".join(new_lines) + "\n"


def _write_toml(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_api_url(url: str) -> None:
    """Persist *url* to the TOML config file and update the cached config."""
    global _config

    # Normalize: strip trailing slash
    url = url.rstrip("/")

    path_str = os.environ.get("AZ_SCOUT_BDD_SKU_CONFIG", "")
    path = Path(path_str) if path_str else _DEFAULT_PATH

    # Read existing content (preserve other sections)
    existing_lines: list[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    content = _build_toml_content(url, existing_lines)

    try:
        _write_toml(path, content)
        logger.info("Saved API URL to %s", path)
    except PermissionError:
        logger.warning("Cannot write to %s – falling back to %s", path, _FALLBACK_PATH)
        _write_toml(_FALLBACK_PATH, content)
        logger.info("Saved API URL to %s", _FALLBACK_PATH)

    # Update cached config
    if _config is not None:
        _config.api_base_url = url
    else:
        _config = load_config()
        _config.api_base_url = url
