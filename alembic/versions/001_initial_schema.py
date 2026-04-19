"""Initial database schema.

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── prices ────────────────────────────────────────────────────────────────
    # BTC, ETH, SOL, etc. OHLCV from CoinGecko.
    op.create_table(
        "prices",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=True),
        sa.Column("high", sa.Numeric(20, 8), nullable=True),
        sa.Column("low", sa.Numeric(20, 8), nullable=True),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(30, 8), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.UniqueConstraint("symbol", "interval", "timestamp", name="uq_prices_symbol_interval_ts"),
    )
    op.create_index("ix_prices_symbol_ts", "prices", ["symbol", "timestamp"])

    # ── market_data ───────────────────────────────────────────────────────────
    # Unified table for forex, commodities, and indices (Yahoo Finance).
    # Using one table with a category column simplifies joins in feature engineering.
    # Tradeoff: slightly wider index scans vs. separate tables being simpler to reason about.
    op.create_table(
        "market_data",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),  # forex | commodity | index
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=True),
        sa.Column("high", sa.Numeric(20, 8), nullable=True),
        sa.Column("low", sa.Numeric(20, 8), nullable=True),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(30, 8), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.UniqueConstraint("symbol", "interval", "timestamp", name="uq_market_symbol_interval_ts"),
    )
    op.create_index("ix_market_data_symbol_ts", "market_data", ["symbol", "timestamp"])

    # ── onchain_metrics ───────────────────────────────────────────────────────
    op.create_table(
        "onchain_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.UniqueConstraint("metric_name", "timestamp", name="uq_onchain_metric_ts"),
    )

    # ── news_raw ──────────────────────────────────────────────────────────────
    op.create_table(
        "news_raw",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("query", sa.String(128), nullable=False),
        sa.Column("headline", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_name", sa.String(128), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sentiment_processed", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("url", name="uq_news_url"),
    )
    op.create_index("ix_news_raw_published_at", "news_raw", ["published_at"])
    op.create_index("ix_news_raw_unprocessed", "news_raw", ["sentiment_processed", "id"])

    # ── news_sentiment ────────────────────────────────────────────────────────
    op.create_table(
        "news_sentiment",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("news_id", sa.BigInteger, sa.ForeignKey("news_raw.id"), nullable=False),
        sa.Column("vader_compound", sa.Float, nullable=False),
        sa.Column("vader_pos", sa.Float, nullable=False),
        sa.Column("vader_neg", sa.Float, nullable=False),
        sa.Column("vader_neu", sa.Float, nullable=False),
        sa.Column("finbert_positive", sa.Float, nullable=False),
        sa.Column("finbert_negative", sa.Float, nullable=False),
        sa.Column("finbert_neutral", sa.Float, nullable=False),
        sa.Column("finbert_label", sa.String(16), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_news_sentiment_news_id", "news_sentiment", ["news_id"])

    # ── features ──────────────────────────────────────────────────────────────
    # One row per day; all features for day T use only data available at end-of-day T.
    op.create_table(
        "features",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date, nullable=False, unique=True),
        sa.Column("btc_close", sa.Float, nullable=True),
        sa.Column("btc_return_1d", sa.Float, nullable=True),
        sa.Column("btc_return_3d", sa.Float, nullable=True),
        sa.Column("btc_return_7d", sa.Float, nullable=True),
        sa.Column("btc_return_14d", sa.Float, nullable=True),
        sa.Column("btc_return_30d", sa.Float, nullable=True),
        sa.Column("btc_volatility_7d", sa.Float, nullable=True),
        sa.Column("btc_volatility_30d", sa.Float, nullable=True),
        sa.Column("btc_rsi_14", sa.Float, nullable=True),
        sa.Column("btc_macd", sa.Float, nullable=True),
        sa.Column("btc_macd_signal", sa.Float, nullable=True),
        sa.Column("btc_bb_position", sa.Float, nullable=True),
        sa.Column("eth_return_1d", sa.Float, nullable=True),
        sa.Column("gold_return_1d", sa.Float, nullable=True),
        sa.Column("dxy_return_1d", sa.Float, nullable=True),
        sa.Column("sp500_return_1d", sa.Float, nullable=True),
        sa.Column("nasdaq_return_1d", sa.Float, nullable=True),
        sa.Column("btc_eth_corr_30d", sa.Float, nullable=True),
        sa.Column("btc_gold_corr_30d", sa.Float, nullable=True),
        sa.Column("hashrate_change_1d", sa.Float, nullable=True),
        sa.Column("tx_count_change_1d", sa.Float, nullable=True),
        sa.Column("active_addr_change_1d", sa.Float, nullable=True),
        sa.Column("vader_mean", sa.Float, nullable=True),
        sa.Column("vader_7d_rolling", sa.Float, nullable=True),
        sa.Column("finbert_mean", sa.Float, nullable=True),
        sa.Column("finbert_7d_rolling", sa.Float, nullable=True),
        sa.Column("article_count", sa.Integer, nullable=True),
        sa.Column("day_of_week", sa.Integer, nullable=True),
        sa.Column("month", sa.Integer, nullable=True),
        sa.Column("days_since_halving", sa.Integer, nullable=True),
        sa.Column("target_next_close", sa.Float, nullable=True),
        sa.Column("target_next_log_return", sa.Float, nullable=True),
    )

    # ── predictions ───────────────────────────────────────────────────────────
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("prediction_date", sa.Date, nullable=False),  # date prediction was made
        sa.Column("target_date", sa.Date, nullable=False),       # date being predicted
        sa.Column("predicted_price", sa.Float, nullable=False),
        sa.Column("predicted_log_return", sa.Float, nullable=True),
        sa.Column("confidence_lower", sa.Float, nullable=True),
        sa.Column("confidence_upper", sa.Float, nullable=True),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("actual_price", sa.Float, nullable=True),       # filled next day
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prediction_date", "target_date", name="uq_prediction_dates"),
    )

    # ── model_metadata ────────────────────────────────────────────────────────
    op.create_table(
        "model_metadata",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_rows", sa.Integer, nullable=True),
        sa.Column("val_rows", sa.Integer, nullable=True),
        sa.Column("rmse", sa.Float, nullable=True),
        sa.Column("mae", sa.Float, nullable=True),
        sa.Column("mape", sa.Float, nullable=True),
        sa.Column("directional_accuracy", sa.Float, nullable=True),
        sa.Column("naive_rmse", sa.Float, nullable=True),  # "tomorrow = today" baseline
        sa.Column("feature_importances", sa.JSON, nullable=True),
        sa.Column("artifact_path", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
    )

    # ── job_runs ──────────────────────────────────────────────────────────────
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # started | success | failure
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("rows_processed", sa.Integer, nullable=True),
    )
    op.create_index("ix_job_runs_job_name_started", "job_runs", ["job_name", "started_at"])


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("model_metadata")
    op.drop_table("predictions")
    op.drop_table("features")
    op.drop_table("news_sentiment")
    op.drop_table("news_raw")
    op.drop_table("onchain_metrics")
    op.drop_table("market_data")
    op.drop_table("prices")
