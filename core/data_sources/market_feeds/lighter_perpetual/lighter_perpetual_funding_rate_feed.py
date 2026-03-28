import asyncio
from .lighter_perpetual_base import LighterPerpetualBase


class LighterPerpetualFundingRateFeed(LighterPerpetualBase):
    """Funding-rate feed for Lighter DEX.

    API: GET /api/v1/funding-rates
    Returns {"funding_rates": [...]}.
    Each item has: symbol, rate, exchange, market_id.
    We only keep items where exchange == "lighter".
    Rates from this endpoint are per-8h — normalise to per-hour (÷8).

    Bid/ask: fetched per-symbol from /api/v1/orderBookOrders?market_id=N&limit=1
    using a semaphore (max 5 concurrent) to avoid overwhelming the API.
    market_id is taken directly from the funding-rates response.
    """

    VENUE = "lighter"

    _ORDERBOOK_CONCURRENCY = 5

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/api/v1/funding-rates"
        data = await self._make_request("GET", url, limit_id="lighter_general")
        if not data:
            return []

        items = data.get("funding_rates", []) if isinstance(data, dict) else data

        # Collect lighter entries with their market_ids
        entries = []
        for item in items:
            if item.get("exchange") != "lighter":
                continue
            sym_raw = item.get("symbol", "")
            sym = sym_raw.split("-")[0].upper()
            rate = item.get("rate", item.get("funding_rate"))
            market_id = item.get("market_id")
            if sym and rate is not None:
                try:
                    funding_rate_8h = float(rate)
                except (ValueError, TypeError):
                    continue
                entries.append({
                    "symbol": sym,
                    "funding_rate": funding_rate_8h / 8.0,
                    "market_id": market_id,
                })

        if not entries:
            return []

        # Fetch orderbooks concurrently (max 5 at a time)
        semaphore = asyncio.Semaphore(self._ORDERBOOK_CONCURRENCY)
        bid_ask_map = await self._fetch_orderbooks(entries, semaphore)

        rows = []
        for entry in entries:
            sym = entry["symbol"]
            bid, ask = bid_ask_map.get(sym, (float("nan"), float("nan")))
            rows.append({
                "trading_pair": sym,
                "funding_rate": entry["funding_rate"],
                "mark_price": float("nan"),
                "index_price": float("nan"),
                "best_bid": bid,
                "best_ask": ask,
            })
        return rows

    async def _fetch_orderbooks(
        self,
        entries: list[dict],
        semaphore: asyncio.Semaphore,
    ) -> dict[str, tuple]:
        """Fetch /api/v1/orderBookOrders for each symbol concurrently.

        Returns {symbol: (best_bid, best_ask)} mapping.
        Falls back to (NaN, NaN) on error.
        """
        async def fetch_one(entry: dict) -> tuple[str, tuple]:
            sym = entry["symbol"]
            market_id = entry.get("market_id")
            if market_id is None:
                return sym, (float("nan"), float("nan"))
            url = f"{self._base_url}/api/v1/orderBookOrders"
            async with semaphore:
                try:
                    data = await self._make_request(
                        "GET", url,
                        params={"market_id": str(market_id), "limit": "1"},
                        limit_id="lighter_general",
                    )
                    bid, ask = _parse_ob_best_prices(data)
                    return sym, (
                        bid if bid is not None else float("nan"),
                        ask if ask is not None else float("nan"),
                    )
                except Exception:
                    return sym, (float("nan"), float("nan"))

        results = await asyncio.gather(*(fetch_one(e) for e in entries))
        return dict(results)


def _parse_ob_best_prices(response) -> tuple:
    """Extract (best_bid, best_ask) from orderBookOrders response.

    Response shape: {
        "bids": [{"price": "X", ...}, ...],
        "asks": [{"price": "Y", ...}, ...],
    }
    Returns (float|None, float|None).
    """
    if not isinstance(response, dict):
        return (None, None)

    best_bid = None
    best_ask = None

    bids = response.get("bids") or []
    if bids and isinstance(bids[0], dict):
        try:
            best_bid = float(bids[0]["price"])
        except (KeyError, ValueError, TypeError):
            pass

    asks = response.get("asks") or []
    if asks and isinstance(asks[0], dict):
        try:
            best_ask = float(asks[0]["price"])
        except (KeyError, ValueError, TypeError):
            pass

    return (best_bid, best_ask)
