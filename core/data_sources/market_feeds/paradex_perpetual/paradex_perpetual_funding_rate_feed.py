from .paradex_perpetual_base import ParadexPerpetualBase

# Paradex uses 8-hour funding period
_FUNDING_PERIOD_HOURS = 8


class ParadexPerpetualFundingRateFeed(ParadexPerpetualBase):
    """Funding-rate feed for Paradex perpetuals.

    API: GET /markets/summary?market=ALL
    Returns {"results": [...]}.
    Each item has: symbol (BTC-USD-PERP format), mark_price, funding_rate,
    optionally bid/ask.

    Rates from this endpoint are per-8h — normalise to per-hour (÷8).
    Entries with zero funding rate are skipped (exchange convention).
    Bid/ask: extracted from summary 'bid'/'ask' fields if present, else NaN.
    No auth needed.
    """

    VENUE = "paradex"

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/markets/summary"
        data = await self._make_request(
            "GET", url,
            params={"market": "ALL"},
            limit_id="paradex_general",
        )
        if not data:
            return []

        results = data.get("results", []) if isinstance(data, dict) else []

        rows = []
        for item in results:
            if not isinstance(item, dict):
                continue
            exchange_symbol = item.get("symbol", "")
            if not exchange_symbol:
                continue

            raw_rate = item.get("funding_rate")
            if raw_rate is None:
                continue
            try:
                rate_8h = float(raw_rate)
            except (ValueError, TypeError):
                continue

            # Skip zero rates (exchange convention)
            if rate_8h == 0:
                continue

            # Normalize symbol: "BTC-USD-PERP" → "BTC"
            sym = exchange_symbol.split("-")[0].upper()

            mark = _safe_float(item.get("mark_price"))

            best_bid = _safe_float(item.get("bid"))
            best_ask = _safe_float(item.get("ask"))

            rows.append({
                "trading_pair": sym,
                "funding_rate": rate_8h / _FUNDING_PERIOD_HOURS,
                "mark_price": mark if mark is not None else float("nan"),
                "index_price": float("nan"),
                "best_bid": best_bid if best_bid is not None else float("nan"),
                "best_ask": best_ask if best_ask is not None else float("nan"),
            })
        return rows


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
