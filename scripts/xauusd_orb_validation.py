"""Validation battery for the XAUUSD 09:30 ORB + Liquidity-Sweep reversal
setup (Setup B only -- Setup A/breakout was already found PF<1 net of spread
in the initial run and is excluded here), reusing the exact same methodology
already applied to the two production configs (First FVG, SR+Bias) rather
than inventing a fourth measurement convention:

  - Recency split, bootstrap resample, spread cost-stress:
    scripts/robustness_analysis.py (unchanged import).
  - Calendar-time walk-forward folds + Monte Carlo (bootstrap resample +
    adverse noise, fixed-fractional real-risk sizing):
    scripts/walk_forward_montecarlo.py (unchanged import).
  - Regime-conditioned performance (TRENDING/MEAN_REVERTING/RANGING):
    scripts/regime_conditioned_performance.py's tag_and_report (unchanged
    import).

Updated 2026-09-01 (M15 port session): this script previously generated its
trade log via `run_backtest("data/history/XAUUSD_M1.csv", spread_points=0.0)`
with NO `enable_breakout=False` -- i.e. it ran the COMBINED Setup-A+B batch
and filtered to `setup_type=="reversal"`, exactly the filtering bug
`strategy/xauusd_orb_liquidity_sweep.py`'s own docstring documents as WRONG
(Setup A's excluded, losing trades occupy the shared position slot and
suppress genuine Setup B opportunities) -- this script itself was never
fixed when that bug was found and fixed elsewhere, which is why the
strategy docstring's M5 numbers cite a manual `python -c` run instead of
this script's own output. Fixed here: `enable_breakout=False` now passed
explicitly. Also switched from the M5 defaults (bar_minutes=5,
entry_window_end=10:00) to the validated M15 config (bar_minutes=15,
entry_window_end=11:00), and from the idealized `zone_edge` entry fill to
the REALISTIC `next_open` fill (a live/paper MARKET order actually fills at
the next bar's open, not the FVG zone's exact edge -- see
execution/fill_simulator.py and the strategy docstring's "KRİTİK TAPINTI"
section) -- this is the number this repo's convention treats as trustworthy,
not the idealized ceiling. Data source is this machine's own
data/history/XAUUSD_M1.csv (FXTM-Demo02 account, plain "XAUUSD" ticker,
2020-01-02 -> 2026-08-27, 6.7 years) rather than the XAUUSD.ifx file the M5
numbers were measured against (not present on this machine -- see
XAUUSD_ORB_SESSION_HANDOFF.md §0).

Sample-size caveat, stated up front rather than left implicit: this
next-open, isolated-Setup-B configuration has n=137 trades over 6.7 years
(~20/year), still a fraction of SR+Bias's n~800+ or First FVG's n~1100+.
Confidence intervals below will be much wider than those two strategies'
reports -- that is the data talking, not a flaw in the method.

Usage:
    python -m scripts.xauusd_orb_validation
"""

from __future__ import annotations

import sys
from datetime import time as dtime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Timeframe
from scripts.backtest_common import load_m1, resample
from scripts.regime_conditioned_performance import df_to_bars, tag_and_report
from scripts.robustness_analysis import bootstrap, cost_stress, load as load_robustness, recency_split, stats
from scripts.walk_forward_montecarlo import Trade as WfmcTrade, monte_carlo, walk_forward
from scripts.xauusd_orb_liquidity_sweep_backtest import run_backtest, write_trades_csv

INPUT_CSV = "data/history/XAUUSD_M1.csv"
REVERSAL_CSV = "artifacts/xauusd_orb_M15_nextopen_reversal_only_trades.csv"
BAR_MINUTES = 15
ENTRY_WINDOW_END = dtime(11, 0)
N_FOLDS = 7  # ~137 trades / 6.7y -> ~1yr, ~20 trades/fold; matches the M5-era "7 six-month folds" granularity
LIVE_RISK_PCT = 0.005  # matches the strategy's own documented 0.5% default


def generate_reversal_trades() -> None:
    """Gross (no spread baked in) so downstream tools apply their own,
    consistent spread-cost convention instead of double-counting it.
    Setup B isolated (`enable_breakout=False`) and realistic next-bar-open
    fill -- see module docstring for why both matter."""
    trades, _funnel = run_backtest(
        INPUT_CSV, spread_points=0.0, enable_breakout=False,
        bar_minutes=BAR_MINUTES, entry_window_end=ENTRY_WINDOW_END, entry_fill_mode="next_open",
    )
    reversal = [t for t in trades if t.setup_type == "reversal"]
    write_trades_csv(reversal, REVERSAL_CSV)
    print(f"Wrote {len(reversal)} reversal-only (gross, M15, next-open fill) trades to {REVERSAL_CSV}")


def run_robustness_battery() -> None:
    trades = load_robustness(REVERSAL_CSV)
    print(f"\n{'=' * 78}\nRobustness battery (scripts/robustness_analysis.py methods)  n={len(trades)}\n{'=' * 78}")
    print("Full history (gross):", stats([t.r for t in trades]))
    print("Recency split (80/20):", recency_split(trades))
    print("Bootstrap (5000x resample of R-multiples):", bootstrap(trades))
    print("Cost-stressed (XAUUSD 0.39pt spread deducted):", cost_stress(trades, "XAUUSD"))


def load_wfmc_trades() -> list[WfmcTrade]:
    """Builds walk_forward_montecarlo.Trade objects net of the 0.39pt XAUUSD
    spread (that module's Monte Carlo model needs `.risk_points` on top of
    `.pnl`/`.r_net`, which robustness_analysis.TradeRow doesn't carry, so the
    rows are re-read directly rather than converted from one dataclass to
    the other)."""
    import csv as csv_mod
    from datetime import datetime

    spread = 0.39
    out = []
    with open(REVERSAL_CSV, newline="", encoding="utf-8") as f:
        for row in csv_mod.DictReader(f):
            entry, stop = float(row["entry_price"]), float(row["stop"])
            risk_points = abs(entry - stop)
            cost_r = spread / risk_points if risk_points > 0 else 0.0
            r_net = float(row["r_multiple"]) - cost_r
            pnl_net = r_net * 1_000.0  # $1000/R, matches walk_forward_montecarlo's own RISK_PER_TRADE_USD convention
            out.append(WfmcTrade(
                ts=datetime.fromisoformat(row["entry_time"]),
                pnl=pnl_net, r_net=r_net, risk_points=risk_points,
            ))
    out.sort(key=lambda t: t.ts)
    return out


def run_walk_forward_and_monte_carlo() -> None:
    trades = load_wfmc_trades()
    print(f"\n{'=' * 78}\nWalk-forward + Monte Carlo (net of 0.39pt spread)  n={len(trades)}, "
          f"{trades[0].ts.date()} -> {trades[-1].ts.date()}\n{'=' * 78}")

    print(f"\n-- Walk-forward: {N_FOLDS} equal calendar-time folds --")
    folds = walk_forward(trades, N_FOLDS)
    passing = 0
    for f in folds:
        tag = "OK " if f["pf"] >= 1.0 else "FAIL"
        if f["pf"] >= 1.0:
            passing += 1
        print(f"  [{tag}] Fold {f['fold']} {f['start']} -> {f['end']}: "
              f"n={f['n']:3d}  WR={f['win_rate']:5.1f}%  PF={f['pf']:.3f}  net_R={f['net_r']:+.2f}")
    print(f"  Folds with PF>=1.0: {passing}/{N_FOLDS} ({passing/N_FOLDS*100:.0f}%)")

    for model_label, is_ff, rp in [
        (f"FIXED $ per trade (research/monte_carlo.py convention, $1000/trade always)", False, None),
        (f"FIXED-FRACTIONAL {LIVE_RISK_PCT*100:.2f}% of current balance (this strategy's own default risk)", True, LIVE_RISK_PCT),
    ]:
        print(f"\n-- Monte Carlo (5000 trials, bootstrap resample + 0-1.5pt extra adverse noise/trade) -- {model_label} --")
        mc = monte_carlo(trades, 5000, fixed_fractional=is_ff, risk_pct=rp or 0.01)
        print(f"  Expected return:        ${mc['expected_return']:+,.0f} ({mc['expected_return']/100_000*100:+.1f}%)")
        print(f"  Median final balance:   ${mc['median_final_balance']:,.0f}")
        print(f"  95% CI final balance:   ${mc['ci95_final_balance'][0]:,.0f} -> ${mc['ci95_final_balance'][1]:,.0f}")
        print(f"  Median max drawdown:    {mc['median_drawdown']*100:.1f}%")
        print(f"  Worst-case max drawdown: {mc['worst_drawdown']*100:.1f}%")
        print(f"  Risk of ruin (<30% of start): {mc['risk_of_ruin_pct']:.2f}%")
        print(f"  P(finish at new equity high): {mc['prob_new_high_pct']:.1f}%")


def run_regime_analysis() -> None:
    m1 = load_m1(INPUT_CSV)
    m15 = df_to_bars(resample(m1, BAR_MINUTES))
    import csv as csv_mod
    from datetime import datetime
    trades = []
    with open(REVERSAL_CSV, newline="", encoding="utf-8") as f:
        for row in csv_mod.DictReader(f):
            trades.append((datetime.fromisoformat(row["entry_time"]), float(row["r_multiple"])))
    trades.sort(key=lambda t: t[0])
    tag_and_report("XAUUSD ORB reversal (Setup B, M15, next-open, gross)", trades, m15, Timeframe.M15, "XAUUSD")


if __name__ == "__main__":
    generate_reversal_trades()
    run_robustness_battery()
    run_walk_forward_and_monte_carlo()
    run_regime_analysis()
