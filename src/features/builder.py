"""Feature engineering pipeline.

Produces one row per day in the `features` table.
All features for day T use only data available by end-of-day T — no lookahead.

Bitcoin halving dates (approximate):
    2012-11-28, 2016-07-09, 2020-05-11, 2024-04-19
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

import numpy as np
from sqlalchemy import text
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands

from src.common.database import get_connection
from src.common.logging import get_logger
from src.common.metrics import job_duration_seconds, job_runs

log = get_logger(__name__)

_HALVING_DATES = [
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 19),
]


def _days_since_halving(d: date) -> int:
    past = [h for h in _HALVING_DATES if h <= d]
    if not past:
        return 0
    return (d - max(past)).days


_PRICES_SQL = text(
    "SELECT symbol, timestamp::date AS date, close"
    " FROM prices WHERE interval = '1d' ORDER BY symbol, timestamp"
)
_MARKET_SQL = text(
    "SELECT symbol, timestamp::date AS date, close"
    " FROM market_data WHERE interval = '1d' ORDER BY symbol, timestamp"
)
_ONCHAIN_SQL = text(
    "SELECT metric_name, timestamp::date AS date, value"
    " FROM onchain_metrics ORDER BY metric_name, timestamp"
)
_SENTIMENT_SQL = text(
    """
    SELECT
        nr.published_at::date AS date,
        ns.vader_compound,
        ns.finbert_positive - ns.finbert_negative AS finbert_score
    FROM news_sentiment ns
    JOIN news_raw nr ON ns.news_id = nr.id
    ORDER BY date
    """
)
_UPSERT_FEATURES_SQL_TMPL = (
    "INSERT INTO features ({cols}) VALUES ({placeholders})"
    " ON CONFLICT (date) DO UPDATE SET {updates}"
)


def _load_prices() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(_PRICES_SQL, conn)


def _load_market_data() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(_MARKET_SQL, conn)


def _load_onchain() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(_ONCHAIN_SQL, conn)


def _load_sentiment() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(_SENTIMENT_SQL, conn)


def build_features() -> pd.DataFrame:
    """Assemble the full feature table and return as a DataFrame."""
    prices = _load_prices()
    if prices.empty:
        log.warning("no_price_data")
        return pd.DataFrame()

    # ── BTC price features ────────────────────────────────────────────────────
    btc = prices[prices["symbol"] == "BTC"].set_index("date").sort_index()
    btc["close"] = btc["close"].astype(float)

    feat = pd.DataFrame(index=btc.index)
    feat["btc_close"] = btc["close"]
    feat["btc_return_1d"] = btc["close"].pct_change(1)
    feat["btc_return_3d"] = btc["close"].pct_change(3)
    feat["btc_return_7d"] = btc["close"].pct_change(7)
    feat["btc_return_14d"] = btc["close"].pct_change(14)
    feat["btc_return_30d"] = btc["close"].pct_change(30)
    feat["btc_volatility_7d"] = btc["close"].pct_change().rolling(7).std()
    feat["btc_volatility_30d"] = btc["close"].pct_change().rolling(30).std()

    rsi = RSIIndicator(close=btc["close"], window=14)
    feat["btc_rsi_14"] = rsi.rsi()

    macd = MACD(close=btc["close"])
    feat["btc_macd"] = macd.macd()
    feat["btc_macd_signal"] = macd.macd_signal()

    bb = BollingerBands(close=btc["close"], window=20)
    band_width = bb.bollinger_hband() - bb.bollinger_lband()
    feat["btc_bb_position"] = np.where(
        band_width > 0,
        (btc["close"] - bb.bollinger_lband()) / band_width,
        0.5,
    )

    # ── Cross-asset features ───────────────────────────────────────────────────
    def add_asset_return(symbol: str, col_name: str, df: pd.DataFrame) -> None:
        asset = df[df["symbol"] == symbol].set_index("date")["close"].astype(float)
        feat[col_name] = asset.pct_change(1).reindex(feat.index)

    eth = prices[prices["symbol"] == "ETH"]
    market = _load_market_data()

    add_asset_return("ETH", "eth_return_1d", prices)
    add_asset_return("XAU/USD", "gold_return_1d", market)
    add_asset_return("UUP", "dxy_return_1d", market)
    add_asset_return("SPY", "sp500_return_1d", market)
    add_asset_return("QQQ", "nasdaq_return_1d", market)

    # 30-day rolling correlations
    btc_ret = btc["close"].pct_change()
    eth_ret = (
        prices[prices["symbol"] == "ETH"]
        .set_index("date")["close"]
        .astype(float)
        .pct_change()
        .reindex(feat.index)
    )
    gold_ret = (
        market[market["symbol"] == "XAU/USD"]
        .set_index("date")["close"]
        .astype(float)
        .pct_change()
        .reindex(feat.index)
    )
    feat["btc_eth_corr_30d"] = btc_ret.rolling(30).corr(eth_ret)
    feat["btc_gold_corr_30d"] = btc_ret.rolling(30).corr(gold_ret)

    # ── On-chain features ──────────────────────────────────────────────────────
    onchain = _load_onchain()

    def add_onchain_change(metric: str, col_name: str) -> None:
        series = (
            onchain[onchain["metric_name"] == metric]
            .set_index("date")["value"]
            .astype(float)
        )
        feat[col_name] = series.pct_change(1).reindex(feat.index)

    add_onchain_change("hash-rate", "hashrate_change_1d")
    add_onchain_change("n-transactions", "tx_count_change_1d")
    add_onchain_change("n-unique-addresses", "active_addr_change_1d")

    # ── Sentiment features ─────────────────────────────────────────────────────
    sentiment = _load_sentiment()
    if not sentiment.empty:
        daily_sent = sentiment.groupby("date").agg(
            vader_mean=("vader_compound", "mean"),
            finbert_mean=("finbert_score", "mean"),
            article_count=("vader_compound", "count"),
        )
        feat["vader_mean"] = daily_sent["vader_mean"].reindex(feat.index)
        feat["finbert_mean"] = daily_sent["finbert_mean"].reindex(feat.index)
        feat["article_count"] = daily_sent["article_count"].reindex(feat.index).fillna(0)
        feat["vader_7d_rolling"] = feat["vader_mean"].rolling(7).mean()
        feat["finbert_7d_rolling"] = feat["finbert_mean"].rolling(7).mean()
        # Neutral prior (0.0) for days without news data
        for col in ["vader_mean", "finbert_mean", "vader_7d_rolling", "finbert_7d_rolling"]:
            feat[col] = feat[col].fillna(0.0)
    else:
        for col in ["vader_mean", "finbert_mean", "article_count", "vader_7d_rolling", "finbert_7d_rolling"]:
            feat[col] = 0.0

    # ── Calendar features ──────────────────────────────────────────────────────
    feat["day_of_week"] = pd.to_datetime(feat.index).dayofweek
    feat["month"] = pd.to_datetime(feat.index).month
    feat["days_since_halving"] = [_days_since_halving(d) for d in feat.index]

    # ── Target variables ───────────────────────────────────────────────────────
    # Shift -1 so row T has the NEXT day's price (lookahead-safe: we only use
    # these as targets during training, never as input features).
    feat["target_next_close"] = btc["close"].shift(-1)
    ratio = btc["close"].shift(-1) / btc["close"]
    feat["target_next_log_return"] = np.where(ratio > 0, np.log(ratio), np.nan)

    feat = feat.reset_index().rename(columns={"date": "date"})
    return feat


def store_features(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    records = df.where(pd.notna(df), None).to_dict("records")
    cols = [c for c in df.columns if c != "id"]
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "date")
    upsert_sql = text(
        f"INSERT INTO features ({col_list}) VALUES ({placeholders})"
        f" ON CONFLICT (date) DO UPDATE SET {updates}"
    )
    with get_connection() as conn:
        result = conn.execute(upsert_sql, records)
    return result.rowcount


def main() -> None:
    start = time.time()
    try:
        df = build_features()
        count = store_features(df)
        job_runs.labels(job="feature_builder", status="success").inc()
        log.info("features_built", rows=count)
    except Exception as exc:
        job_runs.labels(job="feature_builder", status="failure").inc()
        log.error("feature_build_failed", error=str(exc), exc_info=True)
        raise
    finally:
        job_duration_seconds.labels(job="feature_builder").observe(time.time() - start)


if __name__ == "__main__":
    main()
