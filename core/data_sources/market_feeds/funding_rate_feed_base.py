from abc import abstractmethod
from datetime import datetime, timezone

import pandas as pd

from .connector_base import ConnectorBase


class FundingRateFeedBase(ConnectorBase):
    """Abstract base for funding rate feeds.

    Subclasses implement ``_fetch_funding_rates`` which returns raw data.
    This base normalises it into a DataFrame with columns:
        timestamp, trading_pair, funding_rate, mark_price, index_price
    where ``funding_rate`` is **per-hour**.
    """

    @abstractmethod
    async def _fetch_funding_rates(self) -> list[dict]:
        """Return a list of dicts with keys:
        trading_pair, funding_rate (per-hour), mark_price, index_price (or NaN),
        best_bid (or NaN), best_ask (or NaN).
        """

    _COLUMNS = ["timestamp", "trading_pair", "funding_rate", "mark_price", "index_price", "best_bid", "best_ask"]

    async def get_all_funding_rates(self) -> pd.DataFrame:
        """Fetch funding rates for all available pairs."""
        rows = await self._fetch_funding_rates()
        if not rows:
            return pd.DataFrame(columns=self._COLUMNS)
        now = datetime.now(timezone.utc)
        for r in rows:
            r.setdefault("timestamp", now)
            r.setdefault("best_bid", float("nan"))
            r.setdefault("best_ask", float("nan"))
        df = pd.DataFrame(rows, columns=self._COLUMNS)
        return df

    async def get_funding_rates(self, trading_pairs: list[str]) -> pd.DataFrame:
        """Fetch funding rates filtered to *trading_pairs* (upper-cased symbols)."""
        df = await self.get_all_funding_rates()
        if df.empty:
            return df
        upper = {s.upper() for s in trading_pairs}
        return df[df["trading_pair"].isin(upper)].reset_index(drop=True)

    # ConnectorBase requires this — default identity for venues that use bare symbols.
    def get_exchange_trading_pair(self, trading_pair: str) -> str:
        return trading_pair
