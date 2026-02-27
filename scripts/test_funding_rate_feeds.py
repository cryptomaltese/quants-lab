#!/usr/bin/env python3
"""Quick smoke-test for the multi-venue funding rate collector."""

import asyncio
import logging
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.data_sources.funding_rate_collector import FundingRateCollector


async def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    symbols = ["BTC", "ETH", "SOL"]
    print(f"\nCollecting funding rates for {symbols} across all venues ...\n")

    collector = FundingRateCollector()
    try:
        df = await collector.collect(symbols)
    finally:
        await collector.close()

    if df.empty:
        print("No data returned.")
        return

    # Build a pivot table: symbol x venue showing funding_rate_1h
    pivot = df.pivot_table(
        index="trading_pair",
        columns="venue",
        values="funding_rate_1h",
        aggfunc="first",
    )

    print("=" * 72)
    print("  Funding Rates (per hour) — symbol x venue")
    print("=" * 72)

    # Header
    venues = list(pivot.columns)
    header = f"{'Symbol':<10}" + "".join(f"{v:>16}" for v in venues)
    print(header)
    print("-" * len(header))

    for sym in pivot.index:
        row = f"{sym:<10}"
        for v in venues:
            val = pivot.loc[sym, v]
            if val != val:  # NaN
                row += f"{'—':>16}"
            else:
                row += f"{val:>16.8f}"
        print(row)

    print()

    # Also show mark prices
    mark_pivot = df.pivot_table(
        index="trading_pair",
        columns="venue",
        values="mark_price",
        aggfunc="first",
    )
    print("  Mark Prices — symbol x venue")
    print("-" * 72)
    header = f"{'Symbol':<10}" + "".join(f"{v:>16}" for v in mark_pivot.columns)
    print(header)
    print("-" * len(header))
    for sym in mark_pivot.index:
        row = f"{sym:<10}"
        for v in mark_pivot.columns:
            val = mark_pivot.loc[sym, v]
            if val != val:
                row += f"{'—':>16}"
            else:
                row += f"{val:>16.2f}"
        print(row)

    print()


if __name__ == "__main__":
    asyncio.run(main())
