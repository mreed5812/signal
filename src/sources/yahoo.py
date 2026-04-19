"""Twelve Data ingestion for macro market instruments.

Replaces yfinance (Yahoo Finance was blocking ARM64/Pi IPs).

Free-tier limits:
- 800 API credits/day, 8 requests/minute.
- Each time_series call costs 1 credit.
- Running once daily uses only 6 credits/day.

Instruments fetched:
  Forex: EUR/USD, USD/JPY
  Commodities: XAU/USD (Gold)
  Indices: SPX (S&P 500), IXIC (NASDAQ), DXY (US Dollar Index)
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.common.config import settings
from src.common.database import get_connection
from src.common.logging import get_logger
from src.sources.base import DataSource

log = get_logger(__name__)

_BASE_URL = "https://api.twelvedata.com"

_TICKERS: list[dict[str, str]] = [
    {"symbol": "DXY",     "category": "index"},
    {"symbol": "EUR/USD", "category": "forex"},
    {"symbol": "USD/JPY", "category": "forex"},
    {"symbol": "XAU/USD", "category": "commodity"},
    {"symbol": "SPX",     "category": "index"},
    {"symbol": "IXIC",    "category": "index"},
]

_INSERT_SQL = text(
    """
    INSERT INTO market_data
      (symbol, interval, category, timestamp, open, high, low, close, volume, source)
    VALUES
      (:symbol, :interval, :category,
       :timestamp, :open, :high, :low, :close, :volume, :source)
    ON CONFLICT (symbol, interval, timestamp) DO NOTHING
    """
)


class YahooFinanceSource(DataSource):
    """Fetches forex, commodity, and index OHLCV from Twelve Data."""

    name = "yahoo"

    @retry(
        wait=wait_exponential(multiplier=1, min=10, max=60),
        stop=stop_after_attempt(3),
    )
    def _fetch_series(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[dict]:
        resp = requests.get(
            f"{_BASE_URL}/time_series",
            params={
                "symbol": symbol,
                "interval": "1day",
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
                "outputsize": 5000,
                "apikey": settings.twelve_data_api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            log.error(
                "twelve_data_error",
                symbol=symbol,
                message=data.get("message"),
            )
            return []
        return data.get("values", [])

    def _normalize(
        self, symbol: str, category: str, values: list[dict]
    ) -> pd.DataFrame:
        if not values:
            return pd.DataFrame()
        rows = []
        for v in values:
            rows.append({
                "symbol": symbol,
                "interval": "1d",
                "category": category,
                "timestamp": datetime.strptime(
                    v["datetime"], "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc),
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
                "volume": float(v.get("volume") or 0),
                "source": self.name,
            })
        return pd.DataFrame(rows)

    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        for ticker in _TICKERS:
            log.info("fetching_historical", ticker=ticker["symbol"])
            try:
                values = self._fetch_series(
                    ticker["symbol"], start, end
                )
                df = self._normalize(
                    ticker["symbol"], ticker["category"], values
                )
                if not df.empty:
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "fetch_failed",
                    ticker=ticker["symbol"],
                    error=str(exc),
                )
            time.sleep(8)  # 8 req/min free-tier limit
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=5)
        return self.fetch_historical(start, end)

    def store(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        records = df.to_dict("records")
        with get_connection() as conn:
            result = conn.execute(_INSERT_SQL, records)
        return result.rowcount
