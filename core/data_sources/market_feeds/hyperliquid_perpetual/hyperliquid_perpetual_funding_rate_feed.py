from .hyperliquid_perpetual_base import HyperliquidPerpetualBase


class HyperliquidPerpetualFundingRateFeed(HyperliquidPerpetualBase):
    """Funding-rate feed for Hyperliquid.

    API: POST /info  {"type":"metaAndAssetCtxs"}
    Returns [meta, asset_contexts].
    asset_contexts[i].funding is the per-hour rate.
    meta.universe[i].name is the symbol.
    """

    VENUE = "hyperliquid"

    async def _fetch_funding_rates(self) -> list[dict]:
        url = f"{self._base_url}/info"
        data = await self._make_request(
            "POST", url, data={"type": "metaAndAssetCtxs"},
            limit_id="hyperliquid_general",
        )
        if not data or not isinstance(data, list) or len(data) < 2:
            return []

        meta_universe = data[0].get("universe", [])
        asset_ctxs = data[1]

        rows = []
        for meta, ctx in zip(meta_universe, asset_ctxs):
            sym = meta.get("name", "").upper()
            fr = ctx.get("funding")
            if sym and fr is not None:
                try:
                    funding_rate = float(fr)  # already per-hour
                except (ValueError, TypeError):
                    continue
                mark = _safe_float(ctx.get("markPx"))
                rows.append({
                    "trading_pair": sym,
                    "funding_rate": funding_rate,
                    "mark_price": mark if mark is not None else float("nan"),
                    "index_price": float("nan"),  # not directly available
                })
        return rows


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
