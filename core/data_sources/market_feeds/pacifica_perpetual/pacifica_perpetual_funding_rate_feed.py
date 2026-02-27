from .pacifica_perpetual_base import PacificaPerpetualBase


class PacificaPerpetualFundingRateFeed(PacificaPerpetualBase):
    """Funding-rate feed for Pacifica (Solana).

    API: GET /info/prices
    Returns list/dict with items containing: symbol, funding, mark.
    ``funding`` is per-hour — no normalisation needed.
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
            sym = self._norm(item.get("symbol", ""))
            try:
                funding_rate = float(fr)  # already per-hour
            except (ValueError, TypeError):
                continue
            mark = _safe_float(item.get("mark"))
            rows.append({
                "trading_pair": sym,
                "funding_rate": funding_rate,
                "mark_price": mark if mark is not None else float("nan"),
                "index_price": float("nan"),
            })
        return rows


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
