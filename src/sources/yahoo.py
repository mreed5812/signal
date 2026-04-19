"""Yahoo Finance ingestion via yfinance.

Free-tier limits:
- No official rate limit documented; informal throttling after ~2,000 req/day.
- Historical depth: full history available for most instruments.
- What happens when throttled: yfinance raises an exception; tenacity retries.

Instruments fetched:
  Forex/commodities: DX-Y.NYB (DXY), EURUSD=X, JPY=X, GC=F (Gold)
  Indices: ^GSPC (S&P 500), ^IXIC (NASDAQ)
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.common.database import get_connection
from src.common.logging import get_logger
from src.sources.base import DataSource

log = get_logger(__name__)

_FOREX_TICKERS = ["DX-Y.NYB", "EURUSD=X", "JPY=X", "GC=F"]
_INDEX_TICKERS = ["^GSPC", "^IXIC"]
_ALL_TICKERS = _FOREX_TICKERS + _INDEX_TICKERS

_CATEGORY_MAP: dict[str, str] = {
    "DX-Y.NYB": "forex",
    "EURUSD=X": "forex",
    "JPY=X": "forex",
    "GC=F": "commodity",
    "^GSPC": "index",
    "^IXIC": "index",
}


class YahooFinanceSource(DataSource):
    """Fetches forex, commodity, and index OHLCV from Yahoo Finance."""

    name = "yahoo"

    @retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(5))
    def _download(self, ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        return df

    def _normalize(self, ticker: str, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.reset_index()
        # yfinance returns MultiIndex columns when downloading single ticker with auto_adjust
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() if c[1] == "" else c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]

        df = df.rename(columns={"date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(timezone.utc)
        df["symbol"] = ticker
        df["interval"] = "1d"
        df["category"] = _CATEGORY_MAP.get(ticker, "other")
        df["source"] = self.name
        return df[["symbol", "interval", "category", "timestamp", "open", "high", "low", "close", "volume", "source"]]

    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        for ticker in _ALL_TICKERS:
            log.info("fetching_historical", ticker=ticker)
            try:
                raw = self._download(ticker, start, end)
                normalized = self._normalize(ticker, raw)
                if not normalized.empty:
                    frames.append(normalized)
            except Exception as exc:
                log.error("fetch_failed", ticker=ticker, error=str(exc))
            time.sleep(1)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=5)  # 5-day buffer for weekends/holidays
        return self.fetch_historical(start, end)

    def store(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        records = df.to_dict("records")
        sql = text(
            """
            INSERT INTO market_data
              (symbol, interval, category, timestamp, open, high, low, close, volume, source)
            VALUES
              (:symbol, :interval, :category, :timestamp, :open, :high, :low, :close, :volume, :source)
            ON CONFLICT (symbol, interval, timestamp) DO NOTHING
            """
        )
        with get_connection() as conn:
            result = conn.execute(sql, records)
        return result.rowcount
