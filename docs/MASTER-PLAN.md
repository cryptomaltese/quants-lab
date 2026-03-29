# hummingbot-lab Master Plan

> Backtesting + paper trading facility for OpenClaw strategies.
> Replaces quants-lab fork. Zero external infra. SQLite-only.

**Date:** 2026-03-29
**Status:** Draft — awaiting review

---

## Executive Summary

hummingbot-lab is a from-scratch rebuild that:
1. **Collects** live funding snapshots via dex-factory adapters → SQLite
2. **Replays** historical ticks through the same pure functions that hummingbot-trade uses live
3. **Paper-trades** by replaying live ticks without executing orders
4. **Dashboards** every strategy run — same HTML templates, different data source

Everything quants-lab did with MongoDB, Conda, Optuna, and 7k lines of framework
code gets replaced by ~1.5k lines of focused Python that calls the code we already own.

### What we kill from quants-lab

| Keep | Kill |
|------|------|
| Nothing — clean break | `core/` (backtesting engine, task orchestrator, MongoDB client, CLOB data source) |
| | `app/` (12+ directional strategies, market making, screeners) |
| | `research_notebooks/` (17 MB of Jupyter) |
| | `config/` (YAML task templates) |
| | Conda environment, Makefile, Docker multi-stage |
| | Motor (async Mongo), Optuna, Papermill, FastAPI task API |
| | All 40+ Hummingbot connector integrations |

quants-lab was a general-purpose quant research platform. We need a focused
backtesting tool for *our* strategies that use *our* adapters. Clean break.

---

## Architecture

```
dex-factory/                        (unchanged — single source of truth)
    core/
    ├── base.py                     DexClient ABC
    ├── models.py                   FundingData, MarginStatus, ...
    ├── hyperliquid/client.py       HyperliquidClient
    ├── extended/client.py          ExtendedClient
    └── ...

hummingbot-trade/                   (unchanged — live execution)
    controllers/dnpm_v2/
    ├── scanner.py                  uses dex-factory adapters
    ├── scoring.py                  pure math (φ-decay, TTBE, sizing)
    ├── hold_evaluator.py           pure math (dual-decay hold model)
    ├── entry_gate.py               two-gate filter
    ├── venue.py                    VenueConfig
    ├── status_snapshot.py          StatusSnapshot schema
    ├── status_renderer.py          Jinja2 → HTML
    └── ...

hummingbot-lab/                     (NEW — this repo)
    ├── collector/
    │   ├── tick_store.py           SQLite read/write for tick snapshots
    │   └── collect.py              async script: call adapters → store
    ├── replay/
    │   └── engine.py               read ticks → feed strategy → record
    ├── strategies/
    │   └── dnpm_v2.py              tick-level adapter for dnpm_v2 pure fns
    ├── paper/
    │   └── runner.py               live ticks → strategy → no execution
    ├── dashboard/
    │   └── report.py               generate HTML report from run results
    ├── scripts/
    │   ├── collect.sh              cron wrapper
    │   └── backtest.py             CLI entry point
    ├── db/
    │   └── schema.sql              reference DDL
    ├── tests/
    │   ├── test_tick_store.py
    │   ├── test_replay_engine.py
    │   └── test_dnpm_v2_adapter.py
    ├── Dockerfile                  same base as hummingbot-trade
    ├── pyproject.toml
    └── docs/
        └── MASTER-PLAN.md          (this file)
```

### Data flow

```
                    ┌──────────────────────────┐
                    │     dex-factory adapters  │
                    │  HyperliquidClient        │
                    │  ExtendedClient           │
                    └─────────┬────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ COLLECT  │   │  LIVE    │   │  PAPER   │
        │ (cron)   │   │ (trade)  │   │  TRADE   │
        └────┬─────┘   └──────────┘   └────┬─────┘
             │                              │
             ▼                              │
        ┌──────────┐                        │
        │  SQLite  │◄───────────────────────┘
        │ ticks.db │         (paper also writes decisions)
        └────┬─────┘
             │
             ▼
        ┌──────────┐
        │  REPLAY  │
        │  ENGINE  │
        └────┬─────┘
             │
             ▼
        ┌──────────┐    ┌──────────┐
        │ Strategy │───▶│ Decision │
        │ Adapter  │    │ Log      │
        └──────────┘    └────┬─────┘
                             │
                             ▼
                        ┌──────────┐
                        │ REPORT   │
                        │ (HTML)   │
                        └──────────┘
```

---

## SQLite Schema

### Design principles

- One file per venue-pair: `ticks_hl_ext.db` (or one file for everything — see tradeoff below)
- Append-only tick table — no updates, no deletes
- Decision log separate from ticks (replay output ≠ raw data)
- Timestamps as ISO-8601 TEXT (SQLite has no native datetime; TEXT sorts correctly)
- Rates stored as REAL (float64, same as Python)

### Tradeoff: one DB vs many

One file (`lab.db`) is simpler for queries that span venues. Multiple files
would let us shard by venue pair but adds complexity for cross-venue strategies
that need both sides. **Recommendation: single file.** SQLite handles tens of
millions of rows fine, and our tick rate is ~1 row per symbol per hour.

### Tables

```sql
-- Raw tick snapshots from collector
CREATE TABLE ticks (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,  -- ISO-8601 UTC
    venue       TEXT    NOT NULL,  -- "hyperliquid", "extended"
    symbol      TEXT    NOT NULL,  -- canonical: "BTC", "ETH"
    rate_1h     REAL    NOT NULL,  -- funding_rate_1h from FundingData
    best_bid    REAL,              -- nullable (not all venues provide)
    best_ask    REAL,              -- nullable
    settlement_period_hours REAL NOT NULL DEFAULT 1.0,
    is_hip3     INTEGER NOT NULL DEFAULT 0,
    dex_name    TEXT    NOT NULL DEFAULT '',
    -- collector metadata
    batch_id    TEXT    NOT NULL   -- groups rows from same collection run
);

CREATE INDEX idx_ticks_symbol_ts ON ticks(symbol, ts);
CREATE INDEX idx_ticks_venue_ts  ON ticks(venue, ts);
CREATE INDEX idx_ticks_ts        ON ticks(ts);

-- Replay/paper-trade decision log
CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL,  -- identifies the backtest/paper run
    ts          TEXT    NOT NULL,  -- tick timestamp this decision is for
    strategy    TEXT    NOT NULL,  -- "dnpm_v2"
    symbol      TEXT    NOT NULL,
    action      TEXT    NOT NULL,  -- "enter", "hold", "exit", "skip"
    direction   TEXT,              -- "long_a_short_b" or null
    venue_a     TEXT,
    venue_b     TEXT,
    score_bps   REAL,
    reason      TEXT,              -- human-readable
    meta        TEXT               -- JSON blob for strategy-specific data
);

CREATE INDEX idx_decisions_run ON decisions(run_id, ts);

-- Run metadata
CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,
    strategy    TEXT    NOT NULL,
    mode        TEXT    NOT NULL,  -- "backtest" or "paper"
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    config      TEXT    NOT NULL,  -- JSON: full config snapshot
    summary     TEXT               -- JSON: final PnL, stats, etc.
);
```

### Why not store predicted rates?

FundingData doesn't carry predicted rates. PredictedFundingData is a separate
model and only Hyperliquid supports it. If we need it later, add a
`predicted_rate_1h` nullable column to `ticks`. Don't pre-build for it now.

---

## Tick Interface

### What the collector stores

One row per `FundingData` returned by `client.get_funding_rates()`:

```python
@dataclass(frozen=True)
class FundingData:          # from dex-factory/core/models.py
    venue: str              # "hyperliquid", "extended"
    symbol: str             # "BTC", "ETH", "xyz:TSLA"
    funding_rate_1h: float  # already per-hour
    is_hip3: bool = False
    dex_name: str = ""
    best_bid: float | None = None
    best_ask: float | None = None
    settlement_period_hours: float = 1.0
```

This maps 1:1 to the `ticks` table. The collector calls
`client.get_funding_rates()` (same as live scanner) and inserts every
FundingData as a row.

### What the replay engine feeds to strategy adapters

```python
@dataclass
class Tick:
    """One point in time, all venues, all symbols."""
    ts: datetime
    funding: dict[str, list[FundingData]]  # venue → [FundingData, ...]
```

The replay engine groups ticks by timestamp and reconstructs the same view
that `Scanner.scan()` sees in live mode. The strategy adapter receives a
`Tick` and returns a list of `Decision` objects.

### Is FundingData enough?

**For DNPM v2: yes.** The scanner consumes `get_funding_rates()` which returns
`list[FundingData]`. The scoring functions consume `rate_1h` values and
`best_bid`/`best_ask` for crossing cost. That's all in FundingData.

**What's NOT in FundingData that live uses:**
- `get_balance()` → needed for position sizing. Paper mode simulates this.
- `get_positions()` → live-only. Replay tracks positions in-memory.
- `get_margin_status()` → live-only. Paper mode can approximate.
- `get_predicted_funding_rates()` → HL-only, not stored. Scoring uses
  `best_estimate_rate()` which falls back to current rate if no prediction.

**Verdict:** FundingData is the right tick granularity. Position state and
balance are simulation concerns, not data concerns.

---

## Replay Engine Design

### Core loop

```python
class ReplayEngine:
    def __init__(self, db_path: str, strategy: StrategyAdapter):
        self.store = TickStore(db_path)
        self.strategy = strategy

    def run(self, start: datetime, end: datetime, config: dict) -> RunResult:
        run_id = uuid4().hex[:12]
        self.strategy.initialize(config)

        for tick in self.store.iter_ticks(start, end):
            decisions = self.strategy.on_tick(tick)
            for d in decisions:
                self.store.log_decision(run_id, d)

        summary = self.strategy.finalize()
        self.store.save_run(run_id, config, summary)
        return RunResult(run_id=run_id, summary=summary)
```

### Key properties

1. **Deterministic.** Same ticks + same config = same output. No network calls.
2. **No lookahead.** `iter_ticks()` yields in chronological order. Strategy
   only sees current and past ticks.
3. **Strategy owns state.** The engine doesn't know about positions, PnL, or
   sizing. The strategy adapter tracks all of that internally.
4. **Decision log is the output.** Every action (enter, hold, exit, skip) is
   logged with its reasoning. The report is generated from this log.

### iter_ticks()

```python
def iter_ticks(self, start: datetime, end: datetime) -> Iterator[Tick]:
    """Yield one Tick per distinct timestamp in range."""
    rows = self.db.execute(
        "SELECT * FROM ticks WHERE ts >= ? AND ts <= ? ORDER BY ts",
        (start.isoformat(), end.isoformat())
    )
    for ts, group in itertools.groupby(rows, key=lambda r: r['ts']):
        funding = defaultdict(list)
        for row in group:
            funding[row['venue']].append(row_to_funding_data(row))
        yield Tick(ts=datetime.fromisoformat(ts), funding=dict(funding))
```

### What about fill simulation?

Live uses `EntryManager.enter()` → `monitor_fill()` with timeout logic. In
replay, we assume **instant fill at mid-price** (or at best_bid/best_ask if
available). This is a simplification, but:

- DNPM v2 uses limit-at-mid with 4-minute timeout
- Funding rate arb has wide spreads, so fill probability is high
- Slippage modeling adds complexity without proportional insight

If we need fill simulation later, add it as a pluggable `FillModel` to the
strategy adapter. Don't build it now.

---

## Collector Design

### What it does

1. Create aiohttp session
2. Create dex-factory clients (HyperliquidClient, ExtendedClient)
3. Call `client.get_funding_rates()` on each — **same method live uses**
4. Insert all FundingData rows into SQLite with shared batch_id
5. Close session

### collect.py

```python
async def collect(db_path: str, venues: list[str]) -> int:
    """Collect one snapshot from all venues. Returns row count."""
    batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    store = TickStore(db_path)

    async with aiohttp.ClientSession() as session:
        clients = build_clients(session, venues)
        for venue_name, client in clients.items():
            rates = await client.get_funding_rates()
            store.insert_ticks(batch_id, rates)

    return store.batch_count(batch_id)
```

### collect.sh (cron wrapper)

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m collector.collect --db data/lab.db --venues hyperliquid,extended
```

Cron: `2 * * * *` (at HH:02, matching live strategy tick timing).

### No credentials needed

The collector only calls `get_funding_rates()` — a **public, unauthenticated**
endpoint on both Hyperliquid and Extended. No API keys, no wallets, no secrets.
This is a huge simplification vs. live.

### HIP-3 support

HyperliquidClient has `get_all_funding_rates(include_hip3=True)` which fetches
main perps + all HIP-3 DEXes concurrently. The collector should use this
instead of plain `get_funding_rates()` to capture the full universe. The
`is_hip3` and `dex_name` fields in the tick table handle this.

---

## Strategy Adapter Pattern

### Interface

```python
class StrategyAdapter(Protocol):
    def initialize(self, config: dict) -> None: ...
    def on_tick(self, tick: Tick) -> list[Decision]: ...
    def finalize(self) -> dict: ...  # summary stats
```

### DNPM v2 adapter

The adapter wraps the pure functions from hummingbot-trade without importing
the controller or any Hummingbot-specific code:

```python
from controllers.dnpm_v2.scoring import score_pair, compute_ttbe, fee_roundtrip
from controllers.dnpm_v2.hold_evaluator import evaluate_hold
from controllers.dnpm_v2.entry_gate import passes_entry_gate
from controllers.dnpm_v2.venue import VenueConfig

class DnpmV2Adapter:
    """Replay adapter for DNPM v2 strategy."""

    def initialize(self, config: dict) -> None:
        self.venues = load_venue_configs(config)
        self.min_score_bps = config['min_score_bps']
        self.positions: list[SimPosition] = []
        self.balance = config.get('initial_balance_usd', 1000.0)
        self.history: list[dict] = []

    def on_tick(self, tick: Tick) -> list[Decision]:
        decisions = []

        # 1. Score all cross-venue pairs (same logic as Scanner.scan)
        opportunities = self._score_opportunities(tick)

        # 2. Evaluate existing positions for exit
        for pos in list(self.positions):
            hold = self._evaluate_hold(pos, tick)
            if hold.action == 'exit':
                decisions.append(self._close(pos, tick, hold.reason))

        # 3. Evaluate new entries
        for opp in opportunities:
            if passes_entry_gate(opp.score, self.min_score_bps, ...):
                decisions.append(self._enter(opp, tick))

        return decisions

    def finalize(self) -> dict:
        return {
            'total_pnl_bps': sum(p.realized_pnl for p in self.history),
            'num_trades': len(self.history),
            'win_rate': ...,
            ...
        }
```

### Why not import Scanner directly?

Scanner depends on live clients (HyperliquidClient, ExtendedClient) for
fetching rates. In replay, rates come from SQLite. The adapter calls the
same *math* functions (score_pair, fee_roundtrip, evaluate_hold) but
feeds them stored data instead of live data. Scanner's `scan()` method
does too much (fetch + score + filter) to be reusable without the clients.

**Alternative considered:** refactor Scanner into `scan_from_rates(rates)` and
`scan_from_live(clients)`. This is cleaner but requires changing
hummingbot-trade code. We should do this eventually (see issue #7 below) but
the adapter approach works without touching live code.

---

## Dashboard Integration

### Live dashboard (existing)

hummingbot-trade has:
- `StatusSnapshot` (Pydantic model) → data
- `StatusRenderer` (Jinja2) → HTML
- `StatusWriter` → YAML

The template (`status.html.j2`) renders opportunities and positions.

### Backtest report

A backtest report is fundamentally different from a live dashboard:
- Live shows current state; backtest shows a completed run
- Live has opportunities + positions; backtest has a decision timeline + PnL curve
- Live refreshes; backtest is static

**Recommendation:** Don't reuse the live template. Build a separate
`report.html.j2` that renders from the decision log:

```
┌─────────────────────────────────────────────┐
│  DNPM v2 Backtest — 2026-03-01 → 2026-03-28 │
├─────────────────────────────────────────────┤
│  Total PnL: +42.3 bps                       │
│  Trades: 18 (14W / 4L)                      │
│  Avg hold: 6.2 hours                        │
│  Max drawdown: -8.1 bps                     │
├─────────────────────────────────────────────┤
│  [Decision timeline table]                   │
│  ts | symbol | action | score | pnl | reason│
│  ...                                         │
├─────────────────────────────────────────────┤
│  [PnL curve — inline SVG or ASCII]          │
└─────────────────────────────────────────────┘
```

Self-contained HTML, no JS frameworks, same pattern as live (Jinja2 + atomic write).

### Shared code

The *data models* from hummingbot-trade (VenueConfig, scoring functions) are
shared. The *presentation* is not. This is fine — strategies are different,
dashboards are different. That's principle #4.

---

## Paper Trading Mode

Paper trading = replay engine running on **live ticks instead of stored ticks**.

```python
class PaperRunner:
    def __init__(self, strategy: StrategyAdapter, db_path: str, venues: list[str]):
        self.strategy = strategy
        self.store = TickStore(db_path)
        self.venues = venues

    async def run(self, config: dict):
        run_id = uuid4().hex[:12]
        self.strategy.initialize(config)

        while True:
            # 1. Collect live tick (same as collector)
            tick = await self._fetch_live_tick()

            # 2. Store it (builds history for later replay)
            self.store.insert_ticks(tick.batch_id, tick.funding_flat())

            # 3. Feed to strategy
            decisions = self.strategy.on_tick(tick)
            for d in decisions:
                self.store.log_decision(run_id, d)

            # 4. Wait for next settlement
            await asyncio.sleep(self._seconds_until_next_tick())
```

### Key insight

Paper trading is *not* a third mode. It's:
- **Collector** (store live ticks) +
- **Replay** (feed to strategy) +
- **No execution**

running in a loop. The only difference from offline replay is the tick source
(live API vs. SQLite). The strategy adapter is identical.

### Simulated balance

Paper mode needs a simulated balance tracker:

```python
class SimBalance:
    def __init__(self, initial_usd: float):
        self.balance = initial_usd
        self.reserved = 0.0  # in open positions

    def allocate(self, usd: float) -> bool: ...
    def release(self, usd: float, pnl: float) -> None: ...
```

This lives in the strategy adapter, not the engine.

---

## Multi-Strategy Support

### How strategies coexist

Each strategy is:
1. A `StrategyAdapter` implementation (e.g., `DnpmV2Adapter`)
2. Its own config (parameters, venue list, sizing)
3. Its own decision log (filtered by `strategy` column)
4. Its own report template

The replay engine doesn't know about strategy internals. It just calls
`on_tick()` and logs what comes back. Running multiple strategies on the same
data is just:

```python
for strategy_name, config in strategies.items():
    adapter = registry[strategy_name]()
    engine = ReplayEngine(db_path, adapter)
    engine.run(start, end, config)
```

### Strategy registry

Simple dict, no framework:

```python
STRATEGIES = {
    "dnpm_v2": DnpmV2Adapter,
}
```

Add new strategies by adding a file in `strategies/` and registering it.

### Data sharing

All strategies read from the same `ticks` table. The collector doesn't know
about strategies — it collects everything. Strategies filter what they need
via their adapter.

---

## Migration Plan (MongoDB → SQLite)

### What's in MongoDB

quants-lab stores:
- `task_executions`: orchestrator metadata (irrelevant — kill)
- `funding_rates`: collected funding rate data (potentially useful)
- Various screener/optimization results (irrelevant)

### Do we actually need to migrate?

**Probably not.** The MongoDB funding_rates collection was populated by
quants-lab's own collector which used Hummingbot connectors, not dex-factory
adapters. The data format is different. The symbol naming is different.
Hummingbot uses `BTC-USD` trading pairs; dex-factory uses `BTC` symbols.

**Recommendation:** Start fresh. Run the new collector for a week to build
up history, then start backtesting. If we desperately need historical data
for a longer backtest window, we can:

1. Write a one-off migration script that reads Mongo → normalizes → inserts to SQLite
2. But honestly, most of the quants-lab data was candle data for different strategies

**If we do migrate:**

```python
# scripts/migrate_mongo.py
import pymongo, sqlite3

mongo = pymongo.MongoClient("mongodb://admin:admin@localhost:27017/quants_lab")
db = sqlite3.connect("data/lab.db")

for doc in mongo.quants_lab.funding_rates.find():
    db.execute(
        "INSERT INTO ticks (ts, venue, symbol, rate_1h, batch_id) VALUES (?, ?, ?, ?, ?)",
        (doc['timestamp'].isoformat(), doc['venue'], normalize_symbol(doc['symbol']),
         doc['rate'], 'mongo_migration')
    )
```

Estimate: ~2 hours of work if needed. Not a priority.

---

## Opinionated Challenges to the Approved Architecture

### 1. `collector/tick_store.py` should be `db/tick_store.py`

The tick store is used by collector, replay, and paper mode. It's not a
collector concern — it's the data layer. Move it to `db/`.

**Updated structure:**
```
hummingbot-lab/
├── db/
│   ├── tick_store.py      read/write ticks + decisions
│   └── schema.sql         reference DDL
├── collector/
│   └── collect.py         calls adapters → tick_store
├── replay/
│   └── engine.py          reads tick_store → strategy
```

### 2. `collect.sh` is unnecessary

A 3-line shell script that just calls Python is ceremony. Use cron directly:

```cron
2 * * * * cd /path/to/hummingbot-lab && python -m collector.collect
```

Or if you want logging/error handling, make `collect.py` handle it:

```python
if __name__ == "__main__":
    asyncio.run(collect(db_path="data/lab.db", venues=["hyperliquid", "extended"]))
```

### 3. "Same Docker image as hummingbot-trade" needs nuance

hummingbot-trade uses `ghcr.io/cryptomaltese/hummingbot-extended:latest` which
includes the full Hummingbot framework. hummingbot-lab doesn't need Hummingbot
at all — it only needs:
- dex-factory (for collector)
- scoring.py, hold_evaluator.py, entry_gate.py (pure math, no HB deps)
- aiohttp, sqlite3 (stdlib)

**Recommendation:** Same *base* Python version and key deps, but a much
lighter image. The pure math modules from hummingbot-trade have a mock
`__init__.py` that stubs out Hummingbot imports — they're already designed
to run standalone.

```dockerfile
FROM python:3.12-slim
COPY builds/dex-factory /app/dex-factory
COPY builds/hummingbot-trade/controllers/dnpm_v2 /app/dnpm_v2
COPY builds/hummingbot-lab /app/lab
WORKDIR /app/lab
RUN pip install aiohttp jinja2 pyyaml
```

### 4. Symbol universe handling

Live uses `SymbolUniverse` (24h-cached JSON) to match HL symbols to Extended
symbols. The collector needs the same matching. Two options:

- **Option A:** Collector stores raw venue-native symbols; replay does matching
- **Option B:** Collector normalizes to canonical symbols at collection time

**Recommendation: Option B.** Normalize at write time, store canonical symbols.
This means the collector needs SymbolUniverse (or a simpler static mapping),
but it makes queries trivial: `WHERE symbol = 'BTC'` instead of joining on
symbol mappings.

### 5. The `strategies/` directory will be tiny

For the foreseeable future, we have one strategy: DNPM v2. Building a plugin
architecture for one strategy is over-engineering. Start with `dnpm_v2.py`
in a flat module. If we add a second strategy, *then* refactor.

### 6. Consider adding price snapshots to ticks

FundingData has `best_bid` and `best_ask` but not `mark_price` or `index_price`.
The hold evaluator uses `live_crossing_exit` which is derived from bid/ask
spreads. For accurate replay of the hold model, we need bid/ask data. These
are already in FundingData, so we're good — but verify that all venues
actually populate them. Extended's `get_funding_rates()` returns them;
Hyperliquid's does too (from the info endpoint). **No action needed, just
verify during implementation.**

### 7. Eventually refactor Scanner for reuse

The cleanest long-term architecture is:

```python
# In hummingbot-trade/controllers/dnpm_v2/scanner.py
class Scanner:
    def scan_from_rates(self, rates: dict[str, list[FundingData]]) -> list[RankedOpportunity]:
        """Pure: rates in, scored opportunities out."""
        ...

    async def scan(self, clients: dict[str, DexClient]) -> list[RankedOpportunity]:
        """Live: fetch rates, then call scan_from_rates."""
        rates = {name: await client.get_funding_rates() for name, client in clients.items()}
        return self.scan_from_rates(rates)
```

This lets hummingbot-lab call `scan_from_rates()` directly with stored data,
eliminating the need for a separate adapter to re-implement scoring logic.

**Don't do this in v1.** The adapter pattern works. But file this as a
follow-up refactor.

---

## Issue Breakdown

### Phase 1: Foundation (no deps between issues)

#### Issue #1: SQLite tick store
**Title:** Implement tick_store.py — SQLite persistence layer
**Scope:** `db/tick_store.py`, `db/schema.sql`
**Work:**
- Create `TickStore` class with `__init__(db_path)` that creates DB + tables if not exist
- `insert_ticks(batch_id: str, rates: list[FundingData]) -> int`
- `iter_ticks(start: datetime, end: datetime) -> Iterator[Tick]`
- `log_decision(run_id: str, decision: Decision)`
- `save_run(run_id, strategy, mode, config, summary)`
- `get_run(run_id) -> RunMeta`
- `list_runs(strategy=None, mode=None) -> list[RunMeta]`
- Schema as defined above
- Use `sqlite3` stdlib, no ORM

**Acceptance criteria:**
- Unit tests for insert + read roundtrip
- `iter_ticks` groups by timestamp and yields `Tick` objects
- Thread-safe (WAL mode enabled)
- DB file created automatically on first use

---

#### Issue #2: Collector script
**Title:** Implement async collector using dex-factory adapters
**Scope:** `collector/collect.py`
**Work:**
- `async def collect(db_path, venues, include_hip3=True) -> int`
- Build clients from venue list (no credentials needed)
- Call `get_funding_rates()` (or `get_all_funding_rates(include_hip3=True)` for HL)
- Normalize symbols to canonical form using a static mapping (MVP) or SymbolUniverse
- Insert via `TickStore.insert_ticks()`
- CLI: `python -m collector.collect --db data/lab.db --venues hyperliquid,extended`
- Logging to stderr

**Acceptance criteria:**
- Runs successfully against live venues (integration test, not mocked)
- Inserts correct number of rows
- Handles venue errors gracefully (log + continue with other venues)
- Takes < 10s for a full collection run

**Depends on:** #1

---

#### Issue #3: Data models + Tick/Decision types
**Title:** Define shared data types for replay system
**Scope:** `models.py` (top-level)
**Work:**
- `Tick` dataclass (ts, funding dict)
- `Decision` dataclass (ts, strategy, symbol, action, direction, venues, score, reason, meta)
- `RunMeta` dataclass (run_id, strategy, mode, started_at, ended_at, config, summary)
- `SimPosition` dataclass (symbol, venue_a, venue_b, direction, entry_ts, entry_score, size_usd, cumulative_funding_bps)
- Keep it minimal — add fields as needed, not speculatively

**Acceptance criteria:**
- All types are plain dataclasses (no Pydantic, no framework)
- Documented with docstrings
- Importable without side effects

**Depends on:** nothing

---

### Phase 2: Replay Engine

#### Issue #4: Replay engine core
**Title:** Implement replay engine — feed stored ticks to strategy adapter
**Scope:** `replay/engine.py`
**Work:**
- `ReplayEngine.__init__(db_path, strategy: StrategyAdapter)`
- `run(start, end, config) -> RunResult`
- Iterates `TickStore.iter_ticks()`, calls `strategy.on_tick()`, logs decisions
- Writes run metadata on completion
- Returns RunResult with run_id and summary dict

**Acceptance criteria:**
- Deterministic: same input → same output
- No network calls during replay
- Handles empty date ranges gracefully
- Logs progress (tick count, decision count)

**Depends on:** #1, #3

---

#### Issue #5: DNPM v2 strategy adapter
**Title:** Implement DNPM v2 replay adapter wrapping pure scoring/hold functions
**Scope:** `strategies/dnpm_v2.py`
**Work:**
- Import `score_pair`, `fee_roundtrip`, `compute_ttbe` from hummingbot-trade scoring
- Import `evaluate_hold` from hold_evaluator (standalone function, not class)
- Import `passes_entry_gate` (or inline the two-gate logic — it's simple)
- Import `VenueConfig` from venue module
- Implement `StrategyAdapter` protocol:
  - `initialize()`: load venue configs, set params, init SimBalance
  - `on_tick()`: score opportunities → evaluate holds → make entry/exit decisions
  - `finalize()`: compute summary stats (PnL, win rate, avg hold, etc.)
- Track simulated positions + balance internally
- Cross-venue pair matching (HL symbol ↔ Extended symbol)

**Acceptance criteria:**
- Uses real scoring.py functions (not reimplemented)
- Entry gate logic matches live behavior
- Hold evaluation matches live behavior
- Position sizing uses `compute_position_size()` from scoring.py
- Handles case where only one venue has data for a symbol
- Summary stats include: total_pnl_bps, num_entries, num_exits, win_rate, avg_hold_hours, max_drawdown_bps

**Depends on:** #3, #4

---

#### Issue #6: Backtest CLI
**Title:** CLI entry point for running backtests
**Scope:** `scripts/backtest.py`
**Work:**
- `python -m scripts.backtest --db data/lab.db --strategy dnpm_v2 --start 2026-03-01 --end 2026-03-28 --config config.yml`
- Load config YAML → merge with defaults
- Instantiate strategy adapter + replay engine
- Run + print summary to stdout
- Optionally write HTML report (--report flag)

**Acceptance criteria:**
- Exits 0 on success, 1 on failure
- Prints human-readable summary
- Config file is optional (uses defaults)
- Validates date range against available data

**Depends on:** #4, #5

---

### Phase 3: Reports + Paper Trading

#### Issue #7: Backtest report generator
**Title:** HTML report from backtest decision log
**Scope:** `dashboard/report.py`, `dashboard/templates/report.html.j2`
**Work:**
- Read decisions + run metadata from SQLite
- Compute: PnL curve, trade timeline, per-symbol breakdown
- Render Jinja2 template → self-contained HTML
- Inline CSS, no external resources
- ASCII or inline SVG PnL chart (no JS charting library)

**Acceptance criteria:**
- Single .html file, opens in any browser
- Shows: summary stats, decision timeline, per-symbol PnL
- Matches visual style of live dashboard (roughly)

**Depends on:** #4

---

#### Issue #8: Paper trading runner
**Title:** Live paper trading mode — collect + replay in a loop
**Scope:** `paper/runner.py`
**Work:**
- `PaperRunner(strategy, db_path, venues)`
- Async loop: fetch live tick → store → feed strategy → log decisions
- Tick timing: sync to HH:02 UTC (same as live)
- Graceful shutdown on SIGINT/SIGTERM
- Write run metadata on stop

**Acceptance criteria:**
- Runs indefinitely until stopped
- Stores ticks (doubles as collector)
- Decisions logged to same schema as backtest
- Can generate report from paper run using same report tool (#7)

**Depends on:** #2, #5

---

### Phase 4: Polish + Integration

#### Issue #9: Dockerfile
**Title:** Lightweight Docker image for hummingbot-lab
**Scope:** `Dockerfile`, `pyproject.toml`
**Work:**
- Based on `python:3.12-slim`
- Copy dex-factory core + hummingbot-trade pure math modules
- Install minimal deps (aiohttp, jinja2, pyyaml)
- Entry point: collector or backtest (configurable)
- Keep image < 200MB

**Acceptance criteria:**
- `docker build` succeeds
- `docker run ... collect` runs collector
- `docker run ... backtest --help` shows usage
- No Hummingbot framework in image

**Depends on:** #2, #6

---

#### Issue #10: Scanner refactor for data/logic split
**Title:** Refactor Scanner.scan() into scan_from_rates() + scan_live()
**Scope:** `hummingbot-trade/controllers/dnpm_v2/scanner.py` (upstream change)
**Work:**
- Extract rate-fetching from `scan()` into separate step
- New `scan_from_rates(rates: dict[str, list[FundingData]]) -> list[RankedOpportunity]`
- `scan()` becomes: fetch rates → `scan_from_rates()`
- hummingbot-lab adapter can then call `scan_from_rates()` directly
- Remove duplicate scoring logic from DnpmV2Adapter

**Acceptance criteria:**
- `scan()` behavior unchanged (backward compatible)
- `scan_from_rates()` is a pure function (no I/O)
- hummingbot-lab adapter simplified to use this directly
- All existing tests pass

**Depends on:** #5 (adapter works first, then we clean up)

---

### Implementation Order

```
Phase 1 (parallel):  #1 tick_store  ───┐
                      #3 data models ──┤
                      #2 collector  ────┤ (depends on #1)
                                        │
Phase 2 (sequential): #4 replay ───────┤ (depends on #1, #3)
                      #5 adapter ──────┤ (depends on #3, #4)
                      #6 CLI ──────────┤ (depends on #4, #5)
                                        │
Phase 3 (parallel):   #7 report ───────┤ (depends on #4)
                      #8 paper trade ──┤ (depends on #2, #5)
                                        │
Phase 4 (parallel):   #9 Dockerfile ───┤ (depends on #2, #6)
                      #10 scanner ─────┘ (depends on #5)
```

**Critical path:** #1 → #4 → #5 → #6

**Estimated total:** 10 focused issues. No issue is larger than a day of work.
Most are half-day or less.

---

## Open Questions

1. **Collection frequency:** Hourly matches live tick timing, but should we
   collect more frequently for higher-resolution replay? (e.g., every 5 min
   for paper mode simulation.) The schema supports it, but storage grows 12x.

2. **Multi-account backtesting:** hummingbot-trade supports multi-account via
   Docker. Should hummingbot-lab simulate multiple accounts? (Probably not —
   one simulated account is enough for strategy validation.)

3. **Historical data bootstrap:** How far back do we need? dex-factory
   adapters have `get_funding_history(symbol, hours=24)` which gives ~24h of
   history per call. For longer history, we'd need an archive source or start
   collecting now and wait.

4. **Paradex/Lighter support:** The collector and schema support any venue, but
   the DNPM v2 adapter is HL+Extended specific. When we add new venue pairs,
   the adapter needs updating. The collector just needs the venue name.
