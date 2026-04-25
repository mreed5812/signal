"""Abstract base class that every data source must implement.

To add a new source:
1. Create a module under src/sources/.
2. Subclass DataSource and implement fetch_historical, fetch_latest, and store.
3. Register the source in src/scheduler/entrypoint.sh.
"""

import abc
from datetime import datetime, timedelta, timezone

import pandas as pd


class DataSource(abc.ABC):
    """Contract for all pipeline data sources."""

    name: str  # unique identifier used in logs, metrics, and DB records

    @abc.abstractmethod
    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Pull historical data in [start, end].

        Must be idempotent — calling twice returns the same rows without side effects.
        """

    @abc.abstractmethod
    def fetch_latest(self) -> pd.DataFrame:
        """Pull the most recent available data point(s)."""

    @abc.abstractmethod
    def store(self, df: pd.DataFrame) -> int:
        """Upsert df into the database. Returns the number of rows written."""

    def run(self, historical: bool = False) -> None:
        """Entry-point called by the scheduler.

        Fetches data and stores it, recording Prometheus metrics.
        historical=True triggers a full backfill via fetch_historical.
        """
        import time

        from src.common.logging import get_logger
        from src.common.metrics import (
            ingestion_errors,
            job_duration_seconds,
            job_runs,
            last_ingestion_timestamp,
            rows_ingested,
        )

        log = get_logger(self.name)
        start_ts = time.time()
        try:
            if historical:
                end_dt = datetime.now(tz=timezone.utc)
                start_dt = end_dt - timedelta(days=180)
                df = self.fetch_historical(start_dt, end_dt)
            else:
                df = self.fetch_latest()

            count = self.store(df)
            rows_ingested.labels(source=self.name, symbol="all").inc(count)
            last_ingestion_timestamp.labels(source=self.name).set(time.time())
            job_runs.labels(job=self.name, status="success").inc()
            log.info("ingestion_complete", rows=count, historical=historical)
        except Exception as exc:
            ingestion_errors.labels(source=self.name).inc()
            job_runs.labels(job=self.name, status="failure").inc()
            log.error("ingestion_failed", error=str(exc), exc_info=True)
            raise
        finally:
            elapsed = time.time() - start_ts
            job_duration_seconds.labels(job=self.name).observe(elapsed)
