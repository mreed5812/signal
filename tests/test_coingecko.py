"""Tests for CoinGecko data source (mocked HTTP)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.sources.coingecko import CoinGeckoSource


@pytest.fixture()
def source() -> CoinGeckoSource:
    return CoinGeckoSource()


def _mock_response(coin_id: str) -> dict:
    """Minimal valid CoinGecko market_chart/range response."""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return {
        "prices": [[now_ms, 50000.0], [now_ms - 86400000, 49000.0]],
        "total_volumes": [[now_ms, 1e9], [now_ms - 86400000, 9e8]],
    }


def test_fetch_latest_returns_dataframe(source: CoinGeckoSource) -> None:
    with patch.object(source, "_get", side_effect=lambda path, params=None: _mock_response("bitcoin")):
        df = source.fetch_latest()
    assert not df.empty
    assert "close" in df.columns
    assert "symbol" in df.columns


def test_fetch_latest_includes_all_symbols(source: CoinGeckoSource) -> None:
    with patch.object(source, "_get", side_effect=lambda path, params=None: _mock_response("bitcoin")):
        df = source.fetch_latest()
    # Should have rows for all configured symbols
    assert df["symbol"].nunique() == len(source.symbols)


def test_store_calls_db(source: CoinGeckoSource) -> None:
    import pandas as pd
    df = pd.DataFrame([
        {"symbol": "BTC", "interval": "1d", "timestamp": datetime.now(tz=timezone.utc),
         "open": 50000.0, "high": 51000.0, "low": 49000.0, "close": 50500.0,
         "volume": 1e9, "source": "coingecko"}
    ])
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.rowcount = 1

    with patch("src.sources.coingecko.get_connection", return_value=mock_conn):
        count = source.store(df)

    assert count == 1
    mock_conn.execute.assert_called_once()


def test_store_empty_df_returns_zero(source: CoinGeckoSource) -> None:
    import pandas as pd
    assert source.store(pd.DataFrame()) == 0
