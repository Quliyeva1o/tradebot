# Technical Audit Report — Forex SMC Trading Framework ("tradebot")

Audited as a pre-deployment review of a system intended to eventually manage real capital.
Scope: full repository (~15,300 lines of Python across core/, market_structure/, smc/, strategy/,
backtest/, application/, research/, mt5/, tests/). Tests were executed, not just read.

---

## Executive Summary

**Overall score: 52 / 100**

**Would I deploy this to trade live money? NO.**

Why: the core SMC pipeline (swings → structure → OB/FVG/liquidity → strategy → backtest) is
genuinely well thought out in its *intent* — non-repainting swing confirmation, N→N+1 entry
sequencing, per-zone incremental mitigation tracking, circuit breakers for drawdown/daily loss.
That intent is real engineering skill. But the *execution* has a confirmed, test-verified state
propagation bug in the structure engine (MINOR→MAJOR swing upgrades never reach the trend/BOS
logic), a severe O(n² log n) hot path in liquidity detection that will make anything beyond a few
thousand bars unusably slow, a self-reported failing performance test already in the repo, an
entire parallel "Clean Architecture" application layer that is never wired to anything and is pure
dead weight, and a live-execution layer (MT5 connector, risk module) that is essentially a stub.
None of this is cosmetic — several items directly corrupt the market-structure state that every
downstream strategy decision depends on. A backtest run today over the bugs above would produce
numbers that do not reflect what the strategy logic is actually supposed to do.

This is a strong prototype with real domain understanding behind it. It is not a system ready to
risk capital, and in its current state some of its own tests fail, which should have blocked any
claim of "complete test suite" passing.

---

## Strengths

Genuinely well done, not damning with faint praise:

- **Bar-by-bar incremental architecture for market state** (`MarketStateBuilder.append_bar`) avoids
  the single most common backtesting sin: computing indicators/swings over the whole dataset and
  then indexing into it as if you "knew" the future. Swings are confirmed with a `right_bars` delay
  and revealed only at that delayed index — correct in principle.
- **Backtest engine's fill sequencing** (`backtest/engine.py`) generates a setup on candle *N* and
  only allows entry to trigger starting candle *N+1* — this specifically defeats same-bar lookahead
  bias, which is rare to get right in home-grown backtesters.
- **Conservative same-candle SL/TP conflict resolution** (assumes SL hit first when both are
  touched in one candle) is a defensible, non-optimistic choice.
- **`MitigationMonitor`** uses a real incremental cursor (`_last_checked`) per zone instead of
  rescanning full history — correct instinct, just undermined elsewhere (see Performance).
- **Frozen dataclasses throughout the domain layer**, `Protocol`-based ports, and consistent
  `X | None` typing show real familiarity with modern Python and an intent toward immutability.
- **Circuit breakers**: max drawdown, max daily loss, negative-balance protection are present in
  the backtest loop, which is more than most retail backtesters bother with.
- **Chronology/duplicate/graph-link validation** in both `SwingDetector` and
  `MarketStructureEngine` (`_validate_graph`, `DuplicateSwingIDError`, etc.) shows defensive,
  invariant-driven thinking that is rare in trading codebases.

---

## Weaknesses (ranked by severity)

### Critical
1. **Swing MINOR→MAJOR upgrade never propagates to the structure engine.**
   `SwingDetector.detect_incremental` computes an `upgraded_swing` and even mutates its
   classification in place, but `MarketStateBuilder.append_bar` only ever reads
   `result.new_swing` — `result.upgraded_swing` is silently discarded. `MarketStructureEngine`
   has a `handle_upgrade()` method built for exactly this purpose, and it is never called from
   anywhere in the codebase. Net effect: `last_major_high`/`last_major_low` can go stale forever
   once a swing is upgraded after the fact, which is the pointer `check_structural_break` uses to
   decide BOS/CHoCH. **This is not theoretical — `tests/test_market_state_builder.py::test_market_state_builder_swing_upgrades`
   already asserts the correct behavior and currently fails** (verified by running pytest).
2. **Incremental vs batch swing filtering diverge.** In `detect_batch`, when a new swing is
   "better" than the previous same-type swing, the older one is *replaced* (`filtered[-1] = item`),
   preserving strict alternation of HIGH/LOW in the graph. In `detect_incremental`, the equivalent
   branch just `pass`es — it keeps the candidate but never removes the earlier one from the graph
   (`SwingGraph.add_swing` only appends, never replaces). Real-time/backtest processing (which is
   exclusively incremental) can therefore produce two consecutive swings of the same type, breaking
   the alternating-swing assumption that structure/BOS/CHoCH/liquidity clustering logic implicitly
   relies on. Batch and incremental detection are not equivalent, and only the buggier one is what
   actually runs in the backtester.
3. **Liquidity pool detection recomputes from scratch on every single bar.**
   `SMCPipeline.update` calls `LiquidityDetector.find_liquidity_pools(market_state.swing_graph)`
   unconditionally every bar. That method: copies the entire node list (`SwingGraph.nodes` returns
   `list(self._nodes)`), sorts *all* highs and *all* lows by price, and for every cluster does a
   full reverse scan over *all* nodes to check for a sweep. There is no incremental cursor here
   (unlike `MitigationMonitor`, which does this correctly). Cost is roughly O(n log n) per bar,
   O(n² log n) over a full backtest. On anything beyond a few thousand bars of M15/H1 data this
   will dominate runtime; on M1 data over months, it is not practically usable.
4. **The repo's own test suite does not currently pass.** Running `pytest tests/` produces 3
   failures out of 156 tests: the swing-upgrade propagation bug above, a related
   `test_market_state_builder_bos_and_choch` failure, and `test_large_dataset_performance`
   (100k-candle stress test expected <1s, actual 1.09s). A system with a documented "complete test
   suite, Ruff, MyPy, Pytest" claim should not ship with red tests — this is either a quality-gate
   process failure or the tests were never run post-refactor.

### High
5. **Entire `application/` Clean Architecture layer is dead code.** `TradingCoordinator`, all
   inbound/outbound ports (`IDataFeedPort`, `IExecutionPort`, `IStateRepositoryPort`,
   `INotificationPort`), and the DTOs are referenced only by each other and by a single test file
   (`test_application_layer.py`). Neither `run_backtest.py` nor `run_research.py` — the two actual
   entry points — import any of it except `MarketStateBuilder`. There is also a *second*, parallel
   set of interfaces in `core/interfaces.py` (`IDataFeed`, `IExecutionProvider`, `IStrategy`) used
   only by `strategy/base_strategy.py`. Two incompatible port/adapter systems exist side by side,
   neither is the one actually driving the system, and this is exactly the kind of "architecture
   theater" that makes future maintainers guess which abstraction is real.
6. **Live-trading layer is a stub, not an implementation.** `mt5/connector.py` (72 lines) only
   logs in/out of the terminal. There is no order placement, no position reconciliation, no error
   recovery/retry, no `IExecutionProvider` implementation for MT5 at all. `risk/position_size.py`
   and `risk/risk_reward.py` both `raise NotImplementedError` unconditionally and are unused
   anywhere (a *third*, separate position-sizing class, `SimplePositionSizer`, lives in
   `backtest/engine.py` and is the only one actually used). "MT5 Live Engine" on the roadmap is
   accurate framing — there is currently no live engine, just a login helper.
7. **Backtest spread cost is possibly double-counted.** Entry price adds a full `spread` (plus
   slippage), and the exit price on SL/TP/expiry *also* subtracts a full `spread`. If
   `BacktestConfig.spread` is meant to represent the broker's quoted bid/ask spread (as its
   undocumented naming implies), this charges a full spread on both legs of the round trip — i.e.
   2× the real transaction cost per trade. There's no docstring or test clarifying whether `spread`
   is meant as a half-spread, so this cannot be confirmed as intentional; either way it needs to be
   pinned down and unit-tested explicitly, because it materially changes every backtest P&L.
8. **Pending-order fill window is exactly one bar.** A generated `TradeSetup` is checked for fill
   only on the *next* candle; if untouched, it's discarded outright (`pending_setup = None`
   unconditionally after the check). Real SMC entries (retracement into an order block/FVG) often
   take many bars to mitigate. This isn't wrong, but it silently prunes a large share of otherwise
   valid setups and will materially understate opportunity count vs. a live system that would keep
   a limit order resting for longer — this should be a configurable expiry, not implicit.

### Medium
9. **Strategy signal quality: stale BOS gating.** `BullishContinuationStrategy`/`BearishContinuationStrategy`
   use `breaks_history[-1]` — the single most recent break ever recorded — with no recency/age
   check relative to the current bar. Only the *displacement* rule (Rule 7) has an explicit
   lookback window (`lookback_bars`, default 20). A structure break from hundreds of bars ago can
   still validate a "continuation" setup today as long as an order block/FVG/liquidity condition
   happens to realign later — conflating unrelated market phases.
10. **OB/FVG selection picks the first match, not the best.** Both continuation strategies iterate
    `market_state.smc_state.order_blocks`/`fair_value_gaps` and take the first list entry satisfying
    the condition, `break`ing immediately — not the nearest, most recent, or highest-quality one.
    List order is whatever insertion order happened to be, which is largely chronological (oldest
    first), so the *oldest* qualifying OB/FVG tends to win, which is usually the opposite of what a
    discretionary SMC trader would pick.
11. **OB and FVG confluence are not required to be spatially/temporally related.** The strategy
    requires *some* unmitigated bullish OB containing price *and*, independently, *some* unmitigated
    bullish FVG "nearby" — with no check that they belong to the same displacement leg or overlap
    with each other. Two unrelated zones from different market phases can jointly satisfy the rule.
12. **`TradeSetup.timestamp` uses `datetime.now()`, not the bar's timestamp.** In both continuation
    strategies, the setup's own timestamp is wall-clock time at generation, not
    `market_state.get_latest_bar().timestamp`. In a backtest this means every setup gets a
    timestamp clustered around "whenever the script happened to run," not the historical moment it
    fired. (Trade *entry_time* in `BacktestEngine` correctly uses `candle.timestamp` — this only
    corrupts the setup object itself — but that object's `timestamp` field is meaningless for
    audit trails, replay, and non-deterministic between runs.)
13. **Zone lists (OBs/FVGs) never get pruned/evicted.** Even fully mitigated zones stay in
    `smc_state.order_blocks`/`fair_value_gaps` forever and are re-iterated every bar by
    `MitigationMonitor` (whose per-zone search is incremental, but the *list traversal* itself is
    not bounded). Combined with #3, per-bar cost trends upward over the life of a long backtest.
14. **`Swing` objects are mutated in place** (`swing.classification = SwingClassification.MAJOR`,
    strength assignment, etc.) even though the rest of the domain model leans on frozen dataclasses.
    `Swing` itself must therefore be a plain mutable dataclass — an inconsistency with the stated
    "Immutable Models" architectural goal, and a source of subtle aliasing bugs (the same `Swing`
    object is referenced from `SwingGraph`, `MarketStructure.last_major_high`, historical
    `MarketStructure` snapshots, etc. — mutating it retroactively changes what old, supposedly
    immutable, historical state snapshots report).
15. **`market_state.smc_state.order_blocks`/`fair_value_gaps` selection in strategies is O(n) linear
    scan of an unbounded, ever-growing list, every bar, per strategy** — same root cause as #13
    but on the strategy side too.

### Low
16. **Root-level clutter**: `debug_market_state_builder.py`, `debug_swing_graph.py`,
    `manual_test.py`, and duplicate `test_market_state.py` / `test_signal_diagnostics.py` /
    `test_swing_detector.py` sit at the repository root, outside `tests/` (which is the only path
    configured in `pyproject.toml`'s `testpaths`). These look like ad-hoc debugging scripts left
    behind; they're never executed by CI/pytest and are dead weight that will confuse newcomers
    about which `test_swing_detector.py` is authoritative (there are two, with different APIs —
    one importing the raw `SwingDetector`, the other importing
    `DataFrameSwingDetectorAdapter as SwingDetector`).
17. **`AccountInfo`, `Order`, `Position` frozen dataclasses default `timestamp` via
    `field(default_factory=datetime.now)`** — naive (non-timezone-aware) local time, mixed with
    otherwise-careful bar timestamp handling elsewhere. Will bite hard the moment this trades a
    broker in a different timezone or across DST boundaries.
18. **Frozen dataclasses with mutable `dict`/`list` default fields** (`ConfluenceMetadata.metrics`,
    `MarketStructure.internal_structure`/`external_structure`) are not really immutable — `frozen=True`
    prevents reassigning the attribute, not mutating the dict/list it points to. Minor, but
    undermines the "immutable models" claim being made in the architecture pitch.
19. Several `except ValueError` / broad exception patterns swallow errors silently (e.g. premium/
    discount calculation failure in `SMCPipeline.update` falls back to `None` with no logging) —
    fine for expected edge cases, risky if it masks a real bug in production.

---

## Bugs (concrete, file/function level)

| # | File / Function | Bug | Impact | Fix |
|---|---|---|---|---|
| 1 | `application/services/market_state_builder.py :: append_bar` | `result.upgraded_swing` from `swing_detector.detect_incremental` is computed but never used; `structure_engine.handle_upgrade()` is never called | `last_major_high`/`last_major_low` never reflect swings upgraded from MINOR→MAJOR after the fact; BOS/CHoCH detection can use stale major levels indefinitely | Call `self.structure_engine.handle_upgrade(result.upgraded_swing)` (or equivalent) whenever `upgraded_swing` is not None, before/after processing `new_swing` |
| 2 | `market_structure/swing_detector.py :: detect_incremental` (Alternate/Duplicate filter branch) | When a same-type candidate is "better" than the last graph node, code does `pass` (silently keeps both) instead of replacing the previous node as `detect_batch` does | Graph can contain consecutive same-type swings in live/backtest mode, violating the alternation invariant enforced by `MarketStructureEngine._validate_graph` and assumed by BOS/CHoCH/liquidity logic | Replace the previous node (or expose a `replace_last_node` on `SwingGraph`) to match `detect_batch` semantics exactly |
| 3 | `smc/liquidity.py :: find_liquidity_pools`, called from `smc/pipeline.py :: SMCPipeline.update` | Full O(n log n) recompute of all liquidity pools from the entire swing graph on every bar, no incremental cursor | Backtest runtime scales O(n² log n) with bar count; will be prohibitively slow for large histories (months of M1/M5 data) | Track processed swing index high-water-mark like `MitigationMonitor._last_checked`; only re-cluster/re-check sweeps for new swings since last update |
| 4 | `backtest/engine.py :: run` (entry/exit price calc) | `spread` is added at entry and subtracted again at exit (full spread both legs) | Likely double-charges the true bid/ask cost, materially understating backtested profitability | Clarify semantics (half-spread vs full-spread) in `BacktestConfig` docstring, add a unit test asserting round-trip cost == one spread width, adjust formula if confirmed wrong |
| 5 | `strategy/continuation.py :: Bullish/BearishContinuationStrategy.evaluate` | `TradeSetup.timestamp = datetime.now()` instead of the current bar's timestamp | Non-deterministic, meaningless timestamps on generated setups during backtests; breaks reproducibility/auditability | Use `market_state.get_latest_bar().timestamp` |
| 6 | `risk/position_size.py`, `risk/risk_reward.py` | Both raise `NotImplementedError` unconditionally, unused anywhere | Dead/broken modules masquerading as implemented risk management; misleading to anyone auditing "risk management" coverage | Either implement and wire in, or delete — don't ship stubs that look real |
| 7 | `tests/test_swing_detector.py :: test_large_dataset_performance` | Currently failing (1.09s vs <1.0s budget) against `DataFrameSwingDetectorAdapter` | Confirms real perf regression already present, undermines "complete test suite" claim | Profile the DataFrame→Bar adapter conversion path specifically (likely `iterrows`-style overhead), not just the core algorithm |
| 8 | `application/ports/*`, `application/services/trading_coordinator.py`, `core/interfaces.py` | Two parallel, unused abstraction layers | Dead code, confusing to maintainers, inflates the "Clean Architecture" surface without any of it being load-bearing | Delete one, wire the other into `run_backtest.py`/`run_research.py`, or delete both until there's a real consumer |

---

## Architecture Review

| Category | Score /10 | Notes |
|---|---|---|
| Clean Architecture / DDD | 4 | Layering exists on paper (`core`, `application`, `market_structure`, `smc`, `strategy`, `backtest`) but the flagship "application" port/adapter layer is entirely unused by the real entry points. Two competing interface systems (`core/interfaces.py` vs `application/ports/*`) is a textbook architecture smell — pick one. |
| SOLID | 6 | Reasonable single-responsibility split per module (swing detection, structure, each SMC concept isolated). Dependency inversion is undermined by the dead ports (interfaces exist, nothing depends on them where it matters). Open/closed is decent — new strategies can be added via `TradeSetupStrategy`. |
| Dependency Direction | 5 | Domain (`market_structure`, `smc`) is mostly clean of infra, but `MarketStructure`/`SwingGraph` importing `smc.*` types under `TYPE_CHECKING` (in `structure_models.py`) shows the domain layer already knows about a "higher" concept layer — mild inversion of the intended dependency direction. |
| Layer Separation | 5 | The intended separation exists in folder structure; enforcement is weak since the app layer isn't actually used. |
| Circular Dependencies | 7 | None observed directly; `TYPE_CHECKING`-guarded imports in `structure_models.py` suggest the author already had to work around a would-be cycle, which is itself a signal that the layering isn't quite right. |
| Domain Purity | 6 | Core swing/structure/SMC logic is genuinely pandas-free and side-effect-free at the algorithm level — good. Undermined by in-place mutation of "frozen-adjacent" `Swing` objects and mutable dict fields inside frozen dataclasses. |
| Infrastructure Leakage | 6 | `MarketStructureEngine`, `SwingDetector`, SMC detectors don't leak infra concerns. `DataFrameSwingDetectorAdapter` correctly isolates pandas at the edge — good pattern where it's actually followed. |
| Coupling | 5 | `SMCPipeline` tightly orchestrates 6 detectors with hand-rolled offset arithmetic (`fvg.start_index + offset` etc.) — works, but fragile; any change to window sizes elsewhere silently breaks index math. |
| Cohesion | 7 | Individual modules (bos.py-equivalent logic inside structure_engine, fvg.py, order_block.py) are cohesive and single-purpose. |
| Extensibility | 5 | Adding a new SMC concept means touching `SMCPipeline.update`, `MarketState`/`SMCState`, and any consuming strategy — no plugin/registry mechanism, moderate friction. |
| Testability | 6 | Domain logic is unit-testable in isolation (no framework/DB deps), which is good; but several tests currently fail, and the dead application layer has tests validating behavior nobody depends on. |

**Architecture average: ~5.6/10** — the intent is good, the follow-through has one layer that's real and one that's decorative.

---

## Domain Logic Review

- **Swing Detection**: Correct non-repainting design (confirmation delayed by `right_bars`).
  Batch vs incremental filtering divergence (Bug #2) is the standout correctness issue — this is
  the foundation everything else builds on, so it deserves top priority.
- **Market Structure / BOS / CHoCH**: `check_structural_break` logic (break past last major swing,
  CHoCH if it's counter to current trend, else BOS) is a reasonable, standard formulation. The
  swing-upgrade propagation bug (#1) is the critical flaw here — a MINOR high that later qualifies
  as MAJOR should immediately become the new reference level for BOS/CHoCH, and currently doesn't.
- **Liquidity**: Clustering by price tolerance and sweep detection via reverse scan is logically
  sound; the implementation is just not incremental (perf issue, not correctness).
- **Order Blocks**: The "last opposite candle before the break" heuristic with a volume filter is a
  standard, defensible SMC formulation. Reasonable fallback when no opposite candle is found.
  Weakness: `anchor_mode="swing"` falls back to `brk.broken_swing.index` directly when the
  timestamp isn't found in `ts_to_idx` — if `bars` passed in is a different slice than what
  produced the swing, this index could point to the wrong candle silently (no bounds cross-check
  against the swing's own timestamp).
- **FVG**: Correctly scoped to the latest 3-bar window per update — appropriately incremental.
- **Premium/Discount**: Simple midpoint zone (0.5 of major high/low range), standard.
- **Strategy Evaluation**: Directionally correct rule chain, but stale-break gating (#9),
  first-match-not-best-match zone selection (#10), and unrelated OB/FVG confluence (#11) are real
  weaknesses in signal quality, not just style nits.
- **Trade Validation**: R:R gate is present and correctly rejects non-positive/insufficient
  risk-reward before emitting a setup — good practice.
- **Backtest Simulation**: Execution sequencing avoids same-bar lookahead (good); spread
  double-charging (#7) and the one-bar-only pending fill window (#8) are the two things most likely
  to distort reported profitability versus reality.

---

## Backtest Quality

**Can the results be trusted? Not without fixing #1–#4 above and clarifying #7.**

- **Look-ahead bias**: Not present in the *entry sequencing* mechanism itself (N-generate,
  N+1-execute is correct). It *is* present transitively through Bug #1 and #2 — if the market
  structure state feeding the strategy is wrong (stale major levels, non-alternating swings), the
  backtest is faithfully simulating a broken signal generator, not the intended strategy.
- **Survivorship bias**: N/A at this scope — single-symbol backtests, no universe selection.
- **Optimistic fills**: Not really — spread+slippage applied on both entry and exit is if anything
  *pessimistic* (possibly overly so, see #7).
- **Slippage/commission handling**: Present and configurable (`slippage`, `commission`,
  `commission_per_lot`), applied consistently.
- **TP/SL logic**: Reasonable, with a documented conservative tie-break rule for same-candle
  SL+TP conflicts. This is a real, inherent limitation of OHLC-bar backtesting (no intrabar path is
  knowable without tick data) rather than a bug — but it should be called out prominently in any
  report generated from this engine, since it can materially change win rate on volatile
  instruments/timeframes.
- **Pending order / expiry**: One-bar-only fill window (#8) is a real behavioral gap versus how
  SMC retracement entries are meant to work.
- **Position sizing**: Risk-based, correctly guards against zero/near-zero stop distance. No
  margin/leverage constraint modeled at all — an FX account's actual max position size given
  leverage is never checked, only pure risk-based sizing. Fine for a backtest studying strategy
  edge, not fine if this number ever gets used to size a live order.

---

## Strategy Quality

Only two strategies exist (`BullishContinuationStrategy`, `BearishContinuationStrategy`), both
structurally identical (mirrored logic, good consistency). Issues:

- **Duplicate trades**: Guarded reasonably well via `_proposed_keys = {(ob.id, break.id)}` and
  `reset()` on engine reset — this specific mechanism looks correct.
- **Invalid entries**: R:R gate correctly filters, but stale-break gating (#9) can produce entries
  that are logically disconnected from the "continuation" narrative implied by their `trigger_reason`
  string.
- **Over/under-filtering**: Requiring OB + FVG + liquidity sweep + displacement + trend + P/D zone
  simultaneously is a lot of independent AND-conditions with no relatedness requirement between them
  (#11) — this can either over-filter (rarely fires, because independent conditions rarely all
  align) or, when it does fire, produce setups where the "confluence" is coincidental rather than
  structural. Both are bad for different reasons; the fix is the same (require spatial/temporal
  relatedness between the OB/FVG/break, not independent existence).
- **Conflicting signals**: Only one strategy engine result (`setups[0]`) is ever acted on per bar
  by the backtest engine — if both bullish and bearish strategies somehow fired the same bar (they
  shouldn't, given opposite trend gates, but nothing enforces mutual exclusivity at the engine
  level), whichever is first in the list silently wins with no conflict-resolution logic.

---

## Code Quality

- **Naming**: Generally clear and domain-appropriate (`OBDirection`, `SwingClassification`,
  `StructureBreak`). No complaints here.
- **Duplication**: `Bullish`/`BearishContinuationStrategy` are ~90% mirror images of each other —
  understandable for symmetry, but a shared base class extracting the common skeleton (accept a
  `direction`/config object, delegate the direction-specific bits) would cut ~150 lines and halve
  the maintenance surface. Three separate, incompatible position-sizing implementations
  (`SimplePositionSizer`, `risk.position_size.PositionSizer`, referenced nowhere consistently) is
  duplication that actively misleads.
- **Giant methods**: `BacktestEngine.run` (~320 lines) and `MarketStructureEngine.update` do a lot
  in one function body; not unreasonable for a state-machine step, but both would benefit from
  being split into named private helpers (`_check_exits`, `_check_entries`, `_generate_setups`) for
  testability and readability.
- **Dead code**: The entire `application/` port/adapter/coordinator subsystem, `core/interfaces.py`,
  `risk/position_size.py`, `risk/risk_reward.py`, and the root-level `debug_*.py`/`manual_test.py`/
  duplicate `test_*.py` files.
- **Magic numbers**: `window_size = self.displacement_detector.atr_period + 100` in
  `SMCPipeline.update` — the `+100` is unexplained; should be a named constant with a comment on
  why 100 bars of buffer is sufficient.
- **Hidden coupling**: SMC pipeline's manual index-offset arithmetic (`fvg.start_index + offset`)
  is exactly the kind of thing that silently breaks if window sizes change elsewhere without a
  corresponding update here — no test currently pins this contract explicitly enough to catch a
  regression.

---

## Performance Review

Primary bottleneck, by a wide margin: **`LiquidityDetector.find_liquidity_pools`** being called
unconditionally every bar with no incremental state (Bug #3). Everything else in the pipeline
(`FVGDetector` on last 3 bars, `DisplacementDetector` on a fixed window, `MitigationMonitor` with
its `_last_checked` cursor) was clearly designed with per-bar cost in mind — liquidity detection
is the one place that wasn't, and it's also the most expensive operation (sort + cluster + reverse
scan over the *entire* swing history) of the bunch. This single fix would likely be the highest-ROI
performance change in the codebase.

Secondary: unbounded growth of `order_blocks`/`fair_value_gaps` lists (#13) means even the
"incremental" mitigation check's constant per-zone cost is multiplied by an ever-growing zone count
every bar. `SwingGraph.nodes` returning a fresh `list()` copy on every property access (called
repeatedly per bar, e.g. `graph.nodes[-5:]` in `detect_incremental`) is a smaller but real
allocation cost that adds up over a long backtest — should be sliced directly against `self._nodes`
or exposed via an index-based accessor instead of a full copy each time.

The already-failing `test_large_dataset_performance` (100k bars, 1.09s vs a <1.0s budget) is direct,
measured evidence that performance has already regressed past the project's own stated bar — worth
investigating *before* adding the liquidity fix, since the two may compound.

---

## Trading Logic Review

The individual SMC concepts (swing, structure, OB, FVG, liquidity, premium/discount) are each
reasonable, textbook implementations of their respective concept in isolation. The weakness is at
the *composition* level:

- No requirement that the OB, FVG, and liquidity sweep referenced by a single setup be part of the
  same displacement leg — a real discretionary SMC trader reads these as one coherent story (sweep
  → displacement → OB/FVG left behind by that same displacement → retracement into it), not as
  four independently-satisfied checkboxes that can come from different weeks.
- No recency constraint on the structure break itself, only on displacement.
- First-match-not-best-match zone selection likely picks the *least* relevant qualifying zone in
  many real market conditions (oldest in list, not nearest to price/most recent).
- Only continuation setups exist — no reversal/CHoCH-based entries, no liquidity-sweep-only entries,
  no breaker-block strategy despite `smc/breaker.py` existing in the codebase (built but apparently
  never wired into a strategy).

None of this makes the SMC concepts "wrong" — it makes the *strategy layer built on top of them*
weaker confluence than the individual building blocks would support if properly related to each
other.

---

## Test Quality Review

**Real confidence level: moderate on individual algorithms, low on integration.**

156 test functions across 25 files is a genuinely substantial suite for a project this size, and
several files (`test_swing_detector.py`, `test_structure_engine.py`, `test_strategy_engine.py`)
show real edge-case thinking (equal highs/lows, insufficient history, chronology violations,
duplicate timestamps). That's better than most retail trading repos.

That said:
- **3 of 156 tests currently fail** on a clean checkout, including one that directly encodes the
  correct behavior for Bug #1 — the test suite *did* catch this bug, it just wasn't acted on before
  calling it "complete."
- **Application layer tests validate dead code** — `test_application_layer.py` gives false
  confidence that the port/adapter system works, when nothing in the running system depends on it.
- **No test asserts the batch-vs-incremental swing detection equivalence** (Bug #2) directly — this
  is exactly the kind of property-based/differential test ("run both paths over the same data,
  assert identical graphs") that would have caught it immediately, and its absence is a real gap.
- **No performance regression test for `find_liquidity_pools`** specifically (only the swing
  detector has a stress test), so Bug #3's cost wouldn't be caught by CI at all.
- **No test exercises `BacktestEngine.run` against a scenario asserting the exact spread cost
  applied per round trip** — Bug #7 would be trivial to pin down with one assertion and currently
  has none.

Estimated real coverage of *intended* behavior (not just line coverage): roughly 60–70% for the
core swing/structure/SMC algorithms in isolation, considerably lower (~30–40%) for the
integration/state-propagation seams between them, which is exactly where the critical bugs live.

---

## Technical Debt

- Two parallel, unused architecture layers (`application/ports/*` + `core/interfaces.py`).
- Three incompatible position-sizing implementations, two of them unimplemented stubs.
- Root-level debug/manual-test scripts duplicating and diverging from the real `tests/` suite.
- Unbounded zone-list growth with no archival/pruning strategy.
- Hand-rolled index-offset arithmetic in `SMCPipeline` with no contract test pinning window sizes.
- MT5 execution layer that exists in name only.
- `Swing` mutability inconsistent with the rest of the "immutable domain model" architecture claim.
- Currently-failing tests left unresolved in the shipped state.

---

## Refactoring Priority Roadmap

**Priority 1 (correctness, blocks everything else):**
1. Fix swing-upgrade propagation into `MarketStructureEngine` (Bug #1) — this corrupts the
   single most load-bearing piece of state in the system.
2. Fix batch/incremental swing-filter divergence (Bug #2) and add the differential test that
   would have caught it.
3. Resolve the two failing tests and treat "all tests green" as a hard gate going forward.

**Priority 2 (backtest trustworthiness):**
4. Pin down and fix/confirm the spread double-charging (Bug #7) with an explicit unit test.
5. Fix `TradeSetup.timestamp` to use bar time, not wall-clock time (Bug #5).
6. Make pending-order expiry configurable instead of implicitly one bar (#8).

**Priority 3 (performance, needed before any serious-length backtest):**
7. Make `find_liquidity_pools` incremental (Bug #3) — highest-ROI perf fix in the repo.
8. Prune/archive fully-mitigated zones instead of growing lists forever (#13).
9. Avoid full-list copies in `SwingGraph.nodes` on every access.

**Priority 4 (strategy quality):**
10. Add recency gating to the structure-break rule (#9).
11. Select nearest/most-recent OB/FVG, not first-in-list (#10).
12. Require OB/FVG/break to be part of the same displacement leg (#11).

**Priority 5 (architecture hygiene):**
13. Delete or actually wire up the `application/` port layer and `core/interfaces.py` — pick one.
14. Delete the two `NotImplementedError` risk stubs, or implement and wire them.
15. Remove root-level debug scripts and duplicate test files.

**Priority 6 (before any live trading whatsoever):**
16. Build an actual `IExecutionProvider` implementation for MT5 (order placement, position sync,
    reconnection/retry, error handling) — the current connector is a login helper, not an execution
    engine.
17. Add margin/leverage-aware position sizing, not just risk-based sizing.

---

## Production Readiness

```
Architecture:      45%
Trading Logic:     55%
Testing:           60%
Performance:       35%   (severe hot path, will not scale past a modest backtest length)
Live Trading:      15%   (execution layer does not functionally exist yet)
Maintainability:   55%   (dead layers and duplicated abstractions actively hurt this)
Overall:           52%
```

---

## If this were my own trading system managing real capital, what would I change before trusting it with live money?

I would not touch a live account until, at minimum, the swing-upgrade propagation bug and the
batch/incremental swing-detection divergence were fixed and covered by a differential test — those
two corrupt the market-structure state that every single downstream decision is built on, and I
would not trust a single backtest result generated before they're fixed, including any numbers this
system has already produced. I would then pin down the spread-cost question with an explicit test,
because right now I genuinely don't know if this backtest is charging trading costs once or twice
per round trip, and that alone can be the difference between a "profitable" strategy and a losing
one on paper. I'd fix the liquidity detector's per-bar recompute before running anything longer than
a toy dataset — as it stands, a serious multi-year M15 backtest is not really feasible. Only after
all of that would I even start looking at the strategy-quality issues (stale break gating, unrelated
OB/FVG confluence), because right now I can't tell how much of the strategy's apparent edge or lack
thereof is real signal versus an artifact of the bugs above. And I would build a real MT5 execution
adapter — with reconnection, position reconciliation on restart, and explicit error handling —
before ever pointing this at a funded account, because right now there is no live execution layer to
speak of, just a login call. This is a well-intentioned, partially-realized system with real domain
knowledge behind it; it is not yet a system I would let touch money.
