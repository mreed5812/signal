"""Daily sentiment aggregates endpoint."""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["sentiment"])

_DAILY_SENTIMENT_SQL = text(
    """
    SELECT
        nr.published_at::date AS date,
        AVG(ns.vader_compound) AS vader_mean,
        AVG(ns.finbert_positive - ns.finbert_negative) AS finbert_mean,
        COUNT(*) AS article_count
    FROM news_sentiment ns
    JOIN news_raw nr ON ns.news_id = nr.id
    WHERE nr.published_at >= CURRENT_DATE - (:days * INTERVAL '1 day')
    GROUP BY date
    ORDER BY date DESC
    """
)


class DailySentimentItem(BaseModel):
    date: str
    vader_mean: float | None
    finbert_mean: float | None
    article_count: int


@router.get("/sentiment/daily", response_model=list[DailySentimentItem])
async def daily_sentiment(
    days: int = Query(30, ge=1, le=365),
) -> list[DailySentimentItem]:
    with get_connection() as conn:
        rows = conn.execute(_DAILY_SENTIMENT_SQL, {"days": days}).fetchall()
    return [
        DailySentimentItem(
            date=str(r[0]),
            vader_mean=float(r[1]) if r[1] is not None else None,
            finbert_mean=float(r[2]) if r[2] is not None else None,
            article_count=int(r[3]),
        )
        for r in rows
    ]
