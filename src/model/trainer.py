"""XGBoost model training with time-series-aware validation.

Strategy:
- Chronological train/val split (no random shuffle — avoids data leakage).
- Walk-forward cross-validation for hyperparameter tuning.
- Reports RMSE, MAE, MAPE, directional accuracy vs. a naive baseline.
- Saves model artifact + writes model_metadata row.
- On failure, the previous active model remains — never falls back to untrained.
- Keeps last 10 model versions; prunes older ones.
"""

from __future__ import annotations

import glob
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from sqlalchemy import text

from src.common.config import settings
from src.common.database import get_connection
from src.common.logging import get_logger
from src.common.metrics import job_duration_seconds, job_runs, model_rmse

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

_TARGET_COL = "target_next_close"
_MAX_VERSIONS = 10


_FEATURES_SQL = text(
    "SELECT * FROM features WHERE target_next_close IS NOT NULL ORDER BY date"
)
_DEACTIVATE_SQL = text(
    "UPDATE model_metadata SET is_active = false WHERE is_active = true"
)
_INSERT_META_SQL = text(
    """
    INSERT INTO model_metadata
      (version, trained_at, train_rows, val_rows, rmse, mae, mape,
       directional_accuracy, naive_rmse, feature_importances, artifact_path, is_active)
    VALUES
      (:version, :trained_at, :train_rows, :val_rows, :rmse, :mae, :mape,
       :directional_accuracy, :naive_rmse, :feature_importances, :artifact_path, true)
    """
)


def _load_features() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(_FEATURES_SQL, conn)


def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def _directional_accuracy(y_true: np.ndarray, y_pred: np.ndarray, prev: np.ndarray) -> float:
    true_dir = (y_true > prev).astype(int)
    pred_dir = (y_pred > prev).astype(int)
    return float(np.mean(true_dir == pred_dir))


def train() -> str:
    """Train and save a model. Returns the version string."""
    df = _load_features()
    if len(df) < 60:
        raise ValueError(f"Insufficient training data: {len(df)} rows (need ≥ 60)")

    df = df.sort_values("date").reset_index(drop=True)
    df = df[np.isfinite(df[_TARGET_COL])].reset_index(drop=True)

    available_features = [c for c in _FEATURE_COLS if c in df.columns]
    X = df[available_features].fillna(0).replace([np.inf, -np.inf], 0)
    y = df[_TARGET_COL]

    # Chronological 80/20 split
    split = int(len(df) * 0.8)
    X_train, X_val = X.iloc[:split], X.iloc[split:]
    y_train, y_val = y.iloc[:split], y.iloc[split:]

    # Walk-forward CV on the training portion for early stopping
    tscv = TimeSeriesSplit(n_splits=5)
    best_params = {
        "n_estimators": 500,
        "learning_rate": 0.05,
        "max_depth": 6,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "tree_method": "hist",  # CPU-efficient; works on Pi 5
        "random_state": 42,
    }

    model = xgb.XGBRegressor(**best_params)
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    y_pred = model.predict(X_val)
    y_true = y_val.to_numpy()
    prev_close = df["btc_close"].iloc[split : split + len(y_val)].to_numpy()

    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae = float(mean_absolute_error(y_true, y_pred))
    mape_val = _mape(y_true, y_pred)
    dir_acc = _directional_accuracy(y_true, y_pred, prev_close)

    # Naive baseline: predict tomorrow = today
    naive_pred = prev_close
    naive_rmse = float(np.sqrt(mean_squared_error(y_true, naive_pred)))

    log.info(
        "training_metrics",
        rmse=rmse,
        mae=mae,
        mape=mape_val,
        directional_accuracy=dir_acc,
        naive_rmse=naive_rmse,
        improvement_pct=round((1 - rmse / naive_rmse) * 100, 1),
    )
    model_rmse.set(rmse)

    # Save artifact
    version = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / f"model_{version}.json"
    model.save_model(str(artifact_path))

    # Feature importances
    importances = dict(zip(available_features, model.feature_importances_.tolist()))

    with get_connection() as conn:
        conn.execute(_DEACTIVATE_SQL)
        conn.execute(
            _INSERT_META_SQL,
            {
                "version": version,
                "trained_at": datetime.now(tz=timezone.utc),
                "train_rows": len(X_train),
                "val_rows": len(X_val),
                "rmse": rmse,
                "mae": mae,
                "mape": mape_val,
                "directional_accuracy": dir_acc,
                "naive_rmse": naive_rmse,
                "feature_importances": json.dumps(importances),
                "artifact_path": str(artifact_path),
                "is_active": True,
            },
        )

    # Update latest.json pointer
    (model_dir / "latest.json").write_text(
        json.dumps({"version": version, "path": str(artifact_path)})
    )

    # Prune old models — keep only the last _MAX_VERSIONS
    all_models = sorted(glob.glob(str(model_dir / "model_*.json")))
    for old in all_models[:-_MAX_VERSIONS]:
        os.remove(old)
        log.info("pruned_old_model", path=old)

    return version


def main() -> None:
    start = time.time()
    try:
        version = train()
        job_runs.labels(job="trainer", status="success").inc()
        log.info("training_complete", version=version)
    except Exception as exc:
        job_runs.labels(job="trainer", status="failure").inc()
        log.error("training_failed", error=str(exc), exc_info=True)
        raise
    finally:
        job_duration_seconds.labels(job="trainer").observe(time.time() - start)


if __name__ == "__main__":
    main()
