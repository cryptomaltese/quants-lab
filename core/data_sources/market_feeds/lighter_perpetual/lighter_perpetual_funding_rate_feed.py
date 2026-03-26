from .lighter_perpetual_base import LighterPerpetualBase


class LighterPerpetualFundingRateFeed(LighterPerpetualBase):
    """Funding-rate feed for Lighter DEX.

    API: GET /api/v1/funding-rates
    Returns {"funding_rates": [...]}.
    Each item has: symbol, rate, exchange.
    We only keep items where exchange == "lighter".
    Rates from this endpoint are per-8h — normalise to per-hour (÷8).
    """

    VENUE = "lighter"

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/api/v1/funding-rates"
        data = await self._make_request("GET", url, limit_id="lighter_general")
        if not data:
            return []

        items = data.get("funding_rates", []) if isinstance(data, dict) else data
        # Also fetch prices (mark + bid/ask) — reuses the existing exchangeStats call
        prices = await self._fetch_prices()

        rows = []
        for item in items:
            if item.get("exchange") != "lighter":
                continue
            sym_raw = item.get("symbol", "")
            sym = sym_raw.split("-")[0].upper()
            rate = item.get("rate", item.get("funding_rate"))
            if sym and rate is not None:
                try:
                    funding_rate_8h = float(rate)
                except (ValueError, TypeError):
                    continue
                price_info = prices.get(sym, {})
                rows.append({
                    "trading_pair": sym,
                    "funding_rate": funding_rate_8h / 8.0,  # normalise to per-hour
                    "mark_price": price_info.get("mark_price", float("nan")),
                    "index_price": float("nan"),
                    "best_bid": price_info.get("best_bid", float("nan")),
                    "best_ask": price_info.get("best_ask", float("nan")),
                })
        return rows

    async def _fetch_prices(self) -> dict[str, dict]:
        """Fetch exchangeStats and return per-symbol dict with mark/bid/ask prices.

        No new API calls — reuses the existing /api/v1/exchangeStats endpoint.
        """
        url = f"{self._base_url}/api/v1/exchangeStats"
        data = await self._make_request("GET", url, limit_id="lighter_general")
        if not data:
            return {}
        result = {}
        items = data.get("order_book_stats", []) if isinstance(data, dict) else data
        for item in items:
            sym = item.get("symbol", "").split("/")[0].upper()
            if not sym:
                continue
            info = {}
            price = item.get("last_trade_price")
            if price is not None:
                try:
                    p = float(price)
                    if p > 0:
                        info["mark_price"] = p
                except (ValueError, TypeError):
                    pass
            bid = _safe_float(item.get("bid_price"))
            ask = _safe_float(item.get("ask_price"))
            if bid is not None:
                info["best_bid"] = bid
            if ask is not None:
                info["best_ask"] = ask
            result[sym] = info
        return result


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
