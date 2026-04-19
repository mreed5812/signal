"""NewsAPI.org headline ingestion.

Free-tier limits:
- 100 requests/day; historical depth capped at ~30 days.
- Quota exhaustion: we log a warning and stop fetching rather than crashing.
- No backfill beyond 30 days is possible on the free tier; sentiment features
  for older periods use a neutral prior (handled in the feature builder).

Requires env var: NEWS_API_KEY
"""

import os
import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.common.config import settings
from src.common.database import get_connection
from src.common.logging import get_logger
from src.sources.base import DataSource

log = get_logger(__name__)

_BASE_URL = "https://newsapi.org/v2/everything"
_QUERIES = ["bitcoin", "cryptocurrency", "crypto regulation"]
_MAX_HISTORY_DAYS = 29  # free tier caps at 30; use 29 to be safe


class NewsAPISource(DataSource):
    """Fetches news headlines from NewsAPI.org."""

    name = "newsapi"

    def __init__(self) -> None:
        self.api_key = settings.news_api_key
        if not self.api_key:
            log.warning("no_api_key", source=self.name, msg="NEWS_API_KEY not set — skipping")

    @retry(wait=wait_exponential(multiplier=1, min=10, max=120), stop=stop_after_attempt(3))
    def _fetch_page(self, query: str, from_dt: str, to_dt: str, page: int) -> dict:  # type: ignore[type-arg]
        resp = requests.get(
            _BASE_URL,
            params={
                "q": query,
                "from": from_dt,
                "to": to_dt,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 100,
                "page": page,
                "apiKey": self.api_key,
            },
            timeout=15,
        )
        if resp.status_code == 426:
            log.warning("quota_exhausted", source=self.name)
            return {"status": "quota_exhausted", "articles": []}
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def _fetch_query(self, query: str, start: datetime, end: datetime) -> list[dict]:  # type: ignore[type-arg]
        from_str = start.strftime("%Y-%m-%dT%H:%M:%S")
        to_str = end.strftime("%Y-%m-%dT%H:%M:%S")
        articles: list[dict] = []  # type: ignore[type-arg]
        page = 1
        while True:
            data = self._fetch_page(query, from_str, to_str, page)
            if data.get("status") == "quota_exhausted":
                break
            batch = data.get("articles", [])
            articles.extend(batch)
            total = data.get("totalResults", 0)
            if len(articles) >= total or len(batch) == 0:
                break
            page += 1
            time.sleep(0.5)
        return articles

    def _articles_to_df(self, articles: list[dict], query: str) -> pd.DataFrame:  # type: ignore[type-arg]
        rows = []
        for a in articles:
            published_raw = a.get("publishedAt", "")
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            rows.append(
                {
                    "query": query,
                    "headline": (a.get("title") or "")[:512],
                    "description": (a.get("description") or "")[:1024],
                    "source_name": (a.get("source", {}) or {}).get("name", "")[:128],
                    "url": (a.get("url") or "")[:2048],
                    "published_at": published_at,
                    "sentiment_processed": False,
                }
            )
        return pd.DataFrame(rows)

    def fetch_historical(self, start: datetime, end: datetime) -> pd.DataFrame:
        if not self.api_key:
            return pd.DataFrame()
        # Clamp to 30-day free-tier window
        clamped_start = max(start, datetime.now(tz=timezone.utc) - timedelta(days=_MAX_HISTORY_DAYS))
        frames = []
        for query in _QUERIES:
            log.info("fetching_news", query=query)
            articles = self._fetch_query(query, clamped_start, end)
            df = self._articles_to_df(articles, query)
            frames.append(df)
            time.sleep(1)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(hours=16)  # cover the last ingestion window
        return self.fetch_historical(start, end)

    def store(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        records = df.to_dict("records")
        sql = text(
            """
            INSERT INTO news_raw
              (query, headline, description, source_name, url, published_at, sentiment_processed)
            VALUES
              (:query, :headline, :description, :source_name, :url,
               :published_at, :sentiment_processed)
            ON CONFLICT (url) DO NOTHING
            """
        )
        with get_connection() as conn:
            result = conn.execute(sql, records)
        return result.rowcount
