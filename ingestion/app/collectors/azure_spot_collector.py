"""Azure Spot Pricing & Eviction Data Collector.

Collects VM spot eviction rates and spot price history from the
Azure Resource Graph REST API and ingests into PostgreSQL.

Uses two Resource Graph queries against the ``SpotResources`` table:
- ``microsoft.compute/skuspotevictionrate/location``   → ``spot_eviction_rates``
- ``microsoft.compute/skuspotpricehistory/ostype/location`` → ``spot_price_history``
"""

import json
import time
from datetime import datetime
from typing import Any

import psycopg2  # type: ignore[import-untyped]
import requests
from azure.identity import DefaultAzureCredential  # type: ignore[import-untyped]
from core.base_collector import BaseCollector

# Resource Graph REST API endpoint
_RESOURCE_GRAPH_URL = (
    "https://management.azure.com/providers/Microsoft.ResourceGraph"
    "/resources?api-version=2024-04-01"
)

_EVICTION_QUERY = """\
SpotResources
| where type =~ 'microsoft.compute/skuspotevictionrate/location'
| project skuName = tostring(sku.name),
          location = tostring(location),
          evictionRate = tostring(properties.evictionRate)
"""

_PRICE_HISTORY_QUERY = """\
SpotResources
| where type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'
| project skuName = tostring(sku.name),
          osType  = tostring(properties.osType),
          location = tostring(location),
          spotPrices = properties.spotPrices
"""


class AzureSpotCollector(BaseCollector):
    """Azure Resource Graph spot data collector."""

    def __init__(
        self,
        job_id: str,
        job_datetime: datetime,
        job_type: str,
        config: dict[str, Any],
    ) -> None:
        super().__init__(job_id, job_datetime, job_type, config)

        # Retry configuration
        self.api_retry_attempts = int(config.get("api_retry_attempts", 3))
        self.api_retry_delay = float(config.get("api_retry_delay", 2.0))
        self.pg_retry_attempts = 5
        self.pg_retry_delay = 5  # seconds

        # Page delay to respect Resource Graph rate limits (15 req / 5s / tenant)
        self.page_delay = 0.5

        # Eviction-only mode: skip price history query (for hourly historization)
        self.eviction_only = str(config.get("eviction_only", "false")).lower() in (
            "true", "1", "yes",
        )

        self.logger.info(
            "AzureSpotCollector init – max_items=%s, retry=%d/%.1fs, eviction_only=%s",
            "unlimited" if self.max_items == float("inf") else int(self.max_items),
            self.api_retry_attempts,
            self.api_retry_delay,
            self.eviction_only,
        )

    # ------------------------------------------------------------------
    # Abstract property implementations
    # ------------------------------------------------------------------

    @property
    def collector_name(self) -> str:
        return "azure_spot"

    @property
    def table_name(self) -> str:
        # Two tables are managed; return the primary one for lifecycle logs.
        return "spot_eviction_rates"

    @property
    def table_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS spot_eviction_rates (
                job_id       TEXT NOT NULL,
                job_datetime TIMESTAMPTZ NOT NULL,
                sku_name     TEXT NOT NULL,
                region       TEXT NOT NULL,
                eviction_rate TEXT NOT NULL,
                UNIQUE (sku_name, region, job_id)
            );
            CREATE TABLE IF NOT EXISTS spot_price_history (
                job_id        TEXT,
                job_datetime  TIMESTAMPTZ,
                sku_name      TEXT NOT NULL,
                os_type       TEXT NOT NULL,
                region        TEXT NOT NULL,
                price_history JSONB NOT NULL,
                UNIQUE (sku_name, os_type, region)
            );
        """

    def validate_config(self) -> None:
        # No required configuration beyond what BaseCollector checks.
        pass

    # ------------------------------------------------------------------
    # Authentication & subscription discovery
    # ------------------------------------------------------------------

    def _get_auth_header(self) -> dict[str, str]:
        """Acquire a Bearer token via DefaultAzureCredential."""
        credential = DefaultAzureCredential()
        token = credential.get_token("https://management.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}"}



    # ------------------------------------------------------------------
    # Resource Graph helpers
    # ------------------------------------------------------------------

    def _resource_graph_query(
        self,
        session: requests.Session,
        headers: dict[str, str],
        query: str,
    ) -> list[dict[str, Any]]:
        """Execute a paginated Resource Graph query and return all rows.

        SpotResources is global reference data — the request body must NOT
        include ``subscriptions`` or ``managementGroups`` (tenant-level scope).
        """
        all_rows: list[dict[str, Any]] = []
        skip_token: str | None = None

        while True:
            body: dict[str, Any] = {
                "query": query,
                "options": {
                    "resultFormat": "objectArray",
                },
            }
            if skip_token:
                body["options"]["$skipToken"] = skip_token

            self.logger.debug("Resource Graph request body: %s", json.dumps(body, indent=2))
            data = self._post_resource_graph(session, headers, body)

            rows: list[dict[str, Any]] = data.get("data", [])
            total_records = data.get("totalRecords", "?")
            all_rows.extend(rows)
            self.logger.info(
                "Resource Graph page: %d rows (total so far: %d, totalRecords: %s)",
                len(rows),
                len(all_rows),
                total_records,
            )

            if not rows:
                break

            if len(all_rows) >= self.max_items:
                self.logger.info("Reached max_items limit (%s)", int(self.max_items))
                break

            skip_token = data.get("$skipToken")
            if not skip_token:
                break

            time.sleep(self.page_delay)

        return all_rows

    def _post_resource_graph(
        self,
        session: requests.Session,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST to Resource Graph with retry logic."""
        last_exc: Exception | None = None

        for attempt in range(self.api_retry_attempts):
            try:
                resp = session.post(
                    _RESOURCE_GRAPH_URL,
                    headers={**headers, "Content-Type": "application/json"},
                    json=body,
                    timeout=60,
                )

                if resp.status_code == 200:
                    return resp.json()  # type: ignore[no-any-return]

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    self.logger.warning("Rate limited (429), waiting %ds", retry_after)
                    time.sleep(retry_after)
                    last_exc = Exception(f"Rate limited (429) on attempt {attempt + 1}")
                    continue

                if 500 <= resp.status_code < 600:
                    self.logger.warning(
                        "Server error %d on attempt %d", resp.status_code, attempt + 1
                    )
                    last_exc = Exception(f"Server error {resp.status_code}: {resp.text[:200]}")
                else:
                    raise Exception(
                        f"Resource Graph request failed with {resp.status_code}: {resp.text[:500]}"
                    )

            except requests.exceptions.RequestException as exc:
                self.logger.warning("Network error on attempt %d: %s", attempt + 1, exc)
                last_exc = exc

            if attempt < self.api_retry_attempts - 1:
                self.logger.info("Waiting %.1fs before retry…", self.api_retry_delay)
                time.sleep(self.api_retry_delay)

        raise Exception(
            f"Resource Graph request failed after {self.api_retry_attempts} attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # PostgreSQL ingestion
    # ------------------------------------------------------------------

    def _ingest_eviction_batch(
        self,
        pg_conn: Any,
        items: list[dict[str, Any]],
        batch_id: str,
    ) -> bool:
        """Upsert a batch of eviction rate rows."""
        if not items:
            return True

        last_exc: Exception | None = None

        for attempt in range(self.pg_retry_attempts):
            try:
                with pg_conn.cursor() as cur:
                    for item in items:
                        cur.execute(
                            """
                            INSERT INTO spot_eviction_rates
                                (job_id, job_datetime, sku_name, region, eviction_rate)
                            VALUES
                                (%(job_id)s, %(job_datetime)s, %(sku_name)s,
                                 %(region)s, %(eviction_rate)s)
                            ON CONFLICT (sku_name, region, job_id) DO NOTHING
                            """,
                            {
                                "job_id": self.job_id,
                                "job_datetime": self.job_datetime.isoformat(),
                                "sku_name": item["skuName"],
                                "region": item["location"],
                                "eviction_rate": item["evictionRate"],
                            },
                        )
                pg_conn.commit()
                self.logger.debug("Eviction batch %s ingested (%d rows)", batch_id, len(items))
                return True

            except psycopg2.Error as exc:
                pg_conn.rollback()
                last_exc = exc
                self.logger.warning(
                    "PG error eviction batch %s attempt %d/%d: %s",
                    batch_id, attempt + 1, self.pg_retry_attempts, exc,
                )
                if attempt < self.pg_retry_attempts - 1:
                    time.sleep(self.pg_retry_delay)

            except Exception as exc:
                pg_conn.rollback()
                last_exc = exc
                self.logger.warning(
                    "Ingestion error eviction batch %s attempt %d/%d: %s",
                    batch_id, attempt + 1, self.pg_retry_attempts, exc,
                )
                if attempt < self.pg_retry_attempts - 1:
                    time.sleep(self.pg_retry_delay)

        self.logger.error(
            "Failed to ingest eviction batch %s after %d attempts: %s",
            batch_id, self.pg_retry_attempts, last_exc,
        )
        return False

    def _ingest_price_history_batch(
        self,
        pg_conn: Any,
        items: list[dict[str, Any]],
        batch_id: str,
    ) -> bool:
        """Upsert a batch of price history rows (JSONB array per SKU×region×OS)."""
        if not items:
            return True

        last_exc: Exception | None = None

        for attempt in range(self.pg_retry_attempts):
            try:
                with pg_conn.cursor() as cur:
                    for item in items:
                        price_history = item.get("spotPrices", [])
                        cur.execute(
                            """
                            INSERT INTO spot_price_history
                                (job_id, job_datetime, sku_name, os_type, region, price_history)
                            VALUES
                                (%(job_id)s, %(job_datetime)s, %(sku_name)s,
                                 %(os_type)s, %(region)s, %(price_history)s)
                            ON CONFLICT (sku_name, os_type, region)
                            DO UPDATE SET
                                job_id        = EXCLUDED.job_id,
                                job_datetime  = EXCLUDED.job_datetime,
                                price_history = EXCLUDED.price_history
                            """,
                            {
                                "job_id": self.job_id,
                                "job_datetime": self.job_datetime.isoformat(),
                                "sku_name": item["skuName"],
                                "os_type": item["osType"],
                                "region": item["location"],
                                "price_history": json.dumps(price_history),
                            },
                        )
                pg_conn.commit()
                self.logger.debug(
                    "Price history batch %s ingested (%d rows)", batch_id, len(items)
                )
                return True

            except psycopg2.Error as exc:
                pg_conn.rollback()
                last_exc = exc
                self.logger.warning(
                    "PG error price batch %s attempt %d/%d: %s",
                    batch_id, attempt + 1, self.pg_retry_attempts, exc,
                )
                if attempt < self.pg_retry_attempts - 1:
                    time.sleep(self.pg_retry_delay)

            except Exception as exc:
                pg_conn.rollback()
                last_exc = exc
                self.logger.warning(
                    "Ingestion error price batch %s attempt %d/%d: %s",
                    batch_id, attempt + 1, self.pg_retry_attempts, exc,
                )
                if attempt < self.pg_retry_attempts - 1:
                    time.sleep(self.pg_retry_delay)

        self.logger.error(
            "Failed to ingest price batch %s after %d attempts: %s",
            batch_id, self.pg_retry_attempts, last_exc,
        )
        return False

    # ------------------------------------------------------------------
    # Main collection loop
    # ------------------------------------------------------------------

    def collect_data(self, pg_conn: Any) -> int:
        """Query Resource Graph for spot eviction rates and price history."""
        self.logger.info("Acquiring Azure credentials …")
        headers = self._get_auth_header()

        session = requests.Session()
        session.timeout = 60

        total_ingested = 0

        # --- eviction rates ---
        self.logger.info("Querying spot eviction rates …")
        eviction_rows = self._resource_graph_query(
            session, headers, _EVICTION_QUERY
        )
        self.logger.info("Received %d eviction rate rows", len(eviction_rows))

        batch_size = 500
        for i in range(0, len(eviction_rows), batch_size):
            batch = eviction_rows[i : i + batch_size]
            batch_id = f"eviction-{i // batch_size + 1}"
            ok = self._ingest_eviction_batch(pg_conn, batch, batch_id)
            if ok:
                total_ingested += len(batch)
            else:
                raise Exception(f"Failed to ingest {batch_id} ({len(batch)} items)")

        self.logger.info("Eviction rates ingested: %d rows", len(eviction_rows))

        # --- price history (skipped in eviction-only mode) ---
        if self.eviction_only:
            self.logger.info("Eviction-only mode – skipping price history query")
            price_rows = []
        else:
            self.logger.info("Querying spot price history …")
            price_rows = self._resource_graph_query(
                session, headers, _PRICE_HISTORY_QUERY
            )
            self.logger.info("Received %d price history rows", len(price_rows))

            for i in range(0, len(price_rows), batch_size):
                batch = price_rows[i : i + batch_size]
                batch_id = f"price-{i // batch_size + 1}"
                ok = self._ingest_price_history_batch(pg_conn, batch, batch_id)
                if ok:
                    total_ingested += len(batch)
                else:
                    raise Exception(f"Failed to ingest {batch_id} ({len(batch)} items)")

            self.logger.info("Price history ingested: %d rows", len(price_rows))

        self.total_collected = len(eviction_rows) + len(price_rows)
        self.total_ingested = total_ingested
        self.logger.info("Collection completed: %d total items ingested", total_ingested)

        return total_ingested
