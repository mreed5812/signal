"""Scheduler entry-point for the NewsAPI ingestion worker."""

from src.sources.newsapi import NewsAPISource

if __name__ == "__main__":
    NewsAPISource().run()
