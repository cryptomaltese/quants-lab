import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd

from .market_feeds.extended_perpetual.extended_perpetual_funding_rate_feed import ExtendedPerpetualFundingRateFeed
from .market_feeds.hyperliquid_perpetual.hyperliquid_perpetual_funding_rate_feed import HyperliquidPerpetualFundingRateFeed
from .market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed
from .market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

logger = logging.getLogger(__name__)


class FundingRateCollector:
    """Polls all venue feeds and returns a unified DataFrame."""

    def __init__(self):
        self.feeds = [
            ExtendedPerpetualFundingRateFeed(),
            HyperliquidPerpetualFundingRateFeed(),
            LighterPerpetualFundingRateFeed(),
            PacificaPerpetualFundingRateFeed(),
        ]

    async def collect(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """Collect funding rates from all venues concurrently.

        Returns a DataFrame with columns:
            timestamp, venue, trading_pair, funding_rate_1h, mark_price, index_price
        """
        tasks = []
        for feed in self.feeds:
            if symbols:
                tasks.append(feed.get_funding_rates(symbols))
            else:
                tasks.append(feed.get_all_funding_rates())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        dfs = []
        for feed, result in zip(self.feeds, results):
            if isinstance(result, Exception):
                logger.warning("Feed %s failed: %s", feed.VENUE, result)
                continue
            if result is None or result.empty:
                continue
            result = result.copy()
            result["venue"] = feed.VENUE
            dfs.append(result)

        if not dfs:
            return pd.DataFrame(columns=[
                "timestamp", "venue", "trading_pair",
                "funding_rate_1h", "mark_price", "index_price",
                "best_bid", "best_ask",
            ])

        combined = pd.concat(dfs, ignore_index=True)
        combined.rename(columns={"funding_rate": "funding_rate_1h"}, inplace=True)
        # Ensure best_bid/best_ask columns exist even if all feeds omitted them
        for col in ("best_bid", "best_ask"):
            if col not in combined.columns:
                combined[col] = float("nan")
        return combined[["timestamp", "venue", "trading_pair", "funding_rate_1h", "mark_price", "index_price", "best_bid", "best_ask"]]

    async def close(self):
        for feed in self.feeds:
            await feed.close()
