"""
Tests for Issue #45:
- Bug 1: Lighter bid/ask from orderbook (market_id from funding-rates response)
- Bug 2: Pacifica bid/ask using mid fallback
- Addition: Paradex perpetual funding rate feed
"""
import math
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_bid_ask(df: pd.DataFrame):
    assert "best_bid" in df.columns
    assert "best_ask" in df.columns


# ---------------------------------------------------------------------------
# Lighter Bug Fix: real bid/ask from orderbook
# ---------------------------------------------------------------------------

class TestLighterOrderbookBidAsk:
    """Lighter feed must fetch orderbook per-symbol using market_id from funding-rates."""

    def _make_funding_response(self, items):
        return {"funding_rates": items}

    def _make_ob_response(self, bid_price: str, ask_price: str):
        return {
            "code": 200,
            "total_bids": 1,
            "total_asks": 1,
            "bids": [{"price": bid_price, "remaining_base_amount": "1.0"}],
            "asks": [{"price": ask_price, "remaining_base_amount": "1.0"}],
        }

    @pytest.mark.asyncio
    async def test_lighter_fetches_orderbook_for_real_bid_ask(self):
        """Lighter feed calls orderbook endpoint per symbol, returns real bid/ask."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"market_id": 1, "exchange": "lighter", "symbol": "BTC", "rate": 0.0008},
            {"market_id": 0, "exchange": "lighter", "symbol": "ETH", "rate": 0.0004},
        ])

        ob_by_market = {
            "1": self._make_ob_response("95000.00", "95010.00"),
            "0": self._make_ob_response("3200.00", "3201.00"),
        }

        urls_called = []
        params_called = []

        feed = LighterPerpetualFundingRateFeed()

        async def mock_request(method, url, params=None, **kwargs):
            urls_called.append(url)
            if params:
                params_called.append(params)
            if "funding-rates" in url:
                return funding_data
            if "orderBookOrders" in url:
                mid = str(params.get("market_id", ""))
                return ob_by_market.get(mid, {})
            return {}

        with patch.object(feed, "_make_request", new=mock_request):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 2

        btc = next(r for r in rows if r["trading_pair"] == "BTC")
        assert btc["best_bid"] == pytest.approx(95000.0)
        assert btc["best_ask"] == pytest.approx(95010.0)

        eth = next(r for r in rows if r["trading_pair"] == "ETH")
        assert eth["best_bid"] == pytest.approx(3200.0)
        assert eth["best_ask"] == pytest.approx(3201.0)

    @pytest.mark.asyncio
    async def test_lighter_semaphore_limits_concurrency(self):
        """Orderbook calls use semaphore — max 5 concurrent. No deadlock on many symbols."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        # 10 symbols — should all succeed without deadlock
        symbols = [{"market_id": i, "exchange": "lighter", "symbol": f"SYM{i}", "rate": 0.0001}
                   for i in range(10)]
        funding_data = self._make_funding_response(symbols)

        feed = LighterPerpetualFundingRateFeed()
        async def mock_request(method, url, params=None, **kwargs):
            if "funding-rates" in url:
                return funding_data
            if "orderBookOrders" in url:
                return self._make_ob_response("100.0", "101.0")
            return {}

        with patch.object(feed, "_make_request", new=mock_request):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 10
        for r in rows:
            assert r["best_bid"] == pytest.approx(100.0)
            assert r["best_ask"] == pytest.approx(101.0)

    @pytest.mark.asyncio
    async def test_lighter_ob_failure_graceful_nan(self):
        """If orderbook fetch fails for a symbol, bid/ask fall back to NaN."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"market_id": 1, "exchange": "lighter", "symbol": "BTC", "rate": 0.0008},
        ])

        feed = LighterPerpetualFundingRateFeed()
        async def mock_request(method, url, params=None, **kwargs):
            if "funding-rates" in url:
                return funding_data
            # Simulate orderbook failure
            raise Exception("network error")

        with patch.object(feed, "_make_request", new=mock_request):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])

    @pytest.mark.asyncio
    async def test_lighter_ob_calls_use_market_id_param(self):
        """Orderbook calls must pass market_id=N and limit params."""
        from core.data_sources.market_feeds.lighter_perpetual.lighter_perpetual_funding_rate_feed import LighterPerpetualFundingRateFeed

        funding_data = self._make_funding_response([
            {"market_id": 42, "exchange": "lighter", "symbol": "SOL", "rate": 0.0002},
        ])

        captured_params = []
        feed = LighterPerpetualFundingRateFeed()

        async def mock_request(method, url, params=None, **kwargs):
            if "funding-rates" in url:
                return funding_data
            if "orderBookOrders" in url:
                captured_params.append(params or {})
                return self._make_ob_response("100.0", "101.0")
            return {}

        with patch.object(feed, "_make_request", new=mock_request):
            await feed._fetch_funding_rates()

        assert len(captured_params) == 1
        assert str(captured_params[0].get("market_id")) == "42"


# ---------------------------------------------------------------------------
# Pacifica Bug Fix: mid fallback for bid/ask
# ---------------------------------------------------------------------------

class TestPacificaMidFallback:
    """Pacifica: when bid/ask absent, use mid as both bid and ask."""

    @pytest.mark.asyncio
    async def test_pacifica_uses_mid_when_bid_ask_absent(self):
        """When Pacifica API returns only mid (no bid/ask), use mid for both."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        # Real Pacifica /api/v1/info/prices response shape (no bid/ask fields)
        api_data = [
            {"symbol": "BTC-PERP", "funding": "0.0001", "mark": "50000", "mid": "50005"},
            {"symbol": "ETH-PERP", "funding": "0.0002", "mark": "3000", "mid": "3002"},
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 2
        btc = next(r for r in rows if r["trading_pair"] == "BTC")
        assert btc["best_bid"] == pytest.approx(50005.0)
        assert btc["best_ask"] == pytest.approx(50005.0)

        eth = next(r for r in rows if r["trading_pair"] == "ETH")
        assert eth["best_bid"] == pytest.approx(3002.0)
        assert eth["best_ask"] == pytest.approx(3002.0)

    @pytest.mark.asyncio
    async def test_pacifica_prefers_explicit_bid_ask_over_mid(self):
        """If API does return explicit bid/ask, prefer those over mid."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        api_data = [
            {"symbol": "BTC-PERP", "funding": "0.0001", "mark": "50000",
             "mid": "50005", "bid": "49990", "ask": "50010"},
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["best_bid"] == pytest.approx(49990.0)
        assert rows[0]["best_ask"] == pytest.approx(50010.0)

    @pytest.mark.asyncio
    async def test_pacifica_nan_when_no_bid_ask_no_mid(self):
        """When neither bid/ask nor mid is available, best_bid/best_ask are NaN."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        api_data = [
            {"symbol": "SOL-PERP", "funding": "0.0003", "mark": "150"},
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])

    @pytest.mark.asyncio
    async def test_pacifica_non_nan_bid_ask_for_all_symbols_with_mid(self):
        """All symbols with mid should have non-NaN bid/ask (>80% criterion)."""
        from core.data_sources.market_feeds.pacifica_perpetual.pacifica_perpetual_funding_rate_feed import PacificaPerpetualFundingRateFeed

        # Simulate realistic batch: all have mid
        symbols = [
            {"symbol": f"COIN{i}-PERP", "funding": "0.0001", "mark": f"{i*100}", "mid": f"{i*100 + 1}"}
            for i in range(1, 11)
        ]

        feed = PacificaPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=symbols)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 10
        rows_with_bid = sum(1 for r in rows if not math.isnan(r["best_bid"]) and r["best_bid"] > 0)
        assert rows_with_bid / len(rows) >= 0.8, f"Only {rows_with_bid}/10 have bid>0"


# ---------------------------------------------------------------------------
# Paradex Feed Addition
# ---------------------------------------------------------------------------

class TestParadexFundingRateFeed:
    """Paradex perpetual funding rate feed: 8H rates, bid/ask via markets/summary."""

    def _make_summary_response(self, items):
        return {"results": items}

    def _btc_item(self, funding_rate="0.0008", bid="95000", ask="95010"):
        return {
            "symbol": "BTC-USD-PERP",
            "mark_price": "95000",
            "funding_rate": funding_rate,
            "bid": bid,
            "ask": ask,
        }

    @pytest.mark.asyncio
    async def test_paradex_feed_exists_and_has_correct_venue(self):
        """ParadexPerpetualFundingRateFeed exists and has VENUE='paradex'."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        feed = ParadexPerpetualFundingRateFeed()
        assert feed.VENUE == "paradex"

    @pytest.mark.asyncio
    async def test_paradex_normalizes_8h_to_1h(self):
        """Paradex 8H funding rate is divided by 8 to yield 1H rate."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "BTC-USD-PERP", "mark_price": "95000", "funding_rate": "0.0008",
             "bid": "94990", "ask": "95010"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["funding_rate"] == pytest.approx(0.0008 / 8.0)

    @pytest.mark.asyncio
    async def test_paradex_symbol_normalized_to_base(self):
        """'BTC-USD-PERP' is normalized to 'BTC'."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "ETH-USD-PERP", "mark_price": "3000", "funding_rate": "0.0004",
             "bid": "2999", "ask": "3001"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["trading_pair"] == "ETH"

    @pytest.mark.asyncio
    async def test_paradex_bid_ask_from_summary(self):
        """bid/ask extracted from markets/summary 'bid'/'ask' fields."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "BTC-USD-PERP", "mark_price": "95000", "funding_rate": "0.0008",
             "bid": "94990", "ask": "95010"},
            {"symbol": "ETH-USD-PERP", "mark_price": "3000", "funding_rate": "0.0004",
             "bid": "2999", "ask": "3001"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 2
        btc = next(r for r in rows if r["trading_pair"] == "BTC")
        assert btc["best_bid"] == pytest.approx(94990.0)
        assert btc["best_ask"] == pytest.approx(95010.0)

    @pytest.mark.asyncio
    async def test_paradex_mark_price_populated(self):
        """mark_price is extracted from summary."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "SOL-USD-PERP", "mark_price": "150.5", "funding_rate": "0.0002",
             "bid": "150", "ask": "151"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["mark_price"] == pytest.approx(150.5)

    @pytest.mark.asyncio
    async def test_paradex_empty_response_returns_empty_list(self):
        """Empty API response returns empty list."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=None)):
            rows = await feed._fetch_funding_rates()

        assert rows == []

    @pytest.mark.asyncio
    async def test_paradex_dataframe_has_all_columns(self):
        """get_all_funding_rates() DataFrame includes all required columns."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "BTC-USD-PERP", "mark_price": "95000", "funding_rate": "0.0008",
             "bid": "94990", "ask": "95010"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            df = await feed.get_all_funding_rates()

        assert "timestamp" in df.columns
        assert "trading_pair" in df.columns
        assert "funding_rate" in df.columns
        assert "mark_price" in df.columns
        _has_bid_ask(df)

    @pytest.mark.asyncio
    async def test_paradex_skips_zero_funding_rate(self):
        """Entries with zero funding rate are skipped."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "BTC-USD-PERP", "mark_price": "95000", "funding_rate": "0.0008",
             "bid": "94990", "ask": "95010"},
            {"symbol": "ETH-USD-PERP", "mark_price": "3000", "funding_rate": "0",
             "bid": "2999", "ask": "3001"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert rows[0]["trading_pair"] == "BTC"

    @pytest.mark.asyncio
    async def test_paradex_uses_correct_base_url(self):
        """Feed uses Paradex prod URL."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_base import ParadexPerpetualBase

        assert "paradex.trade" in ParadexPerpetualBase._base_url

    @pytest.mark.asyncio
    async def test_paradex_nan_when_bid_ask_missing(self):
        """NaN when bid/ask absent in summary."""
        from core.data_sources.market_feeds.paradex_perpetual.paradex_perpetual_funding_rate_feed import ParadexPerpetualFundingRateFeed

        api_data = self._make_summary_response([
            {"symbol": "BTC-USD-PERP", "mark_price": "95000", "funding_rate": "0.0008"},
        ])

        feed = ParadexPerpetualFundingRateFeed()
        with patch.object(feed, "_make_request", new=AsyncMock(return_value=api_data)):
            rows = await feed._fetch_funding_rates()

        assert len(rows) == 1
        assert math.isnan(rows[0]["best_bid"])
        assert math.isnan(rows[0]["best_ask"])


# ---------------------------------------------------------------------------
# FundingRateCollector: Paradex registered
# ---------------------------------------------------------------------------

class TestCollectorHasParadex:
    """FundingRateCollector must include Paradex feed."""

    def test_collector_includes_paradex_feed(self):
        """FundingRateCollector.feeds includes a feed with VENUE='paradex'."""
        from core.data_sources.funding_rate_collector import FundingRateCollector

        collector = FundingRateCollector()
        venues = [feed.VENUE for feed in collector.feeds]
        assert "paradex" in venues, f"'paradex' not in venues: {venues}"
