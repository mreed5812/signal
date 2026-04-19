"""Next-day price predictor.

Loads the active model, runs inference on the latest feature row, and
writes the result to the predictions table. Also fills in actual_price for
yesterday's prediction row if BTC data is available.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from src.common.config import settings
from sqlalchemy import text

from src.common.database import get_connection
from src.common.logging import get_logger
from src.common.metrics import job_duration_seconds, job_runs, prediction_error_usd

log = get_logger(__name__)

_FEATURE_COLS = [
    "btc_return_1d", "btc_return_3d", "btc_return_7d", "btc_return_14d", "btc_return_30d",
    "btc_volatility_7d", "btc_volatility_30d", "btc_rsi_14", "btc_macd", "btc_macd_signal",
    "btc_bb_position", "eth_return_1d", "gold_return_1d", "dxy_return_1d", "sp500_return_1d",
    "nasdaq_return_1d", "btc_eth_corr_30d", "btc_gold_corr_30d", "hashrate_change_1d",
    "tx_count_change_1d", "active_addr_change_1d", "vader_mean", "vader_7d_rolling",
    "finbert_mean", "finbert_7d_rolling", "article_count", "day_of_week", "month",
    "days_since_halving",
]


def _load_model() -> tuple[xgb.XGBRegressor, str]:
    latest_path = Path(settings.model_dir) / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError("No trained model found — run the trainer first")
    info = json.loads(latest_path.read_text())
    model = xgb.XGBRegressor()
    model.load_model(info["path"])
    return model, info["version"]


_LATEST_FEATURES_SQL = text(
    "SELECT * FROM features WHERE btc_close IS NOT NULL ORDER BY date DESC LIMIT 1"
)
_ACTUAL_PRICE_SQL = text(
    """
    SELECT close FROM prices
    WHERE symbol = 'BTC' AND interval = '1d'
      AND timestamp::date = :d
    ORDER BY timestamp DESC LIMIT 1
    """
)
_UPDATE_ACTUAL_SQL = text(
    "UPDATE predictions SET actual_price = :price WHERE target_date = :d AND actual_price IS NULL"
)
_PREV_PRED_SQL = text(
    "SELECT predicted_price FROM predictions WHERE target_date = :d LIMIT 1"
)
_MODEL_RMSE_SQL = text(
    "SELECT rmse FROM model_metadata WHERE version = :v"
)
_INSERT_PRED_SQL = text(
    """
    INSERT INTO predictions
      (prediction_date, target_date, predicted_price, predicted_log_return,
       confidence_lower, confidence_upper, model_version, created_at)
    VALUES
      (:prediction_date, :target_date, :predicted_price, :predicted_log_return,
       :confidence_lower, :confidence_upper, :model_version, :created_at)
    ON CONFLICT (prediction_date, target_date) DO UPDATE SET
      predicted_price = EXCLUDED.predicted_price,
      predicted_log_return = EXCLUDED.predicted_log_return,
      confidence_lower = EXCLUDED.confidence_lower,
      confidence_upper = EXCLUDED.confidence_upper,
      model_version = EXCLUDED.model_version
    """
)


def _latest_features() -> pd.DataFrame | None:
    with get_connection() as conn:
        row = conn.execute(_LATEST_FEATURES_SQL).fetchone()
    if row is None:
        return None
    return pd.DataFrame([dict(row._mapping)])


def _confidence_interval(predicted: float, rmse: float) -> tuple[float, float]:
    """95% CI assuming normally distributed errors."""
    z = 1.96
    return (predicted - z * rmse, predicted + z * rmse)


def _fill_actuals() -> None:
    """Back-fill actual prices into predictions from yesterday."""
    yesterday = date.today() - timedelta(days=1)
    with get_connection() as conn:
        actual = conn.execute(_ACTUAL_PRICE_SQL, {"d": yesterday}).fetchone()
        if actual is None:
            return
        conn.execute(_UPDATE_ACTUAL_SQL, {"price": float(actual[0]), "d": yesterday})
        pred_row = conn.execute(_PREV_PRED_SQL, {"d": yesterday}).fetchone()
        if pred_row:
            prediction_error_usd.set(abs(float(actual[0]) - float(pred_row[0])))


def predict() -> None:
    model, version = _load_model()

    with get_connection() as conn:
        meta = conn.execute(_MODEL_RMSE_SQL, {"v": version}).fetchone()
    rmse = float(meta[0]) if meta else 1000.0

    features = _latest_features()
    if features is None:
        raise ValueError("No features available — run the feature builder first")

    available_cols = [c for c in _FEATURE_COLS if c in features.columns]
    X = features[available_cols].fillna(0)
    predicted_price = float(model.predict(X)[0])

    btc_close = float(features["btc_close"].iloc[0]) if "btc_close" in features.columns else None
    predicted_log_return = (
        float(np.log(predicted_price / btc_close)) if btc_close and btc_close > 0 else None
    )

    ci_lower, ci_upper = _confidence_interval(predicted_price, rmse)
    today = date.today()
    target_date = today + timedelta(days=1)

    with get_connection() as conn:
        conn.execute(
            _INSERT_PRED_SQL,
            {
                "prediction_date": today,
                "target_date": target_date,
                "predicted_price": predicted_price,
                "predicted_log_return": predicted_log_return,
                "confidence_lower": ci_lower,
                "confidence_upper": ci_upper,
                "model_version": version,
                "created_at": datetime.now(tz=timezone.utc),
            },
        )

    _fill_actuals()
    log.info(
        "prediction_complete",
        predicted_price=predicted_price,
        target_date=str(target_date),
        version=version,
    )


def main() -> None:
    start = time.time()
    try:
        predict()
        job_runs.labels(job="predictor", status="success").inc()
    except Exception as exc:
        job_runs.labels(job="predictor", status="failure").inc()
        log.error("prediction_failed", error=str(exc), exc_info=True)
        raise
    finally:
        job_duration_seconds.labels(job="predictor").observe(time.time() - start)


if __name__ == "__main__":
    main()
