"""Prediction endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["prediction"])

_LATEST_PRED_SQL = text(
    """
    SELECT prediction_date, target_date, predicted_price, confidence_lower,
           confidence_upper, model_version, actual_price
    FROM predictions ORDER BY prediction_date DESC LIMIT 1
    """
)

# PostgreSQL INTERVAL syntax requires the unit keyword inside the string literal,
# so we use `days * INTERVAL '1 day'` to safely inject the numeric parameter.
_HISTORY_SQL = text(
    """
    SELECT target_date, predicted_price, actual_price
    FROM predictions
    WHERE target_date >= CURRENT_DATE - (:days * INTERVAL '1 day')
    ORDER BY target_date DESC
    """
)


class PredictionResponse(BaseModel):
    prediction_date: str
    target_date: str
    predicted_price: float
    confidence_lower: float | None
    confidence_upper: float | None
    model_version: str
    actual_price: float | None


class PredictionHistoryItem(BaseModel):
    target_date: str
    predicted_price: float
    actual_price: float | None
    error_usd: float | None


@router.get("/prediction/latest", response_model=PredictionResponse)
async def latest_prediction() -> PredictionResponse:
    with get_connection() as conn:
        row = conn.execute(_LATEST_PRED_SQL).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="No predictions available yet")
    r = dict(row._mapping)
    return PredictionResponse(
        prediction_date=str(r["prediction_date"]),
        target_date=str(r["target_date"]),
        predicted_price=float(r["predicted_price"]),
        confidence_lower=float(r["confidence_lower"]) if r["confidence_lower"] else None,
        confidence_upper=float(r["confidence_upper"]) if r["confidence_upper"] else None,
        model_version=r["model_version"],
        actual_price=float(r["actual_price"]) if r["actual_price"] else None,
    )


@router.get("/prediction/history", response_model=list[PredictionHistoryItem])
async def prediction_history(
    days: int = Query(30, ge=1, le=365),
) -> list[PredictionHistoryItem]:
    with get_connection() as conn:
        rows = conn.execute(_HISTORY_SQL, {"days": days}).fetchall()
    return [
        PredictionHistoryItem(
            target_date=str(r[0]),
            predicted_price=float(r[1]),
            actual_price=float(r[2]) if r[2] else None,
            error_usd=abs(float(r[2]) - float(r[1])) if r[2] else None,
        )
        for r in rows
    ]
