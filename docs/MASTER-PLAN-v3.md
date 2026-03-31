# hummingbot-lab Master Plan v3

> Strategy as a portable object. Backtest is not a second-class citizen.

**Date:** 2026-04-01
**Status:** Draft — incorporates user feedback on v2 open questions
**Supersedes:** MASTER-PLAN-v2.md

---

## What Changed From v2

v2 introduced the portable strategy concept. v3 resolves the open questions with specific decisions:

1. **strategies/ is a separate repo, now.** Not deferred until "a second strategy." Created immediately as a monorepo with sub-tree per strategy. Both hummingbot-lab and hummingbot-trade import from it. This is basic glue code — the architecture + Hummingbot do the heavy lifting.

2. **Predicted funding rates in backtest.** The premise "only HL has predicted rates" was wrong. Hyperliquid and Pacifica both implement `get_predicted_funding_rates()`. Extended has `nextFundingRate` in its API but parses it as a timestamp — potential bug. Lighter: unknown. Principle: **a feature available in live but ignored in backtest is glaringly unacceptable.** Collector stores predicted rates. `best_estimate_rate()` works identically in backtest and live.

3. **1-minute price ticks.** The hold evaluator's dislocation model uses ψ per minute. With hourly data, that model is meaningless. And cross-dex dislocation contributes significantly to PnL — the dashboard has shown this empirically. Collector runs at 1-minute intervals for bid/ask. Funding rates stay hourly (they only change at settlement). Schema splits into `funding_ticks` (hourly) and `price_ticks` (per-minute).

4. **Multi-account in lab: not needed.** Confirmed.

### What's kept from v2

- Strategy Protocol (configure, on_tick, on_fill, state)
- Types (MarketTick, Decision, Fill, StrategyState, FundingAccrual)
- DnpmV2Strategy design (scan_pairs → hold evaluate → entry gate → size)
- HB Adapter pattern
- Dashboard rendering (strategy owns its template)
- Migration safety ("copy first, then redirect imports")

### What's reworked

- strategies/ repo structure and packaging
- SQLite schema (dual-table: funding_ticks + price_ticks)
- Collector design (dual-cadence: 1-min prices, hourly rates)
- TickStore (iter_ticks now joins price and funding data, includes predicted rates)
- Issue breakdown (strategies repo is issue #1, predicted rate audits are explicit)

---

## Architecture

```
strategies/                        SEPARATE REPO (pip-installable)
├── pyproject.toml
├── protocol.py                    Strategy Protocol definition
└── dnpm_v2/                       one sub-tree per strategy
    ├── __init__.py                exports DnpmV2Strategy
    ├── strategy.py                the black box: tick → decisions
    ├── scoring.py                 pure math (from trade)
    ├── hold_evaluator.py          pure math (from trade)
    ├── entry_gate.py              two-gate filter (from trade)
    ├── venue.py                   VenueConfig, RateSnapshot
    ├── scanner_core.py            _scan_pairs + greedy allocation (from Scanner)
    ├── types.py                   MarketTick, Decision, Fill, StrategyState
    ├── config.py                  strategy params (portable, no credentials)
    ├── config_schema.py           pydantic model for config validation
    ├── templates/
    │   └── dashboard.html.j2      strategy's own dashboard template
    └── tests/
        ├── test_scoring.py        existing tests (moved from trade)
        ├── test_hold.py
        ├── test_strategy.py       integration: tick → decisions
        └── fixtures/              stored ticks for deterministic replay

hummingbot-lab/                    backtest + paper trade
    ├── db/
    │   ├── tick_store.py          SQLite read/write (dual-table aware)
    │   └── schema.sql             DDL (funding_ticks + price_ticks)
    ├── collector/
    │   ├── collect_funding.py     hourly: rates + predicted rates → funding_ticks
    │   └── collect_prices.py      per-minute: bid/ask/mark → price_ticks
    ├── replay/
    │   └── engine.py              stored ticks → strategy → record
    ├── paper/
    │   └── runner.py              live ticks → strategy → no execution
    ├── dashboard/
    │   └── renderer.py            strategy.state() → strategy template → HTML
    ├── scripts/
    │   └── backtest.py            CLI entry point
    ├── tests/
    ├── pyproject.toml
    └── docs/
        └── MASTER-PLAN-v3.md      (this file)

hummingbot-trade/                  live execution (changes from current)
    controllers/dnpm_v2/
    ├── hb_adapter.py              Decision → HB ExecutorAction, Fill ← HB events
    ├── runner.py                  imports strategy, feeds live ticks
    ├── controller.py              SLIMMED: delegates to strategy via adapter
    ├── scanner.py                 SLIMMED: fetch only, scoring moves to strategy
    ├── config.py                  KEPT: HB-specific config (connectors, credentials)
    ├── status_snapshot.py         ADAPTED: reads from strategy.state()
    └── ...

dex-factory/                       unchanged — single source of truth for venue APIs
```

### Data flow

```
              ┌─────────────────────────────────────────┐
              │         dex-factory adapters             │
              │  get_funding_rates()                     │
              │  get_predicted_funding_rates()           │
              │  get_prices() / get_orderbook()          │
              └──────────┬──────────────────────────────┘
                         │
           ┌─────────────┼──────────────────────┐
           │             │                      │
           ▼             ▼                      ▼
     ┌────────────┐  ┌──────────┐         ┌──────────┐
     │ COLLECTOR  │  │  TRADE   │         │  PAPER   │
     │ funding:   │  │ (live)   │         │  TRADE   │
     │  hourly    │  └────┬─────┘         └────┬─────┘
     │ prices:    │       │                    │
     │  1-min     │  FundingData +        FundingData +
     └────┬───────┘  PredictedFundingData PredictedFundingData
          │           → MarketTick         → MarketTick
          │               │                    │
          ▼               ▼                    ▼
     ┌──────────┐  ┌──────────────────────────────────┐
     │  SQLite  │  │   strategies/dnpm_v2/            │
     │ funding_ │  │   strategy.on_tick()             │
     │  ticks   │  │         │                        │
     │ price_   │  │    list[Decision]                │
     │  ticks   │  │         │                        │
     └────┬─────┘  └─────────┼────────────────────────┘
          │                  │
     MarketTick         ┌────┴──────┐
     (joined)           │  RUNNER   │
          │             │ (env-     │
          ▼             │ specific) │
     ┌──────────┐       └────┬──────┘
     │  REPLAY  │            │
     │  ENGINE  │       Fill (real or simulated)
     │   ↓      │            │
     │ strategy │       strategy.on_fill()
     │ .on_tick │
     │   ↓      │
     │ Decision │
     │   log    │
     └──────────┘
```

---

## The Strategy Protocol

Unchanged from v2. Four methods: `configure()`, `on_tick()`, `on_fill()`, `state()`.

```python
class Strategy(Protocol):
    def configure(self, config: dict) -> None: ...
    def on_tick(self, tick: MarketTick) -> list[Decision]: ...
    def on_fill(self, fill: Fill) -> None: ...
    def state(self) -> StrategyState: ...
```

See v2 §Strategy Protocol for rationale on why these four and not more.

---

## Types

### MarketTick

```python
@dataclass(frozen=True)
class MarketTick:
    """One point in time: all venues, all symbols, plus context."""

    ts: datetime
    rates: dict[str, list[RateSnapshot]]
    # symbol → [RateSnapshot per venue]
    # RateSnapshot carries: venue_id, current_rate, predicted_rate,
    #                       best_bid, best_ask, timestamp

    available_balance_usd: float
    funding_accruals: list[FundingAccrual] = field(default_factory=list)
```

**Key change from v2:** `RateSnapshot.predicted_rate` is now populated in backtest (from stored `funding_ticks.predicted_rate_1h`), not always `None`. `best_bid` / `best_ask` come from `price_ticks` at the nearest minute.

### RateSnapshot (in strategies/dnpm_v2/venue.py)

Unchanged from trade. Already carries `predicted_rate: float | None`.

```python
@dataclass
class RateSnapshot:
    venue_id: str
    symbol: str
    current_rate: float             # per-period, in bps
    predicted_rate: float | None = None
    mark_price: float = 0.0
    timestamp: float = 0.0         # unix seconds
    best_bid: float = 0.0
    best_ask: float = 0.0
```

### Decision, Fill, StrategyState, FundingAccrual

Unchanged from v2. See v2 §Types for full definitions.

---

## DNPM v2 as a Strategy

Unchanged from v2 in structure. The key behavioral change: `best_estimate_rate()` now has predicted rates available in backtest, so it behaves identically to live:

```python
# In scoring.py (already implemented in trade):
def best_estimate_rate(snap: RateSnapshot, venue: VenueConfig) -> float:
    if venue.has_predicted_rate and snap.predicted_rate is not None:
        return cap_rate(snap.predicted_rate)
    return cap_rate(snap.current_rate)
```

In v2 backtest, `snap.predicted_rate` was always `None` → always fell back to `current_rate`. In v3, predicted rates are stored and replayed. The strategy code doesn't change — the data pipeline changes.

### What moves to strategies/dnpm_v2/

Same table as v2 §DNPM v2 as a Strategy. No changes.

### DnpmV2Strategy

Same as v2 §DnpmV2Strategy. No changes to the strategy code itself.

### What stays in hummingbot-trade

Same as v2 §What stays in hummingbot-trade.

---

## The HB Adapter (hummingbot-trade)

Unchanged from v2. See v2 §The HB Adapter for full adapter sketch and translation table.

---

## hummingbot-lab Design

### SQLite Schema

Split into two tables to support dual-cadence collection.

```sql
-- Hourly funding rate snapshots (one row per venue/symbol per hour)
CREATE TABLE funding_ticks (
    id                      INTEGER PRIMARY KEY,
    ts                      TEXT    NOT NULL,  -- ISO 8601, hourly granularity
    venue                   TEXT    NOT NULL,
    symbol                  TEXT    NOT NULL,
    rate_1h                 REAL    NOT NULL,  -- current funding rate (decimal, per-hour)
    predicted_rate_1h       REAL,              -- predicted next funding rate (decimal, per-hour)
    time_of_next_funding    INTEGER,           -- unix ms of next settlement
    settlement_period_hours REAL    NOT NULL DEFAULT 1.0,
    is_hip3                 INTEGER NOT NULL DEFAULT 0,
    dex_name                TEXT    NOT NULL DEFAULT '',
    batch_id                TEXT    NOT NULL
);

CREATE INDEX idx_funding_ts_venue_sym ON funding_ticks(ts, venue, symbol);

-- Per-minute price snapshots (one row per venue/symbol per minute)
CREATE TABLE price_ticks (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,  -- ISO 8601, minute granularity
    venue       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    best_bid    REAL,
    best_ask    REAL,
    mark_price  REAL,
    batch_id    TEXT    NOT NULL
);

CREATE INDEX idx_price_ts_venue_sym ON price_ticks(ts, venue, symbol);

-- Decision log (unchanged from v2)
CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    strategy    TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    action      TEXT    NOT NULL,     -- "enter", "exit", "hold"
    direction   TEXT,
    venue_a     TEXT,
    venue_b     TEXT,
    score_bps   REAL,
    size_usd    REAL,
    reason      TEXT,
    meta        TEXT,                 -- JSON
    position_id TEXT
);

-- Run metadata (unchanged from v2)
CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,
    strategy    TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    config      TEXT    NOT NULL,     -- JSON
    summary     TEXT                  -- JSON
);
```

**Storage math:**
- price_ticks: 4 venues × 100 symbols × 1440 min/day × ~80 bytes/row ≈ 46 MB/day ≈ 17 GB/year
- funding_ticks: 4 venues × 100 symbols × 24 hours/day × ~80 bytes/row ≈ 0.77 MB/day ≈ 280 MB/year
- SQLite with WAL mode handles this write volume comfortably. If write throughput becomes an issue (unlikely), first optimization: batch inserts per venue. Fallback: Redis as write buffer with periodic flush to SQLite.

**Why two tables instead of one wide table:**
- price_ticks has 1440× more rows per day than funding_ticks. Mixing them would make funding queries scan through millions of price rows.
- Different retention policies: price_ticks can be compacted to 5-min or 15-min after 90 days. Funding_ticks are small enough to keep forever.
- Separate indexes are more efficient for the two query patterns: "funding rates for a time range" vs "bid/ask at a specific minute."

### Collector (dual-cadence)

Two separate collector scripts, each with its own cron schedule.

```python
# collector/collect_funding.py — runs hourly
async def collect_funding(db_path: str, venues: list[str]) -> int:
    """Collect funding rates + predicted rates from all venues."""
    batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    store = TickStore(db_path)
    async with aiohttp.ClientSession() as session:
        clients = build_clients(session, venues)
        for venue_name, client in clients.items():
            rates = await client.get_funding_rates()
            predicted = await client.get_predicted_funding_rates()

            # Index predicted rates for O(1) lookup
            pred_by_sym = {p.symbol: p for p in predicted}

            store.insert_funding_ticks(batch_id, rates, pred_by_sym)
    return store.batch_count(batch_id)
```

```python
# collector/collect_prices.py — runs every minute
async def collect_prices(db_path: str, venues: list[str]) -> int:
    """Collect bid/ask/mark prices from all venues."""
    batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    store = TickStore(db_path)
    async with aiohttp.ClientSession() as session:
        clients = build_clients(session, venues)
        for venue_name, client in clients.items():
            rates = await client.get_funding_rates()
            # FundingData already carries best_bid, best_ask
            store.insert_price_ticks(batch_id, venue_name, rates)
    return store.batch_count(batch_id)
```

Cron:
```
2  *  *  *  *  python -m collector.collect_funding   # hourly at :02
*  *  *  *  *  python -m collector.collect_prices     # every minute
```

**Why `get_funding_rates()` for price collection?** FundingData already carries `best_bid`, `best_ask` from the venue's API response. We don't need a separate price endpoint. The funding rate fields are ignored (or could be used for sub-hourly rate tracking later).

**Alternative for price collection:** If `get_funding_rates()` is too heavy for per-minute calls (it fetches all symbols including rate computation), consider adding a lightweight `get_prices()` override that returns just bid/ask/mark. Hyperliquid's `get_prices()` already exists in the base class. Defer this optimization until we measure actual collection latency.

### TickStore

```python
class TickStore:
    def insert_funding_ticks(
        self, batch_id: str, rates: list[FundingData],
        predicted: dict[str, PredictedFundingData],
    ) -> int:
        """Insert hourly funding rates with predicted rates."""
        ...

    def insert_price_ticks(
        self, batch_id: str, venue: str, rates: list[FundingData],
    ) -> int:
        """Insert per-minute bid/ask/mark prices."""
        ...

    def iter_ticks(
        self, start: datetime, end: datetime,
        initial_balance: float = 1000.0,
        tick_interval_minutes: int = 60,
    ) -> Iterator[MarketTick]:
        """Yield MarketTicks by joining funding and price data.

        For each tick timestamp:
        1. Funding rates: latest funding_ticks at or before this timestamp
        2. Predicted rates: from the same funding_ticks row
        3. Bid/ask: from price_ticks at the nearest minute
        """
        ...

    def log_decision(self, run_id: str, decision: Decision) -> None: ...
    def save_run(self, run_id: str, config: dict, summary: dict) -> None: ...
```

**iter_ticks with predicted rates:**

```python
def iter_ticks(self, start, end, initial_balance=1000.0,
               tick_interval_minutes=60) -> Iterator[MarketTick]:
    # Query funding ticks (hourly) and price ticks (nearest minute)
    for ts in self._tick_timestamps(start, end, tick_interval_minutes):
        funding_rows = self._funding_at(ts)   # latest funding at or before ts
        price_rows = self._prices_at(ts)      # price_ticks at nearest minute

        rates = defaultdict(list)
        for row in funding_rows:
            # Find matching price data
            price = price_rows.get((row['venue'], row['symbol']))

            snap = RateSnapshot(
                venue_id=row['venue'],
                symbol=row['symbol'],
                current_rate=row['rate_1h'] * 10000 * row['settlement_period_hours'],
                predicted_rate=(
                    row['predicted_rate_1h'] * 10000 * row['settlement_period_hours']
                    if row['predicted_rate_1h'] is not None else None
                ),
                best_bid=price['best_bid'] if price else 0.0,
                best_ask=price['best_ask'] if price else 0.0,
                mark_price=price['mark_price'] if price else 0.0,
                timestamp=ts_to_unix(ts),
            )
            rates[row['symbol']].append(snap)

        yield MarketTick(
            ts=datetime.fromisoformat(ts),
            rates=dict(rates),
            available_balance_usd=initial_balance,
        )
```

**Key difference from v2:** `predicted_rate` is populated from `funding_ticks.predicted_rate_1h`, not hardcoded to `None`. The strategy's `best_estimate_rate()` now works identically in backtest and live.

**tick_interval_minutes:** Default 60 for hourly backtests (same as v2). Can be set to 1 for minute-level backtests that test dislocation behavior. At 1-minute granularity, funding rates repeat (they're hourly), but bid/ask changes every minute — which is exactly what the hold evaluator's dislocation model needs.

### Replay Engine

Same as v2 with one enhancement: funding accrual computation uses predicted rates when available.

```python
class ReplayEngine:
    def run(self, start, end, config: dict) -> RunResult:
        run_id = uuid4().hex[:12]
        self.strategy.configure(config)
        balance = config.get("initial_balance_usd", 1000.0)
        tick_interval = config.get("tick_interval_minutes", 60)

        for tick in self.store.iter_ticks(start, end, balance, tick_interval):
            tick = self._add_accruals(tick)
            decisions = self.strategy.on_tick(tick)

            for d in decisions:
                self.store.log_decision(run_id, d)
                if d.action == "enter":
                    fill = self._simulate_fill(d, tick)
                    self.strategy.on_fill(fill)
                    balance -= fill.size_usd
                elif d.action == "exit":
                    fill = self._simulate_exit(d, tick)
                    self.strategy.on_fill(fill)
                    balance += fill.size_usd + self._compute_pnl(fill)

        state = self.strategy.state()
        self.store.save_run(run_id, config, state.summary)
        return RunResult(run_id=run_id, state=state)
```

### Paper Trading, Dashboard Rendering

Unchanged from v2. See v2 §Paper Trading and §Dashboard Rendering.

---

## strategies/ Repo Structure

```
strategies/                        SEPARATE REPO
├── pyproject.toml                 pip-installable, zero external deps
├── protocol.py                    Strategy Protocol definition
├── dnpm_v2/                       sub-tree: delta-neutral perp maker v2
│   ├── __init__.py                exports DnpmV2Strategy
│   ├── strategy.py
│   ├── scanner_core.py
│   ├── scoring.py
│   ├── hold_evaluator.py
│   ├── entry_gate.py
│   ├── venue.py                   VenueConfig, RateSnapshot
│   ├── venue_registry.py          load_venue_registry from YAML/dict
│   ├── types.py                   MarketTick, Decision, Fill, StrategyState
│   ├── config.py                  strategy param defaults + validation
│   ├── config_schema.py           pydantic schema for config validation
│   ├── templates/
│   │   └── dashboard.html.j2
│   └── tests/
│       ├── conftest.py            shared fixtures
│       ├── test_scoring.py
│       ├── test_hold.py
│       ├── test_entry_gate.py
│       ├── test_scanner_core.py
│       ├── test_strategy.py       integration: tick → decisions
│       └── fixtures/
│           └── ticks_sample.json
├── future_strategy/               placeholder: next strategy goes here
│   └── ...
└── README.md
```

### pyproject.toml

```toml
[project]
name = "openclaw-strategies"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []  # zero external deps — pure Python + stdlib

[project.optional-dependencies]
dev = ["pytest>=7.0"]
schema = ["pydantic>=2.0"]  # only needed if using config_schema.py

[tool.pytest.ini_options]
testpaths = ["dnpm_v2/tests"]
```

### How lab and trade consume it

**Development (all repos cloned locally):**
```bash
# In lab's or trade's pyproject.toml:
[project]
dependencies = [
    "openclaw-strategies @ file:///${PROJECT_ROOT}/../../strategies"
]

# Or simpler: editable install
pip install -e /path/to/strategies
```

**Docker:**
```dockerfile
COPY strategies /app/strategies
RUN pip install -e /app/strategies
```

**CI:** Each repo's CI clones the strategies repo and installs it before running tests.

### Why a separate repo, not builds/strategies/

- **Independence.** Strategy changes don't require lab or trade PRs. Strategy has its own CI, its own version, its own changelog.
- **Cleaner dependency graph.** Lab depends on strategies. Trade depends on strategies. Neither depends on the other. No circular paths.
- **Access control.** Strategy math is the core IP. It can have a different access model than the infra repos.
- **It's basic glue code.** The strategy package is pure math + types. No IO, no frameworks, no build complexity. The overhead of a separate repo is minimal.

---

## Hard Problems and Abstraction Leaks

### 1–7: Unchanged from v2

See v2 §Hard Problems. All seven points (scanner decomposition, hold evaluation crossing cost, position sizing/balance, rotation, config portability, SymbolUniverse, strategy tests) remain valid and unchanged.

### 8. Predicted rate coverage across venues

Current state from dex-factory:

| Venue | `get_predicted_funding_rates()` | API field | Status |
|-------|--------------------------------|-----------|--------|
| Hyperliquid | Implemented | `predictedFundings` → `fundingRate` + `nextFundingTime` | Working |
| Pacifica | Implemented | `/info/prices` → `next_funding` | Working |
| Extended | **Not implemented** | `marketStats.nextFundingRate` exists but parsed as timestamp | Bug — investigate |
| Lighter | Not implemented | Unknown | Audit API docs |
| Paradex | Not implemented | Unknown | Audit API docs |

The base `DexClient` contract returns `[]` by default. Venues without predicted rates gracefully fall back to current rate via `best_estimate_rate()`. This is correct behavior — the strategy degrades, it doesn't break.

**Action items:**
- File issues to audit Extended, Lighter, and Paradex APIs for predicted rate fields
- Fix Extended's `nextFundingRate` parsing (it's a rate, not a timestamp)
- Wire `get_predicted_funding_rates()` into every venue that supports it
- Collector stores whatever each venue provides; `None` where unavailable

### 9. Sub-hourly collection reliability

The per-minute collector must be robust against:
- **Transient API failures:** Log and skip. Missing one minute out of 60 is acceptable — the replay engine uses "nearest minute" matching, so it just picks an adjacent minute.
- **Clock drift:** Use UTC exclusively. Truncate to minute boundary on insert.
- **Write contention:** SQLite WAL mode allows concurrent reads during writes. Collector does batch inserts (one INSERT per venue per minute, not per-symbol).
- **Process crashes:** Cron restarts on the next minute. No state to recover.

If write latency becomes a problem (>10s per collection cycle), options in order:
1. Batch all venue calls with `asyncio.gather()` (should already be doing this)
2. Reduce symbol set (only collect symbols we've ever traded or scored above threshold)
3. Redis as write buffer, flush to SQLite every 5 minutes
4. Per-venue SQLite databases (eliminates write contention entirely)

---

## Issue Breakdown

### Phase 0: strategies/ Repo

#### Issue #1: Create strategies/ repo with scaffold
**Scope:** Repo creation, `pyproject.toml`, `protocol.py`, `dnpm_v2/__init__.py`, `dnpm_v2/types.py`
**Work:**
- Create the repo with the directory structure above
- Define `Strategy` Protocol in `protocol.py`
- Define `MarketTick`, `Decision`, `Fill`, `FundingAccrual`, `StrategyState` in `types.py`
- Zero external deps
- CI: `pytest` on push

**Acceptance:**
- `pip install -e strategies/` works
- Types are importable: `from dnpm_v2.types import MarketTick`
- CI green

---

#### Issue #2: Move pure math modules to strategies/
**Scope:** `strategies/dnpm_v2/{scoring,hold_evaluator,entry_gate,venue,venue_registry}.py`
**Work:**
- Copy scoring.py, hold_evaluator.py, entry_gate.py, venue.py from trade
- Fix imports to use relative (within strategies package)
- Copy existing tests from trade
- Verify all tests pass

**Acceptance:**
- All existing scoring/hold/gate tests pass in the new location
- No dex-factory or HB imports

---

#### Issue #3: Extract scanner_core.py from Scanner
**Scope:** `strategies/dnpm_v2/scanner_core.py`, tests
**Work:**
- Extract `_scan_pairs()` as standalone `scan_pairs()` function
- Extract `ScoredOpportunity` dataclass
- Extract greedy allocation logic
- Modify entry_gate to accept ScoredOpportunity (not RankedOpportunity)
- Tests: given fixture rates, verify scored output

**Acceptance:**
- `scan_pairs()` is a pure function: no self, no clients, no I/O
- Produces identical output to Scanner._scan_pairs() for same input

---

#### Issue #4: Implement DnpmV2Strategy
**Scope:** `strategies/dnpm_v2/strategy.py`, `strategies/dnpm_v2/config.py`
**Work:**
- Implement `configure()`, `on_tick()`, `on_fill()`, `state()`
- Internal position tracking (InternalPosition dataclass)
- Internal balance tracking
- Hold evaluation per-position (using hold_evaluator.py)
- Entry gate filtering (using entry_gate.py)
- Position sizing (using scoring.py compute_position_size)
- Integration test: feed 10 ticks, verify decisions and state

**Acceptance:**
- Deterministic: same ticks + config = same decisions
- Zero external deps
- Strategy tests pass standalone: `cd strategies && pytest`

---

#### Issue #5: Strategy dashboard template
**Scope:** `strategies/dnpm_v2/templates/dashboard.html.j2`
**Work:**
- Adapt from trade's status.html.j2
- Reads from StrategyState (not StatusSnapshot)
- Shows: scan results table, positions table, summary stats
- Self-contained HTML, inline CSS

**Acceptance:**
- Render from fixture StrategyState produces valid HTML
- Visual parity with current live dashboard (roughly)

---

### Phase 1: Lab Foundation

#### Issue #6: SQLite schema + tick store (dual-table)
**Scope:** `hummingbot-lab/db/tick_store.py`, `hummingbot-lab/db/schema.sql`
**Work:**
- Implement dual-table schema: `funding_ticks` + `price_ticks`
- `insert_funding_ticks()`: stores rate_1h + predicted_rate_1h + time_of_next_funding
- `insert_price_ticks()`: stores best_bid, best_ask, mark_price
- `iter_ticks()`: joins funding and price data, yields MarketTick with predicted rates populated
- WAL mode enabled by default
- Minute-precision timestamps on price_ticks

**Acceptance:**
- Insert + read roundtrip test passes
- Yields correct MarketTick with `predicted_rate` populated (not None) when data exists
- Yields MarketTick with bid/ask from nearest-minute price_tick

---

#### Issue #7: Funding collector (hourly)
**Scope:** `hummingbot-lab/collector/collect_funding.py`
**Work:**
- Calls `get_funding_rates()` + `get_predicted_funding_rates()` for each venue
- Stores both in `funding_ticks` table
- Handles venues returning empty predicted rates (stores NULL)

**Acceptance:**
- Collects from HL + Pacifica with predicted rates populated
- Extended/Lighter/Paradex: rate_1h stored, predicted_rate_1h is NULL

---

#### Issue #8: Price collector (per-minute)
**Scope:** `hummingbot-lab/collector/collect_prices.py`
**Work:**
- Calls `get_funding_rates()` per venue (already returns bid/ask)
- Stores bid/ask/mark in `price_ticks` table
- `asyncio.gather()` across venues for parallelism
- Graceful failure: log warning, skip venue on error

**Acceptance:**
- Runs in <30s for 4 venues × 100 symbols
- Missing minutes don't corrupt subsequent queries

---

#### Issue #9: Replay engine
**Scope:** `hummingbot-lab/replay/engine.py`
**Work:**
- `ReplayEngine(db_path, strategy: Strategy)`
- `run(start, end, config) -> RunResult`
- Supports `tick_interval_minutes` config (default 60, can be 1)
- Feed MarketTick from TickStore to strategy
- Simulate fills for enter/exit decisions
- Compute funding accruals between ticks (uses predicted rate when available)
- Track simulated balance
- Log all decisions to SQLite

**Acceptance:**
- Deterministic replay with fixture data
- At tick_interval_minutes=1, bid/ask changes every minute (dislocation model is meaningful)
- Funding accrual uses predicted rates when stored

---

#### Issue #10: Backtest CLI
**Scope:** `hummingbot-lab/scripts/backtest.py`
**Work:**
- `python -m scripts.backtest --db data/lab.db --strategy dnpm_v2 --start ... --end ... --config config.yml`
- `--tick-interval` flag (default 60, option 1 for minute-level)
- Optionally render dashboard HTML (--report)

**Acceptance:**
- End-to-end: collect → backtest → report works

---

### Phase 2: Lab Polish

#### Issue #11: Dashboard renderer (generic)
**Scope:** `hummingbot-lab/dashboard/renderer.py`
**Work:**
- Load strategy's template from strategy package
- Render StrategyState → HTML
- Atomic write to file

---

#### Issue #12: Paper trading runner
**Scope:** `hummingbot-lab/paper/runner.py`
**Work:**
- Uses Strategy protocol
- Fetch live tick (with predicted rates) → build MarketTick → strategy.on_tick() → simulate fills

---

### Phase 3: dex-factory — Predicted Rate Audit

#### Issue #13: Fix Extended `nextFundingRate` parsing
**Scope:** `dex-factory/core/extended/parsing.py`, `dex-factory/core/extended/client.py`
**Work:**
- `marketStats.nextFundingRate` is currently parsed as a timestamp. Investigate: is it actually a rate or a timestamp?
- If it's a rate: implement `get_predicted_funding_rates()` for Extended
- If it's a timestamp: rename the field to avoid confusion, check if another field carries the predicted rate

---

#### Issue #14: Audit Lighter API for predicted rates
**Scope:** `dex-factory/core/lighter/client.py`
**Work:**
- Check Lighter API docs for predicted/next funding rate fields
- If available: implement `get_predicted_funding_rates()`
- If not: document and move on (base class returns [])

---

#### Issue #15: Audit Paradex API for predicted rates
**Scope:** `dex-factory/core/paradex/client.py`
**Work:**
- Check Paradex API docs for predicted funding rate fields
- If available: implement `get_predicted_funding_rates()`
- If not: document and move on

---

### Phase 4: Trade Migration

#### Issue #16: Update trade Scanner to use scanner_core
**Scope:** `hummingbot-trade/controllers/dnpm_v2/scanner.py`
**Work:**
- Import `scan_pairs` from `strategies.dnpm_v2.scanner_core`
- Scanner.scan() becomes: fetch → convert → scan_pairs() → convert to RankedOpportunity
- Delete local `_scan_pairs()`, `_scan_v2()`, `_scan_v2_from_rates()`
- All existing scanner tests must pass

---

#### Issue #17: Create HB adapter
**Scope:** `hummingbot-trade/controllers/dnpm_v2/hb_adapter.py`
**Work:**
- Decision → HB order translation
- Fill ← HB event translation
- Position map (strategy position_id ↔ HB order_id)

---

#### Issue #18: Slim down controller
**Scope:** `hummingbot-trade/controllers/dnpm_v2/controller.py`
**Work:**
- strategy_tick() delegates to strategy.on_tick() + adapter.execute()
- Remove inline scoring, hold evaluation, entry logic
- Keep: lifecycle (start/stop), margin monitoring, dashboard pipeline

---

### Phase 5: Infrastructure

#### Issue #19: Lab Dockerfile + cron
**Scope:** `hummingbot-lab/Dockerfile`, cron configuration
**Work:**
- `python:3.12-slim`
- Install strategies package + dex-factory
- Two cron entries: hourly funding collection, per-minute price collection
- Entry point: collector or backtest (configurable)

---

### Implementation Order

```
Phase 0 (strategies/ repo — no env deps):
  #1 scaffold ──┐
  #2 math  ─────┤  (#2 depends on #1)
  #3 scanner ───┤  (depends on #2)
  #4 strategy ──┤  (depends on #1, #2, #3)
  #5 template ──┘  (depends on #1)

Phase 1 (lab foundation):
  #6 tick store ────┐  (depends on #1)
  #7 funding coll ──┤  (depends on #6)
  #8 price coll ────┤  (depends on #6)
  #9 replay ────────┤  (depends on #4, #6)
  #10 CLI ──────────┘  (depends on #9)

Phase 2 (lab polish):
  #11 dashboard ──┐  (depends on #5, #9)
  #12 paper ──────┘  (depends on #7, #8, #9)

Phase 3 (dex-factory — predicted rates):   ← can run in parallel with Phase 1
  #13 Extended fix ──┐
  #14 Lighter audit ─┤  (all independent)
  #15 Paradex audit ─┘

Phase 4 (trade migration):                 ← can run in parallel with Phase 1
  #16 scanner ────┐  (depends on #3)
  #17 adapter ────┤  (depends on #4)
  #18 controller ─┘  (depends on #16, #17)

Phase 5 (infra):
  #19 Dockerfile + cron ──  (depends on Phase 1)
```

**Critical path:** #1 → #2 → #3 → #4 → #9 → #10 (scaffold → math → scanner → strategy → replay → CLI)

**Parallel tracks after Phase 0:**
- Phase 1 (lab) and Phase 4 (trade migration) are independent
- Phase 3 (dex-factory audits) is independent of everything — can start immediately
- Phase 3 results feed into Phase 1 #7 (funding collector stores whatever predicted rates are available)

---

## Migration Safety

Unchanged from v2. See v2 §Migration Safety. The "copy first, then redirect imports" pattern applies identically, except the copy destination is now a separate repo rather than a monorepo directory.

---

## Resolved Questions (from v2 Open Questions)

| v2 Question | v3 Resolution |
|-------------|---------------|
| Separate repo vs. monorepo for strategies/? | **Separate repo, now.** Not deferred. |
| Predicted rates in backtest? | **Required.** Collector stores them. Schema has columns. Backtest uses them. |
| Sub-hourly ticks? | **1-minute price ticks.** Dual-table schema. Dual-cadence collector. |
| Multi-account in lab? | **Not needed.** One simulated account per run. |
| When to extract strategies/ to its own repo? | **Now.** See above. |
