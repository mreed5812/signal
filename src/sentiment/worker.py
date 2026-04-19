"""Sentiment analysis worker — processes unscored news_raw rows.

Runs VADER (fast, lexicon-based) and FinBERT (finance-tuned transformer) on
every headline. Processes in batches of 32 for FinBERT GPU/CPU efficiency.
Model weights are cached in a Docker volume to avoid re-downloading.

Source credibility weights (hand-curated; adjust as needed):
    Reuters, Bloomberg, Financial Times: 1.5
    CoinDesk, CoinTelegraph: 1.2
    Default: 1.0
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import torch
from transformers import pipeline as hf_pipeline
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from sqlalchemy import text as sa_text

from src.common.database import get_connection
from src.common.logging import get_logger
from src.common.metrics import job_duration_seconds, job_runs

log = get_logger(__name__)

_BATCH_SIZE = 32
_FINBERT_MODEL = "ProsusAI/finbert"

_SOURCE_WEIGHTS: dict[str, float] = {
    "reuters": 1.5,
    "bloomberg": 1.5,
    "financial times": 1.5,
    "coindesk": 1.2,
    "cointelegraph": 1.2,
}


def _source_weight(source_name: str) -> float:
    key = (source_name or "").lower()
    for name, weight in _SOURCE_WEIGHTS.items():
        if name in key:
            return weight
    return 1.0


class SentimentWorker:
    """Scores unprocessed news headlines with VADER and FinBERT."""

    def __init__(self) -> None:
        self.vader = SentimentIntensityAnalyzer()
        log.info("loading_finbert", model=_FINBERT_MODEL)
        self._finbert = hf_pipeline(
            "text-classification",
            model=_FINBERT_MODEL,
            tokenizer=_FINBERT_MODEL,
            top_k=None,  # return all three labels
            device=-1,   # CPU — required for Pi 5 and ARM64
            torch_dtype=torch.float32,
        )
        log.info("finbert_loaded")

    def _fetch_unprocessed(self, limit: int = 1000) -> list[dict]:  # type: ignore[type-arg]
        sql = sa_text(
            """
            SELECT id, headline, source_name
            FROM news_raw
            WHERE sentiment_processed = false
            ORDER BY id
            LIMIT :limit
            """
        )
        with get_connection() as conn:
            rows = conn.execute(sql, {"limit": limit}).fetchall()
        return [dict(r._mapping) for r in rows]

    def _run_vader(self, text: str) -> dict[str, float]:
        scores = self.vader.polarity_scores(text)
        return {
            "vader_compound": scores["compound"],
            "vader_pos": scores["pos"],
            "vader_neg": scores["neg"],
            "vader_neu": scores["neu"],
        }

    def _run_finbert_batch(self, texts: list[str]) -> list[dict[str, float]]:
        raw = self._finbert(texts, truncation=True, max_length=512)
        results = []
        for item in raw:
            score_map = {entry["label"].lower(): entry["score"] for entry in item}
            label = max(score_map, key=lambda k: score_map[k])
            results.append(
                {
                    "finbert_positive": score_map.get("positive", 0.0),
                    "finbert_negative": score_map.get("negative", 0.0),
                    "finbert_neutral": score_map.get("neutral", 0.0),
                    "finbert_label": label,
                }
            )
        return results

    def process_batch(self, rows: list[dict]) -> int:  # type: ignore[type-arg]
        if not rows:
            return 0

        now = datetime.now(tz=timezone.utc)
        texts = [r["headline"] for r in rows]

        # FinBERT processes the whole batch at once
        finbert_results = self._run_finbert_batch(texts)

        sentiment_records = []
        news_ids = []
        for row, finbert in zip(rows, finbert_results):
            vader = self._run_vader(row["headline"])
            sentiment_records.append(
                {
                    "news_id": row["id"],
                    "processed_at": now,
                    **vader,
                    **finbert,
                }
            )
            news_ids.append(row["id"])

        insert_sql = sa_text(
            """
            INSERT INTO news_sentiment
              (news_id, vader_compound, vader_pos, vader_neg, vader_neu,
               finbert_positive, finbert_negative, finbert_neutral, finbert_label, processed_at)
            VALUES
              (:news_id, :vader_compound, :vader_pos, :vader_neg, :vader_neu,
               :finbert_positive, :finbert_negative, :finbert_neutral, :finbert_label, :processed_at)
            ON CONFLICT DO NOTHING
            """
        )
        update_sql = sa_text(
            "UPDATE news_raw SET sentiment_processed = true WHERE id = ANY(:ids)"
        )
        with get_connection() as conn:
            conn.execute(insert_sql, sentiment_records)
            conn.execute(update_sql, {"ids": news_ids})
        return len(rows)

    def run(self) -> None:
        start = time.time()
        total = 0
        try:
            while True:
                rows = self._fetch_unprocessed(limit=_BATCH_SIZE)
                if not rows:
                    break
                processed = self.process_batch(rows)
                total += processed
                log.info("batch_processed", count=processed)
            job_runs.labels(job="sentiment_worker", status="success").inc()
            log.info("sentiment_complete", total_processed=total)
        except Exception as exc:
            job_runs.labels(job="sentiment_worker", status="failure").inc()
            log.error("sentiment_failed", error=str(exc), exc_info=True)
            raise
        finally:
            job_duration_seconds.labels(job="sentiment_worker").observe(time.time() - start)


def main() -> None:
    worker = SentimentWorker()
    worker.run()


if __name__ == "__main__":
    main()
