"""Tests for feature builder (mocked DB queries)."""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.features.builder import _days_since_halving, build_features


def test_days_since_halving_after_2024() -> None:
    d = date(2024, 5, 1)
    days = _days_since_halving(d)
    assert days == (d - date(2024, 4, 19)).days


def test_days_since_halving_before_first() -> None:
    d = date(2012, 1, 1)
    assert _days_since_halving(d) == 0


def _make_btc_prices(n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D").date
    prices = 40000 + np.cumsum(np.random.randn(n) * 500)
    rows = []
    for d, p in zip(dates, prices):
        rows.append({"symbol": "BTC", "date": d, "close": max(p, 1000)})
        rows.append({"symbol": "ETH", "date": d, "close": max(p / 15, 100)})
    return pd.DataFrame(rows)


def _make_market_data(n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D").date
    rows = []
    for d in dates:
        rows += [
            {"symbol": "GC=F", "date": d, "close": 1900.0},
            {"symbol": "DX-Y.NYB", "date": d, "close": 104.0},
            {"symbol": "^GSPC", "date": d, "close": 4800.0},
            {"symbol": "^IXIC", "date": d, "close": 15000.0},
        ]
    return pd.DataFrame(rows)


def _make_onchain(n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D").date
    rows = []
    for d in dates:
        rows += [
            {"metric_name": "hash-rate", "date": d, "value": 600e18},
            {"metric_name": "n-transactions", "date": d, "value": 350000},
            {"metric_name": "n-unique-addresses", "date": d, "value": 900000},
        ]
    return pd.DataFrame(rows)


def test_build_features_no_lookahead() -> None:
    prices = _make_btc_prices(60)
    market = _make_market_data(60)
    onchain = _make_onchain(60)

    with (
        patch("src.features.builder._load_prices", return_value=prices),
        patch("src.features.builder._load_market_data", return_value=market),
        patch("src.features.builder._load_onchain", return_value=onchain),
        patch("src.features.builder._load_sentiment", return_value=pd.DataFrame()),
    ):
        df = build_features()

    assert not df.empty
    assert "btc_return_1d" in df.columns
    assert "target_next_close" in df.columns
    # The last row's target should be NaN (no next-day price available)
    # because shift(-1) leaves the last row as NaN
    assert pd.isna(df["target_next_close"].iloc[-1])
