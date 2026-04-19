"""Correlations and multi-asset time series endpoint."""

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["correlations"])

_PRICES_SQL = text(
    """
    SELECT symbol, timestamp::date AS date, close
    FROM prices
    WHERE symbol IN ('BTC', 'ETH')
      AND interval = '1d'
      AND timestamp >= CURRENT_DATE - (:days * INTERVAL '1 day')
    ORDER BY symbol, timestamp
    """
)

_MARKET_SQL = text(
    """
    SELECT symbol, timestamp::date AS date, close
    FROM market_data
    WHERE interval = '1d'
      AND timestamp >= CURRENT_DATE - (:days * INTERVAL '1 day')
    ORDER BY symbol, timestamp
    """
)

_ONCHAIN_SQL = text(
    """
    SELECT metric_name AS symbol, timestamp::date AS date, value AS close
    FROM onchain_metrics
    WHERE timestamp >= CURRENT_DATE - (:days * INTERVAL '1 day')
      AND metric_name IN ('hash-rate', 'n-transactions')
    ORDER BY metric_name, timestamp
    """
)

_SENTIMENT_SQL = text(
    """
    SELECT
        nr.published_at::date AS date,
        AVG(ns.vader_compound) AS close
    FROM news_sentiment ns
    JOIN news_raw nr ON ns.news_id = nr.id
    WHERE nr.published_at >= CURRENT_DATE - (:days * INTERVAL '1 day')
    GROUP BY date
    """
)


class CorrelationsResponse(BaseModel):
    series: dict[str, list[dict]]  # type: ignore[type-arg]
    correlation_matrix: dict[str, dict[str, float]]  # type: ignore[type-arg]


@router.get("/correlations", response_model=CorrelationsResponse)
async def correlations(
    days: int = Query(90, ge=7, le=730),
    window: int = Query(30, ge=7, le=365),
) -> CorrelationsResponse:
    params = {"days": days}
    with get_connection() as conn:
        prices = pd.read_sql(_PRICES_SQL, conn, params=params)
        market = pd.read_sql(_MARKET_SQL, conn, params=params)
        onchain = pd.read_sql(_ONCHAIN_SQL, conn, params=params)
        sentiment_df = pd.read_sql(_SENTIMENT_SQL, conn, params=params)

    all_data = pd.concat([prices, market, onchain], ignore_index=True)
    if not sentiment_df.empty:
        sentiment_df["symbol"] = "sentiment"
        all_data = pd.concat([all_data, sentiment_df], ignore_index=True)

    wide = all_data.pivot_table(index="date", columns="symbol", values="close")
    returns = wide.pct_change().dropna(how="all")

    corr_matrix: dict[str, dict[str, float]] = {}
    if "BTC" in returns.columns:
        btc_ret = returns["BTC"].tail(window)
        for col in returns.columns:
            if col == "BTC":
                continue
            other = returns[col].tail(window)
            combined = pd.concat([btc_ret, other], axis=1).dropna()
            corr = float(combined.corr().iloc[0, 1]) if len(combined) > 5 else 0.0
            corr_matrix.setdefault("BTC", {})[col] = corr

    series: dict[str, list[dict]] = {}  # type: ignore[type-arg]
    for col in wide.columns:
        s = wide[col].dropna()
        if s.empty:
            continue
        base = s.iloc[0]
        pct = ((s - base) / base * 100) if base != 0 else s
        series[col] = [{"date": str(d), "value": round(float(v), 4)} for d, v in pct.items()]

    return CorrelationsResponse(series=series, correlation_matrix=corr_matrix)
