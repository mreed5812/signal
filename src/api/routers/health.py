"""Health check endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from src.common.database import check_connectivity

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_ok = check_connectivity()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "unreachable",
    )
