"""Scheduler entry-point for the on-chain metrics ingestion worker."""

from src.sources.onchain import OnChainSource

if __name__ == "__main__":
    OnChainSource().run()
