"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from src.api.routers import correlations, features, health, model, prediction, price, sentiment
from src.common.logging import configure_logging

configure_logging()

app = FastAPI(
    title="BTC Pipeline API",
    description="Bitcoin next-day prediction pipeline",
    version="0.1.0",
)

# Prometheus scrape endpoint at /metrics
Instrumentator().instrument(app).expose(app)

# API routers
app.include_router(health.router, prefix="/api")
app.include_router(price.router, prefix="/api")
app.include_router(prediction.router, prefix="/api")
app.include_router(features.router, prefix="/api")
app.include_router(sentiment.router, prefix="/api")
app.include_router(correlations.router, prefix="/api")
app.include_router(model.router, prefix="/api")

# Static dashboard
_dashboard_dir = Path(__file__).parent.parent.parent / "dashboard"
if _dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_dashboard_dir)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    index = _dashboard_dir / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text())
    return HTMLResponse(content="<h1>Dashboard not built yet</h1>")
