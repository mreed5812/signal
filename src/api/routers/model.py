"""Model metadata endpoint."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from src.common.database import get_connection

router = APIRouter(tags=["model"])

_ACTIVE_MODEL_SQL = text(
    "SELECT * FROM model_metadata WHERE is_active = true LIMIT 1"
)


class ModelMetadataResponse(BaseModel):
    version: str
    trained_at: str
    train_rows: int | None
    val_rows: int | None
    rmse: float | None
    mae: float | None
    mape: float | None
    directional_accuracy: float | None
    naive_rmse: float | None
    feature_importances: dict[str, float] | None  # type: ignore[type-arg]


@router.get("/model/metadata", response_model=ModelMetadataResponse)
async def model_metadata() -> ModelMetadataResponse:
    with get_connection() as conn:
        row = conn.execute(_ACTIVE_MODEL_SQL).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="No trained model available")
    r = dict(row._mapping)
    return ModelMetadataResponse(
        version=r["version"],
        trained_at=r["trained_at"].isoformat(),
        train_rows=r.get("train_rows"),
        val_rows=r.get("val_rows"),
        rmse=r.get("rmse"),
        mae=r.get("mae"),
        mape=r.get("mape"),
        directional_accuracy=r.get("directional_accuracy"),
        naive_rmse=r.get("naive_rmse"),
        feature_importances=r.get("feature_importances"),
    )
