"""
Tests for bid/ask extraction in funding rate feeds.
Issue #44: Add best_bid and best_ask to funding rate data.
"""
import math
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd

import sys
import os
# conftest.py stubs hummingbot before this file runs
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_bid_ask(df: pd.DataFrame):
    assert "best_bid" in df.columns, f"Missing best_bid column. Columns: {df.columns.tolist()}"
    assert "best_ask" in df.columns, f"Missing best_ask column. Columns: {df.columns.tolist()}"


def _is_float_or_nan(v):
    return isinstance(v, float)


# ---------------------------------------------------------------------------
# Hyperliquid
# ---------------------------------------------------------------------------

class TestHyperliquidBidAsk:
    """HL feed: best_bid from impactPxs[0], best_ask from impactPxs[1]."""

    def _make_api_response(self, assets):
        """Build a mock metaAndAssetCtxs response."""
        meta = {"universe": [{"name": a["name"]} for a in assets]}
        ctxs = []
        for a in assets:
            ctx = {
                "funding": a.get("funding", "0.0001"),
                "markPx": a.get("markPx", "100.0"),
                "impactPxs": a.get("impactPxs", None),
                "midPx": a.get("midPx", None),
            }
            # Remove None values to simulate real API
            ctx = {k: v for k, v in ctx.items() if v is not None}
            ctxs.append(ctx)
        return [meta, ctxs]

    @pytest.mark.asyncio
    async def test_bid_ask_from_impact_pxs(self):
        """When impactPxs present, best_bid=impactPxs[0], best_ask=impactPxs[1]."""
        from core.data_sources.market_feeds.hyperliquid_perpetual.hyperliquid_perpetual_funding_rate_feed import HyperliquidPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {"name": "BTC", "funding": "0.0001", "markPx": "50000", "impactPxs": ["49990", "50010"]},
            {"name": "ETH", "funding": "0.0002", "markPx": "3000",  "impactPxs": ["2999", "3001"]},
        ])

        feed = HyperliquidPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 2
        btc = next(r for r in rows if r["trading_pair"] == "BTC")
        assert btc["best_bid"] == pytest.approx(49990.0)
        assert btc["best_ask"] == pytest.approx(50010.0)

        eth = next(r for r in rows if r["trading_pair"] == "ETH")
        assert eth["best_bid"] == pytest.approx(2999.0)
        assert eth["best_ask"] == pytest.approx(3001.0)

    @pytest.mark.asyncio
    async def test_fallback_to_mid_px_when_impact_missing(self):
        """When impactPxs missing, fall back to midPx for both sides."""
        from core.data_sources.market_feeds.hyperliquid_perpetual.hyperliquid_perpetual_funding_rate_feed import HyperliquidPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {"name": "SOL", "funding": "0.0003", "markPx": "150", "midPx": "149.5"},
        ])

        feed = HyperliquidPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        sol = rows[0]
        assert sol["best_bid"] == pytest.approx(149.5)
        assert sol["best_ask"] == pytest.approx(149.5)

    @pytest.mark.asyncio
    async def test_nan_when_neither_impact_nor_mid(self):
        """When both impactPxs and midPx missing, best_bid and best_ask are NaN."""
        from core.data_sources.market_feeds.hyperliquid_perpetual.hyperliquid_perpetual_funding_rate_feed import HyperliquidPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {"name": "AVAX", "funding": "0.0001", "markPx": "30"},
        ])

        feed = HyperliquidPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        avax = rows[0]
        assert math.isnan(avax["best_bid"])
        assert math.isnan(avax["best_ask"])

    @pytest.mark.asyncio
    async def test_dataframe_has_bid_ask_columns(self):
        """get_all_funding_rates() DataFrame includes best_bid and best_ask."""
        from core.data_sources.market_feeds.hyperliquid_perpetual.hyperliquid_perpetual_funding_rate_feed import HyperliquidPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {"name": "BTC", "funding": "0.0001", "markPx": "50000", "impactPxs": ["49990", "50010"]},
        ])

        feed = HyperliquidPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            df = await feed.get_all_funding_rates()

        _has_bid_ask(df)
        assert df.iloc[0]["best_bid"] == pytest.approx(49990.0)
        assert df.iloc[0]["best_ask"] == pytest.approx(50010.0)


# ---------------------------------------------------------------------------
# Extended
# ---------------------------------------------------------------------------

class TestExtendedBidAsk:
    """Extended feed: best_bid from bidPrice, best_ask from askPrice."""

    def _make_api_response(self, items):
        return {"data": items}

    @pytest.mark.asyncio
    async def test_bid_ask_from_market_stats(self):
        """best_bid and best_ask extracted from marketStats.bidPrice / askPrice."""
        from core.data_sources.market_feeds.extended_perpetual.extended_perpetual_funding_rate_feed import ExtendedPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {
                "active": True,
                "assetName": "BTC",
                "marketStats": {
                    "fundingRate": "0.0001",
                    "markPrice": "50000",
                    "indexPrice": "49990",
                    "bidPrice": "49985",
                    "askPrice": "50015",
                },
            },
        ])

        feed = ExtendedPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["best_bid"] == pytest.approx(49985.0)
        assert rows[0]["best_ask"] == pytest.approx(50015.0)

    @pytest.mark.asyncio
    async def test_nan_when_bid_ask_missing(self):
        """When bidPrice/askPrice absent, best_bid/best_ask are NaN."""
        from core.data_sources.market_feeds.extended_perpetual.extended_perpetual_funding_rate_feed import ExtendedPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {
                "active": True,
                "assetName": "ETH",
                "marketStats": {
                    "fundingRate": "0.0002",
                    "markPrice": "3000",
                    "indexPrice": "2998",
                },
            },
        ])

        feed = ExtendedPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])

    @pytest.mark.asyncio
    async def test_dataframe_has_bid_ask_columns(self):
        """get_all_funding_rates() DataFrame includes best_bid and best_ask."""
        from core.data_sources.market_feeds.extended_perpetual.extended_perpetual_funding_rate_feed import ExtendedPerpetualFundingRateFeed

        api_data = self._make_api_response([
            {
                "active": True,
                "assetName": "SOL",
                "marketStats": {
                    "fundingRate": "0.0003",
                    "markPrice": "150",
                    "bidPrice": "149",
                    "askPrice": "151",
                },
            },
        ])

        feed = ExtendedPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            df = await feed.get_all_funding_rates()

        _has_bid_ask(df)
        assert df.iloc[0]["best_bid"] == pytest.approx(149.0)
        assert df.iloc[0]["best_ask"] == pytest.approx(151.0)


# ---------------------------------------------------------------------------
# Lighter
# ---------------------------------------------------------------------------

class TestLighterBidAsk:
    """Lighter feed: best_bid/best_ask from exchangeStats if available, NaN otherwise."""

    def _make_funding_response(self, items):
        return {"funding_rates": items}

    def _make_exchange_stats_response(self, items):
        return {"order_book_stats": items}

    @pytest.mark.asyncio
    async def test_bid_ask_from_exchange_stats(self):
        """best_bid from bid_price, best_ask from ask_price in exchangeStats."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"symbol": "BTC-PERP", "rate": "0.0008", "exchange": "lighter"},
        ])
        stats_data = self._make_exchange_stats_response([
            {"symbol": "BTC/USDC", "last_trade_price": "50000", "bid_price": "49990", "ask_price": "50010"},
        ])

        feed = LighterPerpetualFundingRateFeed()
        call_count = [0]
        async def mock_request(method, url, **kwargs):
            call_count[0] += 1
            if "funding-rates" in url:
                return funding_data
            return stats_data

        with patch.object(feed, "_make_request", new=mock_request):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["best_bid"] == pytest.approx(49990.0)
        assert rows[0]["best_ask"] == pytest.approx(50010.0)

    @pytest.mark.asyncio
    async def test_nan_when_bid_ask_missing_in_stats(self):
        """NaN when exchangeStats lacks bid_price/ask_price."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"symbol": "ETH-PERP", "rate": "0.0008", "exchange": "lighter"},
        ])
        stats_data = self._make_exchange_stats_response([
            {"symbol": "ETH/USDC", "last_trade_price": "3000"},
        ])

        feed = LighterPerpetualFundingRateFeed()
        async def mock_request(method, url, **kwargs):
            if "funding-rates" in url:
                return funding_data
            return stats_data

        with patch.object(feed, "_make_request", new=mock_request):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])

    @pytest.mark.asyncio
    async def test_no_extra_api_calls(self):
        """Bid/ask reuses the existing exchangeStats call - no new endpoints."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"symbol": "BTC-PERP", "rate": "0.0008", "exchange": "lighter"},
        ])
        stats_data = self._make_exchange_stats_response([
            {"symbol": "BTC/USDC", "last_trade_price": "50000", "bid_price": "49990", "ask_price": "50010"},
        ])

        urls_called = []
        feed = LighterPerpetualFundingRateFeed()
        async def mock_request(method, url, **kwargs):
            urls_called.append(url)
            if "funding-rates" in url:
                return funding_data
            return stats_data

        with patch.object(feed, "_make_request", new=mock_request):
            await feed._fetch_funding_rates()

        # Only 2 known endpoints: funding-rates + exchangeStats
        unique_urls = set(urls_called)
        known_paths = {"funding-rates", "exchangeStats"}
        for url in unique_urls:
            assert any(p in url for p in known_paths), f"Unexpected URL called: {url}"


# ---------------------------------------------------------------------------
# Pacifica
# ---------------------------------------------------------------------------

class TestPacificaBidAsk:
    """Pacifica feed: best_bid/best_ask from response if available, NaN otherwise."""

    @pytest.mark.asyncio
    async def test_bid_ask_when_available(self):
        """best_bid and best_ask extracted from bid/ask fields if present."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        api_data = [
            {"symbol": "BTC-PERP", "funding": "0.0001", "mark": "50000", "bid": "49990", "ask": "50010"},
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["best_bid"] == pytest.approx(49990.0)
        assert rows[0]["best_ask"] == pytest.approx(50010.0)

    @pytest.mark.asyncio
    async def test_nan_when_bid_ask_absent(self):
        """NaN when API response lacks bid/ask fields."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        api_data = [
            {"symbol": "ETH-PERP", "funding": "0.0002", "mark": "3000"},
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])


# ---------------------------------------------------------------------------
# FundingRateCollector
# ---------------------------------------------------------------------------

class TestFundingRateCollector:
    """Collector.collect() must include best_bid, best_ask columns."""

    @pytest.mark.asyncio
    async def test_collect_includes_bid_ask_columns(self):
        """collect() DataFrame has best_bid and best_ask columns."""
        from core.data_sources.funding_rate_collector import FundingRateCollector

        # Minimal mock DataFrame from a feed
        mock_df = pd.DataFrame([{
            "timestamp": pd.Timestamp.utcnow(),
            "trading_pair": "BTC",
            "funding_rate": 0.0001,
            "mark_price": 50000.0,
            "index_price": 49990.0,
            "best_bid": 49985.0,
            "best_ask": 50015.0,
        }])

        collector = FundingRateCollector()
        for feed in collector.feeds:
            feed.get_all_funding_rates = AsyncMock(return_value=mock_df)
            feed.get_funding_rates = AsyncMock(return_value=mock_df)

        df = await collector.collect()
        await collector.close()

        _has_bid_ask(df)
        assert "funding_rate_1h" in df.columns

    @pytest.mark.asyncio
    async def test_collect_bid_ask_values_preserved(self):
        """Bid/ask values from feeds are propagated through collect()."""
        from core.data_sources.funding_rate_collector import FundingRateCollector

        mock_df = pd.DataFrame([{
            "timestamp": pd.Timestamp.utcnow(),
            "trading_pair": "ETH",
            "funding_rate": 0.0002,
            "mark_price": 3000.0,
            "index_price": 2990.0,
            "best_bid": 2995.0,
            "best_ask": 3005.0,
        }])

        collector = FundingRateCollector()
        for feed in collector.feeds:
            feed.get_all_funding_rates = AsyncMock(return_value=mock_df)
            feed.get_funding_rates = AsyncMock(return_value=mock_df)

        df = await collector.collect()
        await collector.close()

        eth_rows = df[df["trading_pair"] == "ETH"]
        assert not eth_rows.empty
        assert eth_rows.iloc[0]["best_bid"] == pytest.approx(2995.0)
        assert eth_rows.iloc[0]["best_ask"] == pytest.approx(3005.0)

    @pytest.mark.asyncio
    async def test_empty_dataframe_has_bid_ask_columns(self):
        """Empty collect() result still includes best_bid, best_ask columns."""
        from core.data_sources.funding_rate_collector import FundingRateCollector

        collector = FundingRateCollector()
        for feed in collector.feeds:
            feed.get_all_funding_rates = AsyncMock(return_value=pd.DataFrame())
            feed.get_funding_rates = AsyncMock(return_value=pd.DataFrame())

        df = await collector.collect()
        await collector.close()

        _has_bid_ask(df)
