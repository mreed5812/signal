"""Prometheus metrics shared across all pipeline services."""

from prometheus_client import Counter, Gauge, Histogram

# ─── Ingestion ────────────────────────────────────────────────────────────────
rows_ingested = Counter(
    "btc_pipeline_rows_ingested_total",
    "Total rows written to the database",
    ["source", "symbol"],
)

ingestion_errors = Counter(
    "btc_pipeline_ingestion_errors_total",
    "Total ingestion errors by source",
    ["source"],
)

# ─── Jobs ─────────────────────────────────────────────────────────────────────
job_runs = Counter(
    "btc_pipeline_job_runs_total",
    "Total job executions",
    ["job", "status"],  # status: success | failure
)

job_duration_seconds = Histogram(
    "btc_pipeline_job_duration_seconds",
    "Job wall-clock duration in seconds",
    ["job"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

# ─── Model ────────────────────────────────────────────────────────────────────
model_rmse = Gauge(
    "btc_pipeline_model_rmse",
    "Latest model RMSE on the validation set",
)

prediction_error_usd = Gauge(
    "btc_pipeline_prediction_error_usd",
    "Absolute error between the last prediction and actual price (USD)",
)

# ─── Data freshness ───────────────────────────────────────────────────────────
last_ingestion_timestamp = Gauge(
    "btc_pipeline_last_ingestion_timestamp_seconds",
    "Unix timestamp of the most recent successful ingestion",
    ["source"],
)
