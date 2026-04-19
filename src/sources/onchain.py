"""Blockchain.com on-chain metrics ingestion.

Free-tier limits:
- No API key required; no documented rate limit.
- Full history available (years of daily data).
- What happens on failure: tenacity retries; metric is skipped if it consistently fails.

Metrics fetched (all daily):
  hash-rate, n-transactions, transaction-fees, n-unique-addresses
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.common.database import get_connection
from src.common.logging import get_logger
from src.sources.base import DataSource

log = get_logger(__name__)

_BASE_URL = "https://api.blockchain.info/charts"
_METRICS = [
    "hash-rate",
    "n-transactions",
    "transaction-fees",
    "n-unique-addresses",
]


class OnChainSource(DataSource):
    """Fetches daily on-chain Bitcoin metrics from blockchain.com."""

    name = "onchain"

    @retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5))
    def _fetch_metric(self, metric: str, start: datetime) -> list[dict]:  # type: ignore[type-arg]
        resp = requests.get(
            f"{_BASE_URL}/{metric}",
            params={
                "timespan": "all",
                "sampled": "true",
                "metadata": "false",
                "cors": "true",
                "format": "json",
                "start": start.strftime("%Y-%m-%d"),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("values", [])  # type: ignore[no-any-return]

    def _to_df(self, metric: str, values: list[dict]) -> pd.DataFrame:  # type: ignore[type-arg]
        rows = []
        for v in values:
            ts = datetime.fromtimestamp(v["x"], tz=timezone.utc)
            rows.append({"metric_name": metric, "timestamp": ts, "value": float(v["y"])})
        return pd.DataFrame(rows)

    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        for metric in _METRICS:
            log.info("fetching_onchain", metric=metric)
            try:
                values = self._fetch_metric(metric, start)
                df = self._to_df(metric, values)
                frames.append(df)
            except Exception as exc:
                log.error("onchain_metric_failed", metric=metric, error=str(exc))
            time.sleep(1)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=3)
        return self.fetch_historical(start, end)

    def store(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        records = df.to_dict("records")
        sql = text(
            """
            INSERT INTO onchain_metrics (metric_name, timestamp, value)
            VALUES (:metric_name, :timestamp, :value)
            ON CONFLICT (metric_name, timestamp) DO NOTHING
            """
        )
        with get_connection() as conn:
            result = conn.execute(sql, records)
        return result.rowcount
