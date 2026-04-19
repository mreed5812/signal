"""CoinGecko free-tier OHLCV ingestion.

Free-tier limits:
- 30 requests/minute, no API key required.
- Historical OHLCV: full history via /coins/{id}/market_chart/range (daily granularity).
- What happens when limits are hit: HTTP 429 — tenacity retries with
  exponential back-off up to 5 attempts, then raises and marks job failed.

Symbols fetched (configurable via COINGECKO_SYMBOLS env var):
  bitcoin, ethereum, solana, binancecoin, cardano
"""

import os
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

_BASE_URL = "https://api.coingecko.com/api/v3"
_DEFAULT_SYMBOLS = ["bitcoin", "ethereum", "solana", "binancecoin", "cardano"]
_SYMBOL_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binancecoin": "BNB",
    "cardano": "ADA",
}

_INSERT_SQL = text(
    """
    INSERT INTO prices (symbol, interval, timestamp, open, high, low, close, volume, source)
    VALUES (:symbol, :interval, :timestamp, :open, :high, :low, :close, :volume, :source)
    ON CONFLICT (symbol, interval, timestamp) DO NOTHING
    """
)


class CoinGeckoSource(DataSource):
    """Fetches OHLCV data for top-10 crypto coins from CoinGecko."""

    name = "coingecko"

    def __init__(self) -> None:
        raw = os.getenv("COINGECKO_SYMBOLS", "")
        self.symbols: list[str] = (
            [s.strip() for s in raw.split(",")] if raw else _DEFAULT_SYMBOLS
        )
        self.api_key: str = os.getenv("COINGECKO_API_KEY", "")

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"x-cg-demo-api-key": self.api_key}
        return {}

    @retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5))
    def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict:  # type: ignore[type-arg]
        url = f"{_BASE_URL}{path}"
        resp = requests.get(url, params=params, headers=self._headers(), timeout=15)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            log.warning("rate_limited", source=self.name, retry_after=retry_after)
            time.sleep(retry_after)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def _fetch_range(self, coin_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Fetch daily prices via market_chart/range endpoint.

        Note: the free-tier market_chart endpoint doesn't return true OHLCV;
        open/high/low are all set to the close price. Upgrade to CoinGecko Pro
        for /coins/{id}/ohlc with real candles.
        """
        data = self._get(
            f"/coins/{coin_id}/market_chart/range",
            params={
                "vs_currency": "usd",
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
            },
        )
        prices = data.get("prices", [])
        volumes = dict(data.get("total_volumes", []))

        rows = []
        for ts_ms, price in prices:
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            rows.append(
                {
                    "symbol": _SYMBOL_MAP.get(coin_id, coin_id.upper()),
                    "interval": "1d",
                    "timestamp": ts,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": volumes.get(ts_ms, 0.0),
                    "source": self.name,
                }
            )
        return pd.DataFrame(rows)

    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        for coin_id in self.symbols:
            log.info("fetching_historical", coin=coin_id)
            df = self._fetch_range(coin_id, start, end)
            frames.append(df)
            time.sleep(2)  # respect the free-tier rate limit
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        """Fetch the last 2 days so we always have at least one fresh row."""
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=2)
        return self.fetch_historical(start, end)

    def store(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        records = df.to_dict("records")
        with get_connection() as conn:
            result = conn.execute(_INSERT_SQL, records)
        return result.rowcount
