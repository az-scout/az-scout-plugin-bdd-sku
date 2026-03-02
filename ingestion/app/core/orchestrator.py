"""Job Orchestrator.

Coordinates the execution of data collectors with a shared PostgreSQL
connection.  Mirrors the orchestrator pattern from az-pricing-history
but uses PGClientManager instead of ADXClientManager.
"""

import logging
import sys
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from collectors.azure_pricing_collector import AzurePricingCollector
from collectors.azure_spot_collector import AzureSpotCollector
from shared.config import ConfigManager
from shared.pg_client import PGClientManager


class JobOrchestrator:
    """Orchestrates execution of data collectors."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.logger.info("=== Initializing Job Orchestrator ===")

        # Configuration
        self.config_manager = ConfigManager()
        self.config_manager.validate_global_config()
        self.config_manager.log_diagnostics()

        # Job metadata
        self.job_id = str(uuid.uuid4())
        self.job_datetime = datetime.now(UTC)
        global_cfg = self.config_manager.get_global_config()
        self.job_type: str = global_cfg["job_type"]

        # PostgreSQL client
        self.pg_client_manager = PGClientManager(
            host=global_cfg["postgres_host"],
            port=global_cfg["postgres_port"],
            dbname=global_cfg["postgres_db"],
            user=global_cfg["postgres_user"],
            password=global_cfg["postgres_password"],
            sslmode=global_cfg.get("postgres_sslmode", "disable"),
        )

        # State
        self.collectors: dict[str, AzurePricingCollector | AzureSpotCollector] = {}
        self.results: list[dict[str, Any]] = []

        self.logger.info("Job Orchestrator initialized – Job ID: %s", self.job_id)

    # ------------------------------------------------------------------
    # Collector lifecycle
    # ------------------------------------------------------------------

    def _initialize_collectors(self, collector_names: list[str]) -> None:
        self.logger.info("Initializing collectors: %s", collector_names)

        for name in collector_names:
            self.logger.info("Initializing %s …", name)
            collector_config = self.config_manager.get_collector_config(name)

            if name == "azure_pricing":
                collector: AzurePricingCollector | AzureSpotCollector = (
                    AzurePricingCollector(
                        job_id=self.job_id,
                        job_datetime=self.job_datetime,
                        job_type=self.job_type,
                        config=collector_config,
                    )
                )
            elif name == "azure_spot":
                collector = AzureSpotCollector(
                    job_id=self.job_id,
                    job_datetime=self.job_datetime,
                    job_type=self.job_type,
                    config=collector_config,
                )
            else:
                raise ValueError(f"Unknown collector: {name}")

            self.collectors[name] = collector
            self.logger.info("%s collector created", name)

    def run_collectors(self, collector_names: list[str] | None = None) -> list[dict[str, Any]]:
        if collector_names is None:
            collector_names = self.config_manager.get_collectors_to_run()

        self.logger.info("Starting job with collectors: %s", collector_names)
        start_time = datetime.now(UTC)

        try:
            pg_conn = self.pg_client_manager.get_connection()
            self.logger.info("PostgreSQL connection ready")

            self._initialize_collectors(collector_names)

            for name in collector_names:
                collector_start = datetime.now(UTC)
                run_id = str(uuid.uuid4())

                # Record job start in job_runs
                try:
                    with pg_conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO job_runs "
                            "(run_id, dataset, status, started_at_utc) "
                            "VALUES (%s, %s, %s, %s)",
                            (run_id, name, "running", collector_start),
                        )
                    pg_conn.commit()
                except Exception:
                    self.logger.warning("Failed to insert job_runs row for %s", name)
                    try:
                        pg_conn.rollback()
                    except Exception:
                        pass

                try:
                    self.logger.info("Running %s …", name)
                    result = self.collectors[name].run(pg_conn)
                    self.results.append(result)
                    self.logger.info("%s completed successfully", name)

                    # Update job_runs → ok
                    try:
                        with pg_conn.cursor() as cur:
                            cur.execute(
                                "UPDATE job_runs SET status = %s, "
                                "finished_at_utc = %s, "
                                "items_read = %s, items_written = %s "
                                "WHERE run_id = %s",
                                (
                                    "ok",
                                    datetime.now(UTC),
                                    result.get("total_collected", 0),
                                    result.get("total_ingested", 0),
                                    run_id,
                                ),
                            )
                        pg_conn.commit()
                    except Exception:
                        self.logger.warning("Failed to update job_runs for %s", name)
                        try:
                            pg_conn.rollback()
                        except Exception:
                            pass

                except Exception as exc:
                    collector_end = datetime.now(UTC)
                    duration = (collector_end - collector_start).total_seconds()
                    self.results.append(
                        {
                            "collector_name": name,
                            "status": "error",
                            "job_id": self.job_id,
                            "job_datetime": self.job_datetime.isoformat(),
                            "job_type": self.job_type,
                            "start_time": collector_start.isoformat(),
                            "end_time": collector_end.isoformat(),
                            "duration_seconds": duration,
                            "error": str(exc),
                        }
                    )
                    self.logger.error("%s failed: %s", name, exc)

                    # Update job_runs → error
                    try:
                        with pg_conn.cursor() as cur:
                            cur.execute(
                                "UPDATE job_runs SET status = %s, "
                                "finished_at_utc = %s, "
                                "error_message = %s "
                                "WHERE run_id = %s",
                                ("error", collector_end, str(exc), run_id),
                            )
                        pg_conn.commit()
                    except Exception:
                        self.logger.warning("Failed to update job_runs for %s", name)
                        try:
                            pg_conn.rollback()
                        except Exception:
                            pass

            end_time = datetime.now(UTC)
            total_duration = (end_time - start_time).total_seconds()

            ok = [r for r in self.results if r["status"] == "success"]
            fail = [r for r in self.results if r["status"] == "error"]

            self.logger.info("Job completed in %.1fs", total_duration)
            self.logger.info("Successful: %d – Failed: %d", len(ok), len(fail))

            if fail:
                self.logger.warning(
                    "Some collectors failed: %s",
                    [r["collector_name"] for r in fail],
                )

            return self.results

        except Exception as exc:
            end_time = datetime.now(UTC)
            total_duration = (end_time - start_time).total_seconds()
            self.logger.error("Job failed after %.1fs: %s", total_duration, exc)
            raise

    def get_job_summary(self) -> dict[str, Any]:
        ok = [r for r in self.results if r["status"] == "success"]
        fail = [r for r in self.results if r["status"] == "error"]
        return {
            "job_id": self.job_id,
            "job_datetime": self.job_datetime.isoformat(),
            "job_type": self.job_type,
            "total_collectors": len(self.results),
            "successful_collectors": len(ok),
            "failed_collectors": len(fail),
            "total_items_collected": sum(r.get("total_collected", 0) for r in ok),
            "total_items_ingested": sum(r.get("total_ingested", 0) for r in ok),
            "collectors": self.results,
        }

    def cleanup(self) -> None:
        try:
            self.pg_client_manager.close()
        except Exception:
            self.logger.exception("Error during cleanup")


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main() -> None:
    """Main entry point for the one-shot ingestion job."""
    import os

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    logger = logging.getLogger(__name__)
    logger.info("=== Pricing Data Ingestion Starting ===")
    logger.info("Timestamp: %s", datetime.now(UTC).isoformat())

    orchestrator: JobOrchestrator | None = None
    exit_code = 0

    try:
        orchestrator = JobOrchestrator()
        orchestrator.run_collectors()

        summary = orchestrator.get_job_summary()
        logger.info("=== Job Summary ===")
        logger.info("Job ID: %s", summary["job_id"])
        logger.info("Total collectors: %d", summary["total_collectors"])
        logger.info("Successful: %d", summary["successful_collectors"])
        logger.info("Failed: %d", summary["failed_collectors"])
        logger.info("Total items collected: %d", summary["total_items_collected"])
        logger.info("Total items ingested: %d", summary["total_items_ingested"])

        if summary["failed_collectors"] > 0:
            logger.warning("Some collectors failed – check logs for details")
            exit_code = 1
        else:
            logger.info("SUCCESS: All collectors completed")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        exit_code = 130

    except Exception as exc:
        logger.error("FATAL ERROR: %s", exc)
        traceback.print_exc(file=sys.stderr)
        exit_code = 1

    finally:
        if orchestrator:
            orchestrator.cleanup()

    sys.exit(exit_code)
