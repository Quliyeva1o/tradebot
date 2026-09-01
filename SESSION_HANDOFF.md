# Session Handoff — 2026-08-27

Continuation notes for picking this project up on another machine. Covers
what changed, what is trustworthy, what is not, and what to do next.

> **2026-08-31/09-01 update:** a new strategy (XAUUSD 09:30 Opening-Range
> Breakout + Liquidity-Sweep) was built, backtested, ported to a live class,
> and Paper-smoke-tested. See **[XAUUSD_ORB_SESSION_HANDOFF.md](XAUUSD_ORB_SESSION_HANDOFF.md)**
> for the full writeup, open findings, and next-session TODO list.
>
> **2026-09-01 update:** the live class was ported to the M15 configuration
> (§5 items 1, 2, 4, 5, 6 of that doc are now done — see its "M15 port"
> section for the full writeup, including 6 individually root-caused
> fidelity-gap trades and an independent re-validation on a different
> account/broker's data, PF 2.22 idealized / PF 1.33 realistic-fill).
> Still open: the full walk-forward/Monte Carlo/regime battery for M15
> (item 3), and wiring this into a Scheduled Task (item 6's second half,
> deliberately left for explicit user go-ahead).
>
> **2026-09-01 update (live bot composition changed, user-approved):**
> based on the M15 XAUUSD ORB battery above plus a re-check of First FVG's
> already-documented 2026-08-31 downgrade (see
> [ADVANCED_VALIDATION_REPORT.md](ADVANCED_VALIDATION_REPORT.md)) and a new
> SR+ORB portfolio-level risk test, the user approved a live-composition
> change. `FirstFVG15m_NAS100_Demo`/`_Paper` are now **disabled** (5
> independent tests found no real edge net of live spread -- see
> [STRATEGY_RANKING.md](STRATEGY_RANKING.md)). `SRBias_NAS100_Demo` now
> also runs `--require-ranging-regime` (previously Paper-only) at 0.2%
> risk (was 0.25%). `XauusdOrb_Paper` and (a later same-session
> follow-up) `XauusdOrb_Demo` are both running, at **2% risk** (the user's
> explicit choice after seeing the risk-pct sweep -- 2% is the aggressive,
> worst-case-64.3%-drawdown end, NOT the 0.25% conservative end originally
> recommended; reasoning: this is Demo/Paper virtual money, so the
> tail-risk argument against 2% for REAL capital doesn't carry the same
> weight here). A real bug was found and fixed while wiring up Demo:
> `run_live_xauusd_orb.py`'s `STRATEGY_TAG` was too long to survive MT5's
> comment-truncation, so it could never recognize its own real positions --
> see `project-live-bot-composition` memory for the full writeup. Also
> found and deliberately ruled out: running the SAME strategy at two risk
> levels as genuinely separate simultaneous bots (blocked by the
> same-symbol position-exclusivity logic); the risk-pct comparison is done
> arithmetically from one real trade log instead. The table in §1 below is
> now stale; treat this callout as the current source of truth until it's
> rewritten.

---

## 1. Current live state

Three bots run in parallel on ONE MT5 demo account (`ForexTimeFXTM-Demo02`,
login 67660753, $100k virtual), driven by Windows Task Scheduler every 2
minutes:

| Task | Script | Symbol / TF | Risk |
|---|---|---|---|
| `MidnightFVG_*` | `run_live_midnight_fvg.py` | NAS100 M1 | 0.05% |
| `SRBias_XAUUSD_*` | `run_live_sr_bias.py` | XAUUSD M15 | 0.20% |
| `SRBias_NAS100_*` | `run_live_sr_bias.py` | NAS100 M30 | 0.25% |

Each has a `_Demo` (real demo orders) and `_Paper` (PaperBroker, no orders)
variant. Both are gated by the two-layer demo-account rail
(`.env` `MT5_ACCOUNT_TYPE=demo` **and** MT5's own `trade_mode`).

> **Naming note:** the "Midnight FVG" strategy is referred to as **First
> FVG** in conversation. Same thing; the filenames still say `midnight_fvg`.

---

## 2. Bugs found and FIXED this session

All five were verified against real data, and every fix has a regression
test whose teeth were checked by deliberately reverting the fix and
confirming the test fails.

### 2.1 Lookahead in the HTF Daily Bias — CRITICAL
**Files:** `scripts/order_flow_bias_backtest.py`, `scripts/po3_backtest.py`

`resample(label="left", closed="left")` labels the 1H bar covering
`[09:00, 10:00)` as `09:00`, but its high/low/close are only known at 09:59.
Forward-filling the bias from that label let a **09:05 execution bar trade on
09:59 information**. 24.2% of 5m bars carried a wrong bias.

Measured impact — the strategy's *entire* apparent edge was the leak:

| | With lookahead | Without (honest) |
|---|---|---|
| Order Flow XAUUSD 5m | PF 1.63, +76.6R | **PF 0.99, −1.4R** |
| Order Flow NAS100 15m | PF 1.70, +42.7R | **PF 0.94, −5.1R** |

**Fix:** `bias_1h.shift(1, freq="1h")` before reindexing, so only CLOSED 1H
bars are ever used. **Never remove this shift.**
**Test:** `tests/test_backtest_lookahead.py` (perturbs the future, asserts no
past bias value moves). Note: two earlier formulations of this test were
*vacuous* and are documented in that file so they are not retried.

### 2.2 Same-bar SL missed in the First FVG backtest — CRITICAL
**File:** `scripts/backtest_midnight_fvg_live_class.py`

Entry is a mid-bar touch at the FVG edge, but SL/TP scanning started at the
NEXT bar — so a stop-out inside the entry bar itself was missed. Caught by
the user manually checking 2026-08-25 (recorded +2.5R; actually −1.0R, the
entry bar's low 29100.1 was already below the 29102.3 stop).
**Fix:** `simulate_entry_bar_then_outcome()` checks the entry bar first.
Impact: 613 trades, PF 1.21 (was overstated as 1.22 / higher net R).

### 2.3 Daily-resolved cache poisoned by rejected orders — CRITICAL
**File:** `run_live_midnight_fvg.py`

The cache was written from `strategy._trade_taken`, which is set when a setup
is *proposed*, before the broker fill / kill-switch gate. A rejected order or
kill-switch block therefore marked the day "resolved", and the cache-hit path
returned *before* the open-position check — starving the rest of the NY
session of both retries and position management.
**Fix:** `_evaluate_for_new_trade()` now returns True only on an actual FILL;
the cache keys off that, and the cache-hit branch still checks positions.
**Test:** `tests/test_run_live_midnight_fvg.py`.

### 2.4 Multi-bar gaps skipped SL/TP checks — HIGH
**Files:** `run_live_midnight_fvg.py`, `run_live_sr_bias.py`

`_manage_open_trade()` only checked `bars[-1]`. With a 2-minute poll and M1
bars — or after a Task Scheduler delay / PC sleep — several bars close
between invocations, so an SL/TP touch on an intermediate bar was never seen.
Harmless live (MT5 enforces the real SL/TP), **but PaperBroker has no
broker-side SL/TP and relies entirely on this check**.
**Fix:** iterate every bar closed since `position.timestamp`.

### 2.5 Position ownership between bots — CRITICAL
**Files:** `execution/models.py`, `execution/mt5_broker.py`,
`execution/paper_broker.py`, both `run_live_*.py`

Two bots trade NAS100 (First FVG M1, SR+Bias M30) on one account, but both
filtered positions by `p.symbol` only and `get_open_positions()` never read
MT5's `pos.comment` — so ownership was unknowable. Consequences: mutual
blocking; each bot could close the other's position; and if both opened in
the same window, BOTH bailed out as "ambiguous", leaving **neither** managed.
**Fix:** `Position.comment` added (both brokers populate it with the opening
`setup_id`); each runner matches its own `STRATEGY_TAG` prefix. A position
with an empty/unknown comment is **never** treated as ours (so a hand-opened
trade can't be closed by a bot). Verified to survive MT5's 29-char comment
truncation.
**Test:** `tests/test_prod_safety_guards.py`.

### 2.6 No margin ceiling in the live order path — CRITICAL
**File:** `execution/trade_manager.py`

`BacktestEngine` has always had `_margin_ok()`; the live path had none. Risk
sizing divides by stop distance, so a tight stop yields a huge lot size.

Measured with the broker's own `order_calc_margin()` and REAL contract sizes:

| Strategy | Worst margin | Trades over a 20% ceiling |
|---|---|---|
| SR+Bias XAUUSD 15m | $90,522 (**90.5%** of $100k) | **876 / 1896 (46%)** |
| SR+Bias NAS100 30m | $10,389 (10.4%) | 0 |
| First FVG NAS100 M1 | $3,182 (3.2%) | 0 |

The problem is entirely XAUUSD (1 lot = 100 oz → 9 lots ≈ $4.1M notional,
41x leverage on $100k). This is gap risk, not just margin risk: a 1% adverse
gap through a 2-point stop loses far more than "0.2% risk" implies.

**Fix:** `DEFAULT_MAX_MARGIN_PCT = 0.20` in `TradeManager`. The entry is
**scaled down, not blocked** — blocking would delete 46% of XAUUSD trades and
make live diverge wildly from backtest. Only if the venue minimum lot still
breaches the ceiling is the trade refused. Added `IBroker.calculate_margin()`
(MT5 uses `order_calc_margin`; PaperBroker uses contract size × leverage —
verified to return identical values: both $41,292 for 9 lots XAUUSD).

> **Consequence to remember:** SR+Bias XAUUSD live returns will now be LOWER
> than its backtest, because ~46% of entries get size-capped. That strategy
> needs re-backtesting *with* the cap to know its true expectancy.

### 2.7 Correction to my own audit
I first reported "SR+Bias NAS100: 208% margin, 11 trades exceed the account."
**That was wrong** — I assumed `contract_size=20` for NAS100; it is actually
`1`. Corrected numbers are in the table above. NAS100 has no margin problem.

---

## 3. OPEN issues (not yet fixed)

### 3.1 Spread is not in the strategy math — CRITICAL, still open
No backtest deducts transaction cost. Using the broker's live spreads
(NAS100 **3.0**, XAUUSD **0.39**, EURUSD **0.00014**), charged once per trade:

| Strategy | Median stop | Cost | PF before → after |
|---|---|---|---|
| First FVG NAS100 M1 | 7.70 pts | 0.39R | 1.21 → **0.73** (+87R → **−156R**) |
| SR+Bias XAUUSD 15m | 2.22 | 0.18R | 1.11 → **0.85** (+154R → **−251R**) |
| SR+Bias NAS100 30m | 30.0 | 0.03R | 1.22 → **1.06** |

First FVG's median stop is only 7.7 points against a 3.0 spread — ~39% of
each trade's risk is spent at entry. **Both First FVG and SR+Bias XAUUSD are
net-negative once real costs are applied.** Fix direction: subtract expected
spread inside the min-RR gate, and/or widen stops, and/or retire First FVG.

**Update 2026-08-28:** this row is the OLD M1/liquidity-TP First FVG
(`scripts/first_fvg_backtest.py`) and is still unresolved as described above.
A SEPARATE, newer First FVG variant (`scripts/nas100_first_fvg_15m_backtest.py`,
session-anchored at 00:00/09:30, 5m/15m, fixed 2R/3R target, median stop
22-39pt) has since been fully spread-tested across both session anchors,
both timeframes, and 2R/3R — see
[FIRST_FVG_15M_SPREAD_REPORT.md](FIRST_FVG_15M_SPREAD_REPORT.md). Verdict for
that variant: 09:30 + 15m + 2R is net-positive on spread (PF 1.01 5y / 1.16
1y); every other combination of that variant is not.

**Update 2026-08-28 (2):** SR+Bias has now been re-run with fresh MT5 data
(2020-01-01 onward) across ALL 5 symbols this repo has ever tested it
against (NAS100/XAUUSD/EURUSD/GBPUSD/USDJPY), all 4 timeframes (5/15/30/60m),
and both TP variants (fixed-3R / liquidity) -- 40 combinations total, with
spread applied throughout (real per-bar historical spread for XAUUSD/EURUSD/
GBPUSD/USDJPY -- confirmed non-zero and realistic across the full 2020-2026
history, unlike NAS100's; fixed 3.0pt for NAS100). Full ranking, methodology,
and a code-correctness review of both backtest scripts: see
[SR_DAILY_BIAS_SPREAD_REPORT.md](SR_DAILY_BIAS_SPREAD_REPORT.md).

Result: only 2/40 combinations clear PF ≥ 1.0 on BOTH the 5y and 1y window --
**NAS100 30m liquidity-TP** (PF 1.12 5y / 1.55 1y, n=695/111) is the clear
winner and matches this account's current live NAS100 M30 config. **This
row's XAUUSD 15m finding is now CONFIRMED and EXTENDED: none of XAUUSD's 4
timeframes clear PF 1.0 on the 5y window** (best is 60m fixed3r at 0.939) --
the currently-live `SRBias_XAUUSD_*` task should be reconsidered. EURUSD and
GBPUSD lose money in every single one of their 20 combinations (PF 0.39-0.78)
and should not be traded with this strategy at all.

### 3.2 PC sleep outage
The machine slept 2026-08-26 14:59 UTC → 2026-08-27 06:27 UTC (15.5h) and a
real First FVG signal was missed (BUY @ 29494.8, 00:21 NY). `WakeToRun` is
enabled on the tasks but `powercfg` shows wake timers are **disabled on
battery** (AC only). Fix: keep the PC on AC, or as admin:
```
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT
```

### 3.3 Lower-severity, documented
- **Aggregate risk:** 3 bots size independently against one account →
  simultaneous exposure can reach ~3x intended. `DailyRiskTracker` is shared
  in real mode but its file writes are non-atomic, and it resets on the UTC
  day (mid-session for NY strategies).
- **PO3 enters mid-bar** at the FVG edge, so it has the §2.2 same-bar class
  of gap. Checked: 0 occurrences in the current trade set, latent only.
- **`volume_min` clamp** in `position_sizer.py:76` rounds UP, which would
  over-risk on a small account. Not triggered at $100k (measured 1.0x exact).
- **Test hygiene:** `tests/test_research*.py` / `test_walk_forward.py` write
  `artifacts/parameter_stability.csv`, `optimization_results.csv`,
  `walk_forward_summary.csv`, `parameter_heatmap.csv` into the real repo dir
  on every pytest run. All-zero dummy output, unrelated to our strategies.

---

## 4. Verified CORRECT (measured, no action needed)

- Position sizing math: actual risk = **1.0x** intended, against real broker
  constraints, for all three live configs.
- SR+Bias daily bias uses only the previous fully-closed day — **no
  lookahead** (the §2.1 bug does not affect it, confirmed by re-running:
  identical results before and after the fix).
- First FVG uses no HTF context at all — no lookahead.
- Broker `stops_level = 0`; zero SL-distance violations across 3,499 trades.
- Parameter sensitivity is smooth (no overfitting signature):
  First FVG `min_gap` 2.4/3.0/3.6 → PF 1.13/1.21/1.23;
  SR+Bias `swing_len` 8/10/12 → PF 1.06/1.11/1.09.
- Cross-strategy monthly-return correlation: **−0.21 … +0.26** (effectively
  uncorrelated — the diversification claim is real).
- `.env` is not tracked and never was committed.

---

## 5. Trustworthy results (regenerated 2026-08-27 with all fixes)

Data: full M1 history refreshed from MT5, 2020 → 2026-08-26.
NAS100 1,903,718 bars · XAUUSD 2,273,116 bars · EURUSD 2,425,968 bars.
**All R-multiples below EXCLUDE spread (see §3.1).**

### Live strategies
| Strategy | Trades | WR | PF | Net R |
|---|---|---|---|---|
| First FVG NAS100 M1 | 613 | 32.6% | 1.21 | +87.0R |
| SR+Bias XAUUSD 15m | 1896 | 25.9% | 1.11 | +154.3R |
| SR+Bias NAS100 30m | 811 | 31.3% | 1.22 | +123.9R |

### PO3 (new this session) — NOT actionable
12 combos, **92 trades total across 6 years** (3–18 per combo). Headline PFs
look great (EURUSD 30m 9.22, NAS100 60m 8.50) but rest on 3–6 trades each —
statistically meaningless, and ~0.5–3 trades/year is unusable in practice.
This is the spec working as written (sweep + displacement + MSS + FVG-retest
must all coincide), not a bug. To evaluate it seriously, relax one of the
four hard gates. Full table: `artifacts/po3_trades_*.csv`.

**Update 2026-08-28:** re-run extended to 5 symbols (added GBPUSD/USDJPY)
with spread applied — see [PO3_SPREAD_REPORT.md](PO3_SPREAD_REPORT.md). Also
found and FIXED a real bug while re-reviewing: entry is a mid-bar FVG-zone
touch, but SL/TP scanning only started the FOLLOWING bar, so a same-bar
stop-out on the entry bar itself was never checked -- the exact §2.2 class
of gap this doc already flagged as "latent" here. Fixed in
`scripts/po3_spread_sweep.py` (not yet backported to the original
`scripts/po3_backtest.py`). Net verdict unchanged and now worse: 20
combinations, **144 trades total**, 1-22 per combination (one cell is a
single trade). No amount of spread modeling matters when the sample is this
small -- do not rank or trust any PF in this set. Recommendation stands:
relax a hard gate and re-test, or drop the strategy.

### Order Flow — edge did not survive the lookahead fix
XAUUSD 5m PF 0.99, NAS100 15m PF 0.94. A full 12-combo re-sweep was running
when this session ended; check `artifacts/order_flow_bias_trades_*.csv`
timestamps (anything from 2026-08-27 18:55 or later is post-fix).
**All pre-fix Order Flow conclusions — rankings, "improving trend", monthly
tables — are void.**

**Update 2026-08-28:** the re-sweep is now finished, extended from 3 to 5
symbols (added GBPUSD/USDJPY), and spread has been applied for the first
time -- see [ORDER_FLOW_SPREAD_REPORT.md](ORDER_FLOW_SPREAD_REPORT.md). Code
re-reviewed, no new correctness issues found (the §2.1 fix holds). Verdict:
**do not take this strategy live.** 4/20 combinations clear PF >= 1.0 on
both 5y/1y, but every single one rests on 5-51 trades over 5 years (2-16 in
the 1y window) -- an order of magnitude fewer than First FVG (n=1000+) or
SR (n=400-3000) -- so the ranking is not statistically trustworthy (same
"too few trades to mean anything" regime already flagged for PO3 in this
doc). Half-year breakdowns for the top 2 candidates swing from PF 0.00 to
PF 9.29 between six-month windows on 2-9 trades each, which is noise, not
edge.

### Archived
76 superseded CSVs were moved to `artifacts/old/` (gitignored; the committed
versions remain in git history). They predate the fixes and are misleading.

---

## 6. Key commands

```bash
# Backtests
./.venv/Scripts/python.exe -m scripts.backtest_midnight_fvg_live_class
./.venv/Scripts/python.exe scripts/sr_daily_bias_backtest_liquidity_tp.py --symbol XAUUSD --tf 15
./.venv/Scripts/python.exe -m scripts.po3_sweep
./.venv/Scripts/python.exe -m scripts.order_flow_bias_sweep

# Analysis helpers
./.venv/Scripts/python.exe -m scripts.summarize_trade_log --input <csv> --label <name>
./.venv/Scripts/python.exe -m scripts.daily_breakdown --input <csv> --label <name> --days 30
./.venv/Scripts/python.exe -m scripts.robustness_analysis   # recency split, bootstrap, cost stress

# Live (paper is always safe)
./.venv/Scripts/python.exe run_live_sr_bias.py --symbol XAUUSD --timeframe M15 --paper

# Data refresh — NOTE: --start must cover full history or the CSV is truncated
./.venv/Scripts/python.exe -m data.download_history --symbols NAS100,XAUUSD,EURUSD \
    --timeframe M1 --start 2020-01-01 --output-dir data/history
```

Tests: `./.venv/Scripts/python.exe -m pytest -q`
(`tests/test_swing_detector.py` and `tests/test_liquidity.py` contain
timing-sensitive performance assertions that fail under CPU load; one known
`XFAIL` for Bug #29 in `walkthrough.md`.)

---

## 7. Suggested next steps

1. ~~Spread into the RR gate~~ **DONE 2026-08-28** for First FVG (09:30+15m+2R
   survives, see [FIRST_FVG_15M_SPREAD_REPORT.md](FIRST_FVG_15M_SPREAD_REPORT.md)),
   SR+Bias (NAS100 30m liquidity survives, XAUUSD does not, see
   [SR_DAILY_BIAS_SPREAD_REPORT.md](SR_DAILY_BIAS_SPREAD_REPORT.md)), and
   Order Flow (nothing survives with a trustworthy sample size, see
   [ORDER_FLOW_SPREAD_REPORT.md](ORDER_FLOW_SPREAD_REPORT.md)). Still
   outstanding: the OLD M1/liquidity-TP First FVG variant
   (`scripts/first_fvg_backtest.py`, §3.1's original row) and PO3.
2. **Re-backtest SR+Bias XAUUSD with the margin cap** (§2.6) to get its true
   post-cap expectancy.
3. ~~Finish / review the Order Flow re-sweep~~ **DONE 2026-08-28**, including
   the gate relaxation this item asked for: `OF_MIN_CONFIRMATIONS` 3→2
   (`--min-confirmations` on `scripts.order_flow_bias_spread_sweep`) took the
   sample from 144→2348 trades across 20 combos, but PF got WORSE on most
   combos, not better. Verdict unchanged: do not take it live. USDJPY 15m
   (n=72/5y, n=17/1y, PF 1.26/1.02) is the one combo worth a second look; see
   [ORDER_FLOW_SPREAD_REPORT.md](ORDER_FLOW_SPREAD_REPORT.md) section 6.
4. ~~Decide on PO3~~ **DONE 2026-08-28** — extended re-sweep (5 symbols,
   spread, same-bar-stop bug fixed) first confirmed the original verdict,
   worse (144 trades/20 combos). Gate relaxation found the REAL bottleneck
   was never the 4 documented hard gates: a skip-funnel diagnostic on
   NAS100 60m showed `neutral_bias` rejecting 83% of all bars, 5x every
   other gate combined -- MSS_LOOKBACK_BARS 10->20 changed nothing (13->13
   trades), MIN_RR 2.0->1.5 barely moved it (13->15), but
   `DAILY_BIAS_VOTE_THRESHOLD` 2->1 (trade on either of the 2 daily-bias
   votes agreeing, not requiring both) took it to 25 (2x) with PF staying
   high (8.50->6.15). Full 5-symbol re-sweep at threshold=1: 549 trades
   across 20 combos (3.8x), **12/20 now clear PF>=1.0 on both 5y/1y**
   (0/20 before). Best: USDJPY 5m (n=62/11) and NAS100 60m (n=25/6). Still
   nowhere near First FVG/SR's sample sizes and PFs (1.09-8.66) are
   suspiciously high for the small windows -- read as "worth paper-testing
   for a few months," not "validated." See
   [PO3_SPREAD_REPORT.md](PO3_SPREAD_REPORT.md) section 4.
5. **Fix the power policy** (§3.2) before trusting uptime.
6. ~~Bootstrap/Monte Carlo CI on the two live configs~~ **DONE 2026-08-28**
   -- see [ROBUSTNESS_VALIDATION_REPORT.md](ROBUSTNESS_VALIDATION_REPORT.md).
   Important asymmetry found: SR+Bias's 5y PF has an 82.5% bootstrap
   probability of being a real positive edge; **First FVG's does not** --
   only 54.0% (90% CI [0.90, 1.12] straddles 1.0, essentially a coin flip).
   Both are still the best surviving configs of their families, but treat
   First FVG's edge as unconfirmed, not just smaller, when sizing risk.
   Walk-forward split is still outstanding (largely superseded by the
   recency split already run here -- neither strategy's parameters were
   fit via a search/optimization loop, so there's no in-sample fit to
   guard against).
7. ~~SR's live-vs-backtest fidelity check~~ **DONE 2026-08-28** -- see
   [ROBUSTNESS_VALIDATION_REPORT.md](ROBUSTNESS_VALIDATION_REPORT.md)'s new
   section. Ran the ACTUAL `SrDailyBiasStrategy` bar-by-bar over the full
   6y NAS100 M30 history (`scripts/backtest_sr_daily_bias_live_class.py`)
   and diffed against the batch script. The docstring's "KNOWN FIDELITY
   GAP" is real but small: live class finds 838 trades vs batch's 811
   (+3.3%), PF (net of spread) 1.024 vs 1.057 -- within the bootstrap CI's
   noise band, not a material divergence. (Caught and fixed a bug in the
   verification script itself along the way: a boolean `in_position` flag
   that got cleared via a same-iteration forward-search, which silently
   disabled the one-trade-at-a-time gate and inflated the count to 1017
   before the fix -- see that script's own comment for why `open_until_idx`
   is the correct pattern, matching backtest_midnight_fvg_live_class.py.)

### Methodology rules this project now follows
- Never trust a backtest number without checking for lookahead — the
  future-perturbation test in `tests/test_backtest_lookahead.py` is the
  pattern to copy for any new HTF input.
- Always verify a regression test's teeth by reverting the fix.
- Prefer identical code in backtest and live; where they must differ,
  document the divergence explicitly (see `strategy/sr_daily_bias.py`'s
  "KNOWN FIDELITY GAP" note).
- Report R-multiples both with and without transaction cost.
