"""Scheduler entry-point for the CoinGecko ingestion worker."""

from src.sources.coingecko import CoinGeckoSource

if __name__ == "__main__":
    CoinGeckoSource().run()
