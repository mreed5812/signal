"""Scheduler entry-point for the Yahoo Finance ingestion worker."""

from src.sources.yahoo import YahooFinanceSource

if __name__ == "__main__":
    YahooFinanceSource().run()
