"""Recent feature rows endpoint."""

from fastapi import APIRouter, Query
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["features"])

_RECENT_SQL = text(
    """
    SELECT * FROM features
    WHERE date >= CURRENT_DATE - (:days * INTERVAL '1 day')
    ORDER BY date DESC
    """
)


@router.get("/features/recent")
async def recent_features(days: int = Query(7, ge=1, le=90)) -> list[dict]:  # type: ignore[type-arg]
    with get_connection() as conn:
        rows = conn.execute(_RECENT_SQL, {"days": days}).fetchall()
    return [dict(r._mapping) for r in rows]
