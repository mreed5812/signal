"""Current BTC price endpoint."""

from fastapi import APIRouter, HTTPException
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
