"""Historical backfill script — runs once on first deployment.

Idempotent: safe to re-run; uses ON CONFLICT DO NOTHING throughout.
Targets 5+ years of history where free tiers allow it.
"""

from src.common.logging import configure_logging, get_logger
from src.sources.coingecko import CoinGeckoSource
from src.sources.onchain import OnChainSource
from src.sources.yahoo import YahooFinanceSource
from src.sources.newsapi import NewsAPISource

configure_logging()
log = get_logger("backfill")


def main() -> None:
    log.info("backfill_start")

    sources = [
        CoinGeckoSource(),
        OnChainSource(),
        YahooFinanceSource(),
        NewsAPISource(),   # free tier caps at 30 days — documented limitation
    ]

    for source in sources:
        log.info("backfilling_source", source=source.name)
        try:
            source.run(historical=True)
            log.info("source_complete", source=source.name)
        except Exception as exc:
            log.error("source_failed", source=source.name, error=str(exc), exc_info=True)
            # Continue with other sources even if one fails

    log.info("backfill_complete")


if __name__ == "__main__":
    main()
