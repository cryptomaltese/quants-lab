# hummingbot-lab Master Plan v2

> Strategy as a portable object. Both lab and trade import identically.

**Date:** 2026-03-30
**Status:** Draft — reworked from v1 around the portable strategy concept
**Supersedes:** MASTER-PLAN.md (v1)

---

## What Changed From v1

v1 had lab importing pure functions from trade. This created three problems:

1. **Can't develop a strategy in lab without it already existing in trade.** Lab was downstream of trade by construction.
2. **No graduation path.** "Math that works in backtest" and "live strategy" were structurally different things — different code paths, different state management, different assumptions.
3. **The HB integration pain was postponed.** The wedge between what the math thinks is achievable and what's actually executable in Hummingbot would only surface late.

v2 fixes this by making the strategy a **self-contained, environment-agnostic package** that both lab and trade import identically. The two environments differ only in where ticks come from and how decisions get executed.

### What's kept from v1

- SQLite schema (ticks table, decisions table, runs table)
- Collector design (dex-factory adapters → SQLite)
- Single-DB recommendation
- Hourly collection cadence
- No MongoDB migration

### What's reworked

- Everything around code organization and module boundaries
- Strategy adapter pattern → Strategy protocol (strategy owns its logic, not an adapter wrapping someone else's)
- Dashboard approach (strategy owns its template)
- Issue breakdown

---

## Architecture

```
strategies/                        shared package (pip-installable)
  dnpm_v2/
    ├── strategy.py                the black box: tick → decisions
    ├── scoring.py                 pure math (from trade, authoritative copy)
    ├── hold_evaluator.py          pure math (from trade, authoritative copy)
    ├── entry_gate.py              two-gate filter (from trade)
    ├── venue.py                   VenueConfig, RateSnapshot (from trade)
    ├── scanner_core.py            _scan_pairs + greedy allocation (from Scanner)
    ├── types.py                   MarketTick, Decision, Fill, StrategyState
    ├── config.py                  strategy params (portable, no credentials)
    ├── templates/
    │   └── dashboard.html.j2      strategy's own dashboard template
    └── tests/
        ├── test_scoring.py        existing tests (moved from trade)
        ├── test_hold.py
        ├── test_strategy.py       integration: tick → decisions
        └── fixtures/              stored ticks for deterministic replay

hummingbot-lab/                    backtest + paper trade
    ├── db/
    │   ├── tick_store.py          SQLite read/write
    │   └── schema.sql             DDL
    ├── collector/
    │   └── collect.py             dex-factory → SQLite
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
        └── MASTER-PLAN-v2.md      (this file)

hummingbot-trade/                  live execution (changes from current)
    controllers/dnpm_v2/
    ├── hb_adapter.py              NEW: Decision → HB ExecutorAction, Fill ← HB events
    ├── runner.py                  NEW: imports strategy, feeds live ticks
    ├── controller.py              SLIMMED: delegates to strategy via adapter
    ├── scanner.py                 SLIMMED: fetch only, scoring moves to strategy
    ├── config.py                  KEPT: HB-specific config (connectors, credentials)
    ├── status_snapshot.py         ADAPTED: reads from strategy.state()
    └── ...

dex-factory/                       unchanged — single source of truth for venue APIs
```

### Data flow

```
              ┌──────────────────────────┐
              │    dex-factory adapters   │
              │  get_funding_rates()      │
              └──────────┬───────────────┘
                         │
           ┌─────────────┼─────────────┐
           │             │             │
           ▼             ▼             ▼
     ┌──────────┐  ┌──────────┐  ┌──────────┐
     │ COLLECT  │  │  TRADE   │  │  PAPER   │
     │ (cron)   │  │ (live)   │  │  TRADE   │
     └────┬─────┘  └────┬─────┘  └────┬─────┘
          │              │             │
          │        FundingData    FundingData
          │         → MarketTick   → MarketTick
          │              │             │
          ▼              ▼             ▼
     ┌──────────┐  ┌──────────────────────────┐
     │  SQLite  │  │   strategies/dnpm_v2/    │
     │ ticks.db │  │   strategy.on_tick()     │
     └────┬─────┘  │         │                │
          │        │    list[Decision]         │
     MarketTick    │         │                │
          │        └─────────┼────────────────┘
          ▼                  │
     ┌──────────┐      ┌────┴──────┐
     │  REPLAY  │      │  RUNNER   │
     │  ENGINE  │      │ (env-     │
     │   ↓      │      │ specific) │
     │ strategy │      └────┬──────┘
     │ .on_tick │           │
     │   ↓      │      Fill (real or simulated)
     │ Decision │           │
     │   log    │      strategy.on_fill()
     └──────────┘
```

---

## The Strategy Protocol

### Interface

```python
from typing import Protocol
from datetime import datetime

class Strategy(Protocol):
    """
    A strategy is a stateful black box.

    It receives market data, emits trading decisions, and accepts
    execution feedback. It never performs I/O. It never knows which
    environment it runs in.
    """

    def configure(self, config: dict) -> None:
        """
        Initialize strategy with parameters.

        Called once before the first tick. Config is a plain dict —
        strategy validates and stores what it needs.
        """
        ...

    def on_tick(self, tick: MarketTick) -> list[Decision]:
        """
        Process one market data snapshot. Return trading decisions.

        Called once per tick (hourly for DNPM v2). The strategy:
        1. Scores all opportunities from tick.rates
        2. Evaluates existing positions for hold/exit
        3. Evaluates new entry opportunities
        4. Returns a list of Decision objects

        Must be deterministic: same tick + same internal state = same output.
        Must not perform I/O.
        """
        ...

    def on_fill(self, fill: Fill) -> None:
        """
        Accept execution feedback from the runner.

        Called by the runner after it executes (or simulates) a Decision.
        The strategy updates its internal position/balance state.

        In backtest: called immediately with simulated fill.
        In live: called when order confirmation arrives.
        In paper: called immediately with simulated fill (like backtest).
        """
        ...

    def state(self) -> StrategyState:
        """
        Return current strategy state for dashboard rendering.

        The returned StrategyState includes everything the dashboard
        template needs. Called after on_tick() by the runner.
        """
        ...
```

### Why these four methods and not more

**Considered and rejected:**

- `on_balance_update(balance)` — Balance is part of MarketTick. The runner sets it there. Avoids a separate callback channel.
- `reset()` — Use a new instance. Strategies are cheap.
- `on_position_sync(positions)` — Reconciliation is a live-only concern. The HB adapter handles it outside the protocol.
- `finalize() -> dict` — Replaced by `state()`. The runner calls `state()` at the end and extracts summary stats from it.

**Why `on_fill()` exists (not just return-and-forget):**

The strategy needs to update its internal position tracking. Without on_fill(), it would have to assume its decisions were executed perfectly. In live trading, fills are partial, delayed, or rejected. The strategy must know what actually happened to make correct subsequent decisions.

If backtest assumes instant fills, `on_fill()` is still called — it just happens synchronously in the same tick. The strategy code path is identical.

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
    # Runner provides this. Backtest: simulated. Live: from exchange.
    # Strategy uses it for position sizing.
```

**Why `dict[str, list[RateSnapshot]]` and not `list[FundingData]`?**

FundingData is a dex-factory model. The strategy shouldn't depend on dex-factory directly — it adds a transitive dependency and couples the strategy to a specific data source format. RateSnapshot is already a clean, minimal dataclass in venue.py with exactly the fields the scoring model needs (venue_id, current_rate, predicted_rate, best_bid, best_ask). The runner converts FundingData → RateSnapshot at the boundary.

**Why `available_balance_usd` on the tick?**

Position sizing needs current balance. In live, this comes from exchange APIs. In backtest, it's tracked by the replay engine's simulated balance. By putting it on the tick, the strategy doesn't need a separate "balance query" mechanism — everything it needs arrives in one shot.

### Decision

```python
@dataclass(frozen=True)
class Decision:
    """A trading decision emitted by the strategy."""

    ts: datetime              # tick timestamp this decision is for
    action: str               # "enter", "exit", "hold"
    symbol: str               # canonical symbol
    venue_a: str              # first venue in pair
    venue_b: str              # second venue in pair
    direction: str            # "short_a_long_b" or "long_a_short_b"
    size_usd: float           # desired position size (0 for exit/hold)
    score_bps: float          # entry/hold score that motivated this decision
    reason: str               # human-readable explanation
    meta: dict                # strategy-specific data (horizon, crossing cost, etc.)
    position_id: str | None   # for exit/hold: which position this refers to
```

**What the runner does with each action:**

- `enter`: Translate to venue-specific orders. Execute. Call `on_fill()`.
- `exit`: Close the referenced position. Call `on_fill()` with close data.
- `hold`: Log only. No execution. (Important for decision log completeness.)

**Why `meta: dict`?**

Different strategies will want to log different diagnostic data. DNPM v2 logs crossing_cost_bps, best_horizon, fee_roundtrip, etc. A future strategy might log entirely different metrics. The structured fields (symbol, venue_a, etc.) cover what every strategy needs; meta covers what's strategy-specific.

### Fill

```python
@dataclass(frozen=True)
class Fill:
    """Execution feedback from the runner to the strategy."""

    decision_id: str          # links back to the Decision
    ts: datetime              # when fill occurred
    action: str               # "enter" or "exit" (mirrors decision)
    symbol: str
    venue_a: str
    venue_b: str
    direction: str
    size_usd: float           # actual filled size (may differ from requested)
    fill_price_a: float       # execution price on venue A (0 if no fill)
    fill_price_b: float       # execution price on venue B (0 if no fill)
    status: str               # "filled", "partial", "rejected", "timeout"
    entry_cost_bps: float     # actual crossing cost at fill time
    position_id: str          # runner assigns this for new positions
```

**Backtest fill simulation:**

```python
# In replay engine:
for decision in decisions:
    if decision.action == "enter":
        fill = simulate_fill(decision, tick)  # instant fill at mid-price
        strategy.on_fill(fill)
    elif decision.action == "exit":
        fill = simulate_exit(decision, tick)
        strategy.on_fill(fill)
```

### StrategyState

```python
@dataclass
class StrategyState:
    """Strategy state for dashboard rendering. Strategy-specific."""

    strategy_name: str        # "dnpm_v2"
    template_name: str        # "dashboard.html.j2" — strategy provides its own

    # Common fields (every strategy provides these)
    ts: datetime              # last tick time
    positions: list[dict]     # open positions (schema is strategy-specific)
    summary: dict             # PnL, trade count, etc.

    # Strategy-specific data (template knows how to render this)
    scan_results: list[dict]  # top N opportunities (DNPM v2 specific)
    extra: dict               # anything else the template needs
```

**Why the template comes with the strategy:**

A funding rate arb strategy shows opportunities, spreads, TTBE. A momentum strategy would show signal strength, correlation, drawdown. The dashboard must be strategy-specific. Shipping the template in the strategy package means:
1. Lab and trade render identical dashboards for the same strategy
2. Adding a new strategy automatically adds its dashboard
3. No need to update lab or trade's rendering code per-strategy

---

## DNPM v2 as a Strategy

### What moves to `strategies/dnpm_v2/`

| File | Source | Notes |
|------|--------|-------|
| `scoring.py` | trade/controllers/dnpm_v2/scoring.py | Verbatim. Pure math, no changes needed. |
| `hold_evaluator.py` | trade/controllers/dnpm_v2/hold_evaluator.py | Verbatim. Pure math. |
| `entry_gate.py` | trade/controllers/dnpm_v2/entry_gate.py | Verbatim. Pure math. |
| `venue.py` | trade/controllers/dnpm_v2/venue.py | Verbatim. VenueConfig + RateSnapshot. |
| `scanner_core.py` | Extracted from trade/controllers/dnpm_v2/scanner.py | `_scan_pairs()` + greedy allocation. Pure function: rates → scored opportunities. |
| `types.py` | NEW | MarketTick, Decision, Fill, StrategyState |
| `strategy.py` | NEW | DnpmV2Strategy implementing the Strategy protocol |
| `config.py` | Subset of trade/controllers/dnpm_v2/config.py | Strategy params only (no HB connectors, no credentials) |
| `templates/dashboard.html.j2` | Adapted from trade/.../templates/status.html.j2 | Same visual design, reads from StrategyState |

### What stays in hummingbot-trade

| Component | Why it stays |
|-----------|-------------|
| `controller.py` | HB lifecycle integration (ControllerBase subclass) |
| `hb_adapter.py` (new) | Decision → ExecutorAction translation |
| `entry.py` | HB order placement (EntryManager) |
| `management.py` | OpenPosition, PositionManager (HB-specific position tracking) |
| `router.py` | VenueRouter, VenueState (HB connector-aware) |
| `scanner.py` | Fetch-only wrapper: clients → FundingData → MarketTick → strategy |
| `config.py` | Full config including venue_connectors, credentials paths, HB params |
| `status_snapshot.py` | Reads from strategy.state(), wraps in Pydantic for HB pipeline |

### scanner_core.py — the extracted scoring engine

The current Scanner._scan_pairs() is already a pure function: `dict[str, list[RateSnapshot]] → list[ScoredOpportunity]`. It imports only from scoring.py and venue.py. Extraction is mechanical:

```python
# strategies/dnpm_v2/scanner_core.py

from .scoring import (
    MU_1H, best_estimate_rate, crossing_cost,
    cumulative_funding, fee_roundtrip, fee_close, score_pair,
)
from .venue import VenueConfig, RateSnapshot

@dataclass
class ScoredOpportunity:
    symbol: str
    venue_a: str
    venue_b: str
    direction: str
    horizon_hours: int
    score_entry: float      # bps
    score_hold: float       # bps
    spread_raw: float
    fee_roundtrip: float
    rate_a: float
    rate_b: float
    crossing_cost_bps: float = 0.0

def scan_pairs(
    rates: dict[str, list[RateSnapshot]],
    venue_registry: dict[str, VenueConfig],
    horizons: list[int],
    entry_threshold: float = 2.0,
    staleness_threshold_s: float = 300.0,
) -> list[ScoredOpportunity]:
    """Pure function: rates in, scored opportunities out.

    Extracted from Scanner._scan_pairs(). No self, no clients, no state.
    """
    ...  # same logic as current _scan_pairs
```

Trade's Scanner then becomes:

```python
# In hummingbot-trade, after migration:
class Scanner:
    async def scan(self) -> list[RankedOpportunity]:
        rates = await self._fetch_all_rates()  # still does fetch
        scored = scan_pairs(rates, self._venue_registry, self.horizons)
        return self._to_ranked(scored)  # HB compat conversion
```

### DnpmV2Strategy — the black box

```python
class DnpmV2Strategy:
    """DNPM v2: cross-venue funding rate arbitrage."""

    def configure(self, config: dict) -> None:
        self._venue_registry = build_venue_registry(config)
        self._horizons = config.get("horizons", [1, 2, 4, 8, 16, 24])
        self._min_score_bps = config.get("min_score_bps", 15.0)
        self._pessimism_factor = config.get("pessimism_factor", 0.95)
        self._phi = config.get("phi", 0.98)
        self._hold_params = HoldParams(
            phi_per_hour=config.get("phi_per_hour", 0.98),
            psi_per_minute=config.get("psi_per_minute", 0.50),
            lookahead_hours=config.get("hold_lookahead_hours", 1.0),
        )
        self._size_floor_pct = config.get("size_floor_pct", 0.10)
        self._size_cap_pct = config.get("size_cap_pct", 0.25)
        self._size_scale_per_bps = config.get("size_scale_per_bps", 0.05)
        self._positions: list[InternalPosition] = []
        self._closed: list[InternalPosition] = []
        self._last_scan: list[ScoredOpportunity] = []
        self._tick_count = 0

    def on_tick(self, tick: MarketTick) -> list[Decision]:
        self._tick_count += 1
        decisions = []

        # 1. Score all cross-venue pairs
        self._last_scan = scan_pairs(
            tick.rates,
            self._venue_registry,
            self._horizons,
        )

        # 2. Evaluate existing positions
        for pos in list(self._positions):
            hold_decision = self._evaluate_hold(pos, tick)
            if hold_decision.action == "exit":
                decisions.append(self._make_exit_decision(pos, tick, hold_decision))
            else:
                decisions.append(self._make_hold_decision(pos, tick, hold_decision))

        # 3. Evaluate new entries (entry gate + sizing)
        for opp in self._last_scan:
            if self._passes_entry_gate(opp):
                size = self._compute_size(opp, tick.available_balance_usd)
                if size > 0:
                    decisions.append(self._make_enter_decision(opp, tick, size))

        return decisions

    def on_fill(self, fill: Fill) -> None:
        if fill.action == "enter" and fill.status in ("filled", "partial"):
            self._positions.append(InternalPosition(
                position_id=fill.position_id,
                symbol=fill.symbol,
                venue_a=fill.venue_a,
                venue_b=fill.venue_b,
                direction=fill.direction,
                size_usd=fill.size_usd,
                entry_ts=fill.ts,
                entry_cost_bps=fill.entry_cost_bps,
                cumulative_funding_bps=0.0,
            ))
        elif fill.action == "exit":
            pos = self._find_position(fill.position_id)
            if pos:
                self._positions.remove(pos)
                self._closed.append(pos)

    def state(self) -> StrategyState:
        return StrategyState(
            strategy_name="dnpm_v2",
            template_name="dashboard.html.j2",
            ts=...,
            positions=[self._pos_to_dict(p) for p in self._positions],
            summary=self._compute_summary(),
            scan_results=[self._opp_to_dict(o) for o in self._last_scan[:20]],
            extra={},
        )
```

### What about funding accrual?

In live trading, the strategy receives actual funding via `FundingUpdate` events from the exchange. In backtest, funding must be simulated.

**The approach:** Funding accrual is computed by the runner, not the strategy. Each tick, the runner calculates how much funding each position earned since the last tick (using the stored/live rates), and informs the strategy via a lightweight update:

```python
@dataclass(frozen=True)
class FundingAccrual:
    """Funding earned/paid on a position since last tick."""
    position_id: str
    amount_bps: float         # positive = earned, negative = paid
    period_hours: float       # settlement period that just elapsed
```

The strategy's `on_tick` receives these in the MarketTick:

```python
@dataclass(frozen=True)
class MarketTick:
    ts: datetime
    rates: dict[str, list[RateSnapshot]]
    available_balance_usd: float
    funding_accruals: list[FundingAccrual] = field(default_factory=list)
```

In backtest: replay engine computes accruals from stored rates.
In live: runner computes accruals from `get_cumulative_funding_since_open()`.
In both cases: strategy updates `pos.cumulative_funding_bps += accrual.amount_bps`.

This keeps the strategy's hold evaluator correct — it uses `cumulative_funding_received_bps` which stays accurate regardless of environment.

---

## The HB Adapter (hummingbot-trade)

### How thin can it be?

The adapter translates between Strategy decisions and Hummingbot's execution model. Here's the translation:

```
Strategy Decision              HB Action
─────────────────              ─────────
enter(sym, venue_a, venue_b,   EntryManager.enter(opportunity)
      direction, size_usd)      → place limit orders on both venues
                                → monitor_fill()
                                → on_fill(Fill)

exit(position_id)              ExitManager.exit_position(position)
                                → close orders on both venues
                                → on_fill(Fill)

hold(position_id)              (no-op, logged only)
```

### Hard translation problems

1. **Order types.** The strategy says "enter at $500 on HL+Extended". HB uses specific order types (limit-at-mid with 4-minute timeout). The adapter encodes this — it's execution policy, not strategy logic.

2. **Partial fills.** Strategy decides to enter; one leg fills, the other doesn't. The adapter must handle this (cancel unfilled leg, report partial Fill to strategy). This is the hardest part of the adapter.

3. **Concurrent positions.** The strategy may emit multiple enter decisions in one tick. The adapter must execute them serially or with careful concurrency to avoid double-allocating balance.

4. **Position reconciliation.** Strategy tracks positions internally via on_fill(). But exchange state may diverge (e.g., manual intervention, unexpected liquidation). The adapter should periodically reconcile and emit corrective fills if needed. This is NOT part of the strategy protocol — it's adapter-level bookkeeping.

5. **Margin monitoring.** The monitor_tick_loop (30s interval for liquidation checks) stays in the adapter/controller. If margin is critical, the adapter force-closes positions and sends exit Fills to the strategy.

### Adapter sketch

```python
class HbAdapter:
    """Translates Strategy decisions to HB executor actions."""

    def __init__(self, strategy: DnpmV2Strategy, entry_mgr, exit_mgr, ...):
        self._strategy = strategy
        self._entry = entry_mgr
        self._exit = exit_mgr
        self._position_map: dict[str, OpenPosition] = {}  # strategy_id → HB position

    async def execute_decisions(self, decisions: list[Decision]) -> None:
        for d in decisions:
            if d.action == "enter":
                result = await self._entry.enter(self._to_opportunity(d))
                fill_status = await self._entry.monitor_fill(result.order_id)
                fill = self._to_fill(d, result, fill_status)
                self._strategy.on_fill(fill)
            elif d.action == "exit":
                hb_pos = self._position_map[d.position_id]
                result = await self._exit.exit_position(hb_pos, ...)
                fill = self._to_exit_fill(d, result)
                self._strategy.on_fill(fill)
```

### What changes in the existing controller

The controller becomes a thin orchestrator:

```python
class DnpmV2Controller(ControllerBase):
    async def strategy_tick(self):
        # 1. Fetch rates from all venues
        rates = await self._fetch_rates()

        # 2. Build MarketTick
        tick = MarketTick(
            ts=now,
            rates=rates,
            available_balance_usd=self._get_balance(),
            funding_accruals=self._compute_accruals(),
        )

        # 3. Strategy decides
        decisions = self._strategy.on_tick(tick)

        # 4. Adapter executes
        await self._adapter.execute_decisions(decisions)

        # 5. Dashboard
        state = self._strategy.state()
        self._render_dashboard(state)
```

This is dramatically simpler than the current controller (which has scan + route + enter + manage + monitor all interleaved).

---

## hummingbot-lab Design

### Collector (unchanged from v1)

Same design: async script calls dex-factory clients → inserts FundingData into SQLite.

```python
async def collect(db_path: str, venues: list[str]) -> int:
    batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    store = TickStore(db_path)
    async with aiohttp.ClientSession() as session:
        clients = build_clients(session, venues)
        for venue_name, client in clients.items():
            rates = await client.get_funding_rates()
            store.insert_ticks(batch_id, rates)
    return store.batch_count(batch_id)
```

Cron: `2 * * * *`

### TickStore (unchanged from v1)

```python
class TickStore:
    def insert_ticks(batch_id, rates: list[FundingData]) -> int
    def iter_ticks(start, end) -> Iterator[MarketTick]
    def log_decision(run_id, decision: Decision)
    def save_run(run_id, strategy, mode, config, summary)
```

Key change: `iter_ticks()` now yields `MarketTick` (not the v1 `Tick` type). It converts stored rows to `RateSnapshot` objects and groups by timestamp.

```python
def iter_ticks(self, start, end, initial_balance=1000.0) -> Iterator[MarketTick]:
    for ts, group in groupby(rows, key=lambda r: r['ts']):
        rates = defaultdict(list)
        for row in group:
            snap = RateSnapshot(
                venue_id=row['venue'],
                symbol=row['symbol'],
                current_rate=row['rate_1h'] * 10000 * row['settlement_period_hours'],
                predicted_rate=None,  # not stored
                best_bid=row['best_bid'] or 0.0,
                best_ask=row['best_ask'] or 0.0,
                timestamp=ts_to_unix(row['ts']),
            )
            rates[row['symbol']].append(snap)

        yield MarketTick(
            ts=datetime.fromisoformat(ts),
            rates=dict(rates),
            available_balance_usd=initial_balance,  # updated by replay engine
        )
```

### Replay Engine

```python
class ReplayEngine:
    def __init__(self, db_path: str, strategy: Strategy):
        self.store = TickStore(db_path)
        self.strategy = strategy

    def run(self, start, end, config: dict) -> RunResult:
        run_id = uuid4().hex[:12]
        self.strategy.configure(config)
        balance = config.get("initial_balance_usd", 1000.0)

        for tick in self.store.iter_ticks(start, end, balance):
            # Compute funding accruals for open positions
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

            # Update balance for next tick
            tick = replace(tick, available_balance_usd=balance)

        state = self.strategy.state()
        self.store.save_run(run_id, config, state.summary)
        return RunResult(run_id=run_id, state=state)
```

### Fill Simulation

```python
def _simulate_fill(self, decision: Decision, tick: MarketTick) -> Fill:
    """Instant fill at mid-price. Conservative simplification."""
    # Get bid/ask from tick for both venues
    snap_a = self._find_snap(tick, decision.symbol, decision.venue_a)
    snap_b = self._find_snap(tick, decision.symbol, decision.venue_b)

    # Mid-price (or 0 if no bid/ask)
    price_a = (snap_a.best_bid + snap_a.best_ask) / 2 if snap_a else 0.0
    price_b = (snap_b.best_bid + snap_b.best_ask) / 2 if snap_b else 0.0

    # Entry crossing cost from actual bid/ask
    entry_cost = crossing_cost(
        snap_a.best_bid, snap_a.best_ask,
        snap_b.best_bid, snap_b.best_ask,
        decision.direction,
    ) if snap_a and snap_b else 0.0

    return Fill(
        decision_id=decision.id,
        ts=tick.ts,
        action="enter",
        symbol=decision.symbol,
        venue_a=decision.venue_a,
        venue_b=decision.venue_b,
        direction=decision.direction,
        size_usd=decision.size_usd,
        fill_price_a=price_a,
        fill_price_b=price_b,
        status="filled",
        entry_cost_bps=entry_cost,
        position_id=uuid4().hex[:12],
    )
```

### Paper Trading

Paper trading = replay engine on live ticks. Same as v1:

```python
class PaperRunner:
    async def run(self, config: dict):
        self.strategy.configure(config)
        while not self._stop:
            tick = await self._fetch_live_tick()
            self.store.insert_ticks(tick.batch_id, tick.raw_funding)
            decisions = self.strategy.on_tick(tick)
            for d in decisions:
                self.store.log_decision(self.run_id, d)
                if d.action in ("enter", "exit"):
                    fill = self._simulate_fill(d, tick)
                    self.strategy.on_fill(fill)
            await self._sleep_until_next_tick()
```

### Dashboard Rendering

Lab provides a generic rendering pipeline. The strategy provides its template.

```python
class DashboardRenderer:
    def render(self, strategy_state: StrategyState) -> str:
        """Load strategy's template, render with its state."""
        # Template lives in the strategy package
        template_dir = strategies_package_path / strategy_state.strategy_name / "templates"
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
        template = env.get_template(strategy_state.template_name)
        return template.render(state=strategy_state)
```

Same rendering in lab and trade. Strategy determines what's shown.

---

## SQLite Schema

Unchanged from v1. The ticks table stores raw FundingData. The decisions table stores Strategy decisions. The runs table stores run metadata.

```sql
CREATE TABLE ticks (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,
    venue       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    rate_1h     REAL    NOT NULL,
    best_bid    REAL,
    best_ask    REAL,
    settlement_period_hours REAL NOT NULL DEFAULT 1.0,
    is_hip3     INTEGER NOT NULL DEFAULT 0,
    dex_name    TEXT    NOT NULL DEFAULT '',
    batch_id    TEXT    NOT NULL
);

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
    position_id TEXT                  -- links enter/hold/exit for same position
);

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

Addition vs. v1: `position_id` column on decisions, `size_usd` column on decisions. Both are needed to reconstruct position lifecycle from the decision log.

---

## Hard Problems and Abstraction Leaks

### 1. Scanner decomposition is the critical path

The current Scanner is 900+ lines mixing fetch, convert, score, gate, allocate, hold-evaluate, and rotation-detect. Extracting `scan_pairs()` as a pure function is straightforward — it already is one internally. But the entry gate currently takes a `RankedOpportunity` (HB-specific type). It needs to take a `ScoredOpportunity` instead.

**Risk:** Medium. The entry gate logic is simple (two multiplications and two comparisons). Changing its input type is a one-line change.

### 2. Hold evaluation needs crossing cost from the tick

`evaluate_hold()` takes `live_crossing_exit_bps` — the current bid/ask spread for exiting. In live, this comes from real-time orderbook data. In backtest, it comes from stored bid/ask.

**The leak:** Stored bid/ask is a snapshot at collection time (hourly). Real bid/ask fluctuates within the hour. The hold evaluator's price dislocation model (ψ per minute) is designed for real-time data. In backtest, the hourly snapshot makes the dislocation term meaningless — ψ^60 ≈ 0 for any practical ψ.

**Mitigation:** In backtest, set `live_crossing_exit_bps = 0` (assume dislocation has fully decayed by next tick). This simplifies hold evaluation to funding-only, which is the conservative and correct thing to do with hourly data. Document this as a known simplification.

### 3. Position sizing needs balance, balance needs fills

The strategy sizes positions based on `available_balance_usd`. But after emitting an "enter" decision, the balance should decrease. If the strategy emits two enter decisions in one tick, the second one should see the reduced balance.

**Solution:** The strategy tracks balance internally. `on_fill()` updates it. But in one tick, the strategy emits decisions first, then fills arrive. Two options:

- **Option A:** Strategy reserves balance optimistically as it generates enter decisions within `on_tick()`. If a fill comes back rejected, it un-reserves.
- **Option B:** Runner processes decisions one at a time, calling `on_fill()` between each. This means the strategy's `on_tick()` only emits one enter at a time, and the runner loops until no more enters are emitted.

**Recommendation: Option A.** It's simpler and matches how the current code works (the router selects ONE best opportunity per tick). The strategy can internally deduct from available balance as it generates entries, and correct on fill/reject. For DNPM v2 specifically, the router logic (one entry per tick) already prevents double-allocation.

### 4. Rotation requires knowing current positions AND new opportunities

Rotation decision: "is this new opportunity good enough to replace my worst position?" This requires the strategy to compare new scan results against existing positions. This is already strategy-internal — the strategy knows both its positions and the scan results.

**No leak here.** Rotation is pure strategy logic. The runner just sees enter + exit decisions.

### 5. Config portability

Strategy config (phi, horizons, min_score_bps, size params) must be the same between lab and trade. But trade also needs HB-specific config (connector names, credentials, order timeout).

**Solution:** Two-layer config:

```yaml
# strategy config (portable, checked into strategies/)
strategy:
  phi: 0.98
  horizons: [1, 2, 4, 8, 16, 24]
  min_score_bps: 15.0
  size_floor_pct: 0.10
  size_cap_pct: 0.25

# environment config (not portable)
environment:
  venue_connectors:
    hyperliquid: hyperliquid_perpetual
    extended: extended_perpetual
  fill_timeout_seconds: 240
  credentials_path: conf/credentials.yml
```

The strategy only sees the `strategy` section. The runner handles the `environment` section.

### 6. What happens to SymbolUniverse?

SymbolUniverse currently matches HL symbols to Extended symbols (e.g., both return "BTC" but naming conventions differ for some assets). In the strategy, symbol matching is embedded in `scan_pairs()` — it matches by exact symbol name across venues.

**Status quo works.** dex-factory already normalizes symbols to canonical form. The collector writes canonical symbols. `scan_pairs()` matches by exact name. SymbolUniverse was a workaround for inconsistent naming that dex-factory has since solved.

If we encounter edge cases (different symbol names across venues), the runner normalizes at the boundary before building the MarketTick.

### 7. Strategy tests without lab or trade

The strategy package must be testable standalone:

```bash
cd strategies/
pip install -e .
pytest dnpm_v2/tests/
```

No dex-factory imports. No HB imports. No SQLite. Tests use stored fixtures (JSON files with MarketTick data) and verify that `on_tick()` produces expected decisions.

This is achievable because the strategy only depends on its own types (venue.py, types.py) and stdlib math.

---

## strategies/ Package Structure

```
strategies/
├── pyproject.toml              # pip-installable, no external deps
├── dnpm_v2/
│   ├── __init__.py             # exports DnpmV2Strategy
│   ├── strategy.py             # the Strategy implementation
│   ├── scanner_core.py         # scan_pairs() pure function
│   ├── scoring.py              # from trade (authoritative copy)
│   ├── hold_evaluator.py       # from trade (authoritative copy)
│   ├── entry_gate.py           # from trade
│   ├── venue.py                # VenueConfig, RateSnapshot
│   ├── venue_registry.py       # load_venue_registry from YAML
│   ├── types.py                # MarketTick, Decision, Fill, StrategyState
│   ├── config.py               # strategy param defaults + validation
│   ├── templates/
│   │   └── dashboard.html.j2   # strategy dashboard template
│   └── tests/
│       ├── test_scoring.py
│       ├── test_hold.py
│       ├── test_entry_gate.py
│       ├── test_scanner_core.py
│       ├── test_strategy.py    # integration: tick → decisions
│       └── fixtures/
│           └── ticks_sample.json
└── protocol.py                 # Strategy Protocol definition
```

### pyproject.toml

```toml
[project]
name = "openclaw-strategies"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []  # zero external deps — pure Python + stdlib

[tool.pytest.ini_options]
testpaths = ["dnpm_v2/tests"]
```

### How lab and trade consume it

**During development (monorepo):**
```bash
# In lab's pyproject.toml:
[project]
dependencies = ["openclaw-strategies @ file:///${PROJECT_ROOT}/../strategies"]

# Or simpler: sys.path manipulation in runner.py
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "strategies"))
from dnpm_v2 import DnpmV2Strategy
```

**In Docker:**
```dockerfile
COPY builds/strategies /app/strategies
RUN pip install -e /app/strategies
```

---

## Issue Breakdown

### Phase 0: Strategy Package (no environment deps)

#### Issue #1: Create strategies/ package with types
**Scope:** `strategies/pyproject.toml`, `strategies/protocol.py`, `strategies/dnpm_v2/types.py`
**Work:**
- Define `Strategy` Protocol
- Define `MarketTick`, `Decision`, `Fill`, `FundingAccrual`, `StrategyState` dataclasses
- Zero external deps
- Full docstrings with examples

**Acceptance:**
- `pip install -e strategies/` works
- Types are importable: `from dnpm_v2.types import MarketTick`

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
- Works identically in lab and trade

**Acceptance:**
- Render from fixture StrategyState produces valid HTML
- Visual parity with current live dashboard (roughly)

---

### Phase 1: Lab Foundation

#### Issue #6: SQLite tick store
**Scope:** `hummingbot-lab/db/tick_store.py`, `hummingbot-lab/db/schema.sql`
**Work:**
- Same as v1 issue #1, but `iter_ticks()` yields `MarketTick`
- Conversion: stored FundingData rows → `RateSnapshot` → `MarketTick`

**Acceptance:**
- Insert + read roundtrip test passes
- Yields correct MarketTick with proper rate conversion (decimal → bps × period)

---

#### Issue #7: Collector
**Scope:** `hummingbot-lab/collector/collect.py`
**Work:**
- Same as v1 issue #2 (unchanged — collector doesn't know about strategies)

---

#### Issue #8: Replay engine
**Scope:** `hummingbot-lab/replay/engine.py`
**Work:**
- `ReplayEngine(db_path, strategy: Strategy)`
- `run(start, end, config) -> RunResult`
- Feed MarketTick from TickStore to strategy
- Simulate fills for enter/exit decisions
- Compute funding accruals between ticks
- Track simulated balance
- Log all decisions to SQLite

**Acceptance:**
- Deterministic replay with fixture data
- Fill simulation uses stored bid/ask
- Funding accrual computed correctly
- Decision log matches expected output

---

#### Issue #9: Backtest CLI
**Scope:** `hummingbot-lab/scripts/backtest.py`
**Work:**
- `python -m scripts.backtest --db data/lab.db --strategy dnpm_v2 --start ... --end ... --config config.yml`
- Instantiate strategy + engine, run, print summary
- Optionally render dashboard HTML (--report)

**Acceptance:**
- End-to-end: collect → backtest → report works

---

### Phase 2: Lab Polish

#### Issue #10: Dashboard renderer (generic)
**Scope:** `hummingbot-lab/dashboard/renderer.py`
**Work:**
- Load strategy's template from strategy package
- Render StrategyState → HTML
- Atomic write to file

---

#### Issue #11: Paper trading runner
**Scope:** `hummingbot-lab/paper/runner.py`
**Work:**
- Same as v1 issue #8, but uses Strategy protocol
- Fetch live tick → build MarketTick → strategy.on_tick() → simulate fills

---

### Phase 3: Trade Migration

#### Issue #12: Update trade Scanner to use scanner_core
**Scope:** `hummingbot-trade/controllers/dnpm_v2/scanner.py`
**Work:**
- Import `scan_pairs` from `strategies.dnpm_v2.scanner_core`
- Scanner.scan() becomes: fetch → convert → scan_pairs() → convert to RankedOpportunity
- Delete `_scan_pairs()`, `_scan_v2()`, `_scan_v2_from_rates()` (replaced by import)
- All existing scanner tests must pass

**Acceptance:**
- Scanner behavior unchanged
- scan_unfiltered() still works
- Dashboard (live_dashboard.py) still works

---

#### Issue #13: Create HB adapter
**Scope:** `hummingbot-trade/controllers/dnpm_v2/hb_adapter.py`
**Work:**
- `HbAdapter(strategy, entry_mgr, exit_mgr)`
- Decision → HB order translation
- Fill ← HB event translation
- Position map (strategy position_id ↔ HB order_id)

---

#### Issue #14: Slim down controller
**Scope:** `hummingbot-trade/controllers/dnpm_v2/controller.py`
**Work:**
- strategy_tick() delegates to strategy.on_tick() + adapter.execute()
- Remove inline scoring, hold evaluation, entry logic
- Keep: lifecycle (start/stop), margin monitoring, dashboard pipeline

**Acceptance:**
- Live behavior unchanged (verified by paper trading before live deploy)

---

### Phase 4: Infrastructure

#### Issue #15: Lab Dockerfile
**Scope:** `hummingbot-lab/Dockerfile`
**Work:**
- `python:3.12-slim`
- Install strategies package + dex-factory
- Entry point: collector or backtest

---

### Implementation Order

```
Phase 0 (strategy package — no env deps):
  #1 types ────┐
  #2 math  ────┤  (parallel — no deps between them)
  #3 scanner ──┤  (depends on #2)
  #4 strategy ─┤  (depends on #1, #2, #3)
  #5 template ─┘  (depends on #1)

Phase 1 (lab foundation):
  #6 tick_store ──┐  (depends on #1)
  #7 collector ───┤  (depends on #6, independent of strategy)
  #8 replay ──────┤  (depends on #4, #6)
  #9 CLI ─────────┘  (depends on #8)

Phase 2 (lab polish):
  #10 dashboard ──┐  (depends on #5, #8)
  #11 paper ──────┘  (depends on #7, #8)

Phase 3 (trade migration):
  #12 scanner ────┐  (depends on #3)
  #13 adapter ────┤  (depends on #4)
  #14 controller ─┘  (depends on #12, #13)

Phase 4 (infra):
  #15 Dockerfile ──  (depends on Phase 1)
```

**Critical path:** #1 → #4 → #8 → #9 (types → strategy → replay → CLI)

Phase 0 is the highest-leverage work. Once the strategy package exists with passing tests, both lab and trade can independently integrate it.

Phase 3 can proceed in parallel with Phase 1 — trade migration and lab buildout are independent once the strategy package (Phase 0) is complete.

---

## Migration Safety

### The "copy first, then redirect imports" pattern

Scoring.py, hold_evaluator.py, etc. currently live in trade and have tests there. The migration:

1. **Copy** files to strategies/ (Phase 0, issue #2)
2. **Add tests** in strategies/ that verify identical behavior
3. **Build lab** importing from strategies/ (Phase 1)
4. **Update trade** to import from strategies/ instead of local (Phase 3, issue #12)
5. **Delete** local copies from trade only after trade's tests pass with the redirect

At no point does trade break. The local copies remain functional until trade is explicitly migrated.

### Testing the migration

Before Phase 3 issue #14 (slim controller) goes live:
1. Run the dashboard (live_dashboard.py) with the new Scanner (issue #12)
2. Run paper trading with the new controller
3. Compare decisions against the old controller's decisions for the same market data
4. Only then deploy to live

---

## Open Questions

1. **Separate repo vs. monorepo directory for strategies/?** Monorepo directory is simpler. Separate repo enables independent versioning and tighter access control. Recommendation: start as monorepo directory (`builds/strategies/`), extract to repo if/when we have multiple strategies or contributors.

2. **Predicted rates in backtest.** HL predicted rates are not stored by the collector. The strategy's `best_estimate_rate()` falls back to current rate when predicted is None. This is conservative and correct for backtest. If we want predicted rate data in backtest, add a `predicted_rate_1h` column to the ticks table and store it during collection (only HL provides it).

3. **Sub-hourly ticks.** The hold evaluator's dislocation model (ψ per minute) is meaningless with hourly data. If we want accurate hold evaluation in backtest, we'd need minute-level bid/ask data. This is a large data volume increase and not worth it for v1. The simplification (set live_crossing_exit=0 in backtest) is documented and acceptable.

4. **Multi-account in lab.** Not needed. One simulated account per run. Use multiple runs with different configs to compare scenarios.

5. **When to extract strategies/ to its own repo.** When we add a second strategy, or when the package stabilizes enough to warrant independent versioning. Not before.
