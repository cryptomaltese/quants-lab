import math

from .extended_perpetual_base import ExtendedPerpetualBase


class ExtendedPerpetualFundingRateFeed(ExtendedPerpetualBase):
    """Funding-rate feed for Extended Exchange (StarkNet).

    API: GET /info/markets  →  data[]  with marketStats.fundingRate (hourly).
    """

    VENUE = "extended"

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/info/markets"
        data = await self._make_request("GET", url, limit_id="extended_general")
        if not data:
            return []

        items = data.get("data", []) if isinstance(data, dict) else []
        rows = []
        for item in items:
            if not item.get("active"):
                continue
            ms = item.get("marketStats", {})
            fr = ms.get("fundingRate")
            if fr is None:
                continue
            try:
                funding_rate = float(fr)  # already per-hour
            except (ValueError, TypeError):
                continue

            mark = _safe_float(ms.get("markPrice"))
            index = _safe_float(ms.get("indexPrice"))
            # fall back to bid/ask midpoint for index price
            if index is None or (isinstance(index, float) and math.isnan(index)):
                bid = _safe_float(ms.get("bidPrice"))
                ask = _safe_float(ms.get("askPrice"))
                if bid is not None and ask is not None:
                    index = (bid + ask) / 2.0

            best_bid = _safe_float(ms.get("bidPrice"))
            best_ask = _safe_float(ms.get("askPrice"))
            rows.append({
                "trading_pair": self._norm(item.get("assetName", "")),
                "funding_rate": funding_rate,
                "mark_price": mark if mark is not None else float("nan"),
                "index_price": index if index is not None else float("nan"),
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
