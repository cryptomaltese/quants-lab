from .pacifica_perpetual_base import PacificaPerpetualBase


class PacificaPerpetualFundingRateFeed(PacificaPerpetualBase):
    """Funding-rate feed for Pacifica (Solana).

    API: GET /info/prices
    Returns list/dict with items containing: symbol, funding, mark, mid.
    ``funding`` is per-hour — no normalisation needed.

    Bid/ask: The /api/v1/info/prices endpoint returns `mid`, `mark`, `oracle`
    but does NOT expose `bid`/`ask`. Use `mid` as both bid and ask
    (spread=0 approximation). If `bid`/`ask` are present (future API upgrade),
    prefer those over `mid`.
    """

    VENUE = "pacifica"

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/info/prices"
        data = await self._make_request("GET", url, limit_id="pacifica_general")
        if not data:
            return []

        items = data if isinstance(data, list) else data.get("data", [])

        rows = []
        for item in items:
            fr = item.get("funding")
            if fr is None:
                continue
            sym = self._norm(item.get("symbol", "").split("-")[0])
            try:
                funding_rate = float(fr)  # already per-hour
            except (ValueError, TypeError):
                continue
            mark = _safe_float(item.get("mark"))

            # Prefer explicit bid/ask if present (future-proof),
            # otherwise fall back to mid (spread=0 approximation).
            # Note: /api/v1/info/prices does not expose bid/ask as of 2026-03.
            best_bid = _safe_float(item.get("bid"))
            best_ask = _safe_float(item.get("ask"))
            if best_bid is None or best_ask is None:
                mid = _safe_float(item.get("mid"))
                if mid is not None:
                    best_bid = mid
                    best_ask = mid

            rows.append({
                "trading_pair": sym,
                "funding_rate": funding_rate,
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
