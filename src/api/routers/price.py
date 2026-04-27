"""Current BTC price endpoint."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["price"])

_LATEST_PRICE_SQL = text(
    """
    SELECT close, timestamp FROM prices
    WHERE symbol = 'BTC' AND interval = '1d'
    ORDER BY timestamp DESC LIMIT 1
    """
)

_HISTORY_SQL = text(
    """
    SELECT timestamp::date AS date, close
    FROM prices
    WHERE symbol = 'BTC' AND interval = '1d'
      AND timestamp >= NOW() - :days * INTERVAL '1 day'
    ORDER BY timestamp
    """
)


class PriceResponse(BaseModel):
    symbol: str
    price: float
    timestamp: str


@router.get("/price/current", response_model=PriceResponse)
async def current_price() -> PriceResponse:
    with get_connection() as conn:
        row = conn.execute(_LATEST_PRICE_SQL).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="No price data available yet")
    return PriceResponse(symbol="BTC", price=float(row[0]), timestamp=row[1].isoformat())


@router.get("/price/history")
async def price_history(days: int = Query(default=90, ge=1, le=1000)) -> dict:
    with get_connection() as conn:
        rows = conn.execute(_HISTORY_SQL, {"days": days}).fetchall()
    return {
        "dates": [str(r[0]) for r in rows],
        "prices": [float(r[1]) for r in rows],
    }
