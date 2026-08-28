"""Re-runs the "Original SL vs Wide SL" First FVG comparison (the M1,
00:00-session, liquidity-era variant -- scripts/first_fvg_backtest.py vs
scripts/first_fvg_backtest_wide_sl.py, NOT the 09:30/15m/2R variant this
repo has since settled on) WITH real spread applied, since the only
previous version of this comparison the user has was spread-free.

Reuses both scripts' run_backtest() UNCHANGED -- imported, not
reimplemented. Both already handle same-bar-as-entry stop-outs correctly
(entry is a mid-bar FVG-zone touch; both scripts check that entry bar's own
high/low against SL before falling through to later bars -- verified by
reading both files, no bug found here, unlike PO3).

Spread: fixed 3.0-point NAS100 round-trip constant (same reasoning as every
other NAS100 spread-cost calc in this repo -- the real per-bar spread column
reads 0.0 before 2024), applied as cost_r = 3.0 / risk_distance using each
trade's own entry_price/stop (already on the Trade dataclass in both
scripts), subtracted from r_multiple.

Usage:
    python -m scripts.first_fvg_wide_sl_spread_compare
"""

from __future__ import annotations

import json

import pandas as pd

import scripts.first_fvg_backtest as orig
import scripts.first_fvg_backtest_wide_sl as wide

SPREAD_POINTS = 3.0
STARTING_BALANCE_1000 = 1_000.0


def net_r(trades: list) -> list[float]:
    out = []
    for t in trades:
        risk_dist = abs(t.entry_price - t.stop)
        cost_r = (SPREAD_POINTS / risk_dist) if risk_dist > 0 else 0.0
        out.append(t.r_multiple - cost_r)
    return out


def period_stats(trades: list, rs: list[float], end_date, offset: pd.DateOffset) -> dict:
    start = (pd.Timestamp(end_date) - offset).date().isoformat()
    end = end_date.isoformat()
    pairs = [(t, r) for t, r in zip(trades, rs) if start <= str(t.date) <= end]
    n = len(pairs)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "total_r": 0.0}
    wins = sum(1 for _, r in pairs if r > 0)
    gp = sum(r for _, r in pairs if r > 0)
    gl = abs(sum(r for _, r in pairs if r <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    total_r = sum(r for _, r in pairs)
    return {
        "trades": n, "win_rate": round(wins / n * 100, 1),
        "profit_factor": (round(pf, 3) if gl > 0 else None), "total_r": round(total_r, 2),
    }


PERIODS = {
    "1mo": pd.DateOffset(months=1), "3mo": pd.DateOffset(months=3),
    "6mo": pd.DateOffset(months=6), "1y": pd.DateOffset(years=1),
    "3y": pd.DateOffset(years=3), "5y": pd.DateOffset(years=5),
}
RISK_LEVELS = [0.005, 0.01, 0.05]


def main() -> None:
    orig_trades = orig.run_backtest()
    wide_trades = wide.run_backtest()
    orig_net = net_r(orig_trades)
    wide_net = net_r(wide_trades)

    end_date = max(max(t.date for t in orig_trades), max(t.date for t in wide_trades))
    print(f"Data through: {end_date}\n")

    results: dict[str, dict] = {"end_date": end_date.isoformat(), "orig": {}, "wide": {}}
    for label, offset in PERIODS.items():
        results["orig"][label] = period_stats(orig_trades, orig_net, end_date, offset)
        results["wide"][label] = period_stats(wide_trades, wide_net, end_date, offset)

    print(f"{'Period':6} {'Trade(O)':>9} {'PF(O)':>7} {'R(O)':>8}   {'Trade(W)':>9} {'PF(W)':>7} {'R(W)':>8}")
    for label in ["1mo", "3mo", "6mo", "1y", "3y", "5y"]:
        o, w = results["orig"][label], results["wide"][label]
        print(f"{label:6} {o['trades']:>9} {str(o['profit_factor']):>7} {o['total_r']:>8}   {w['trades']:>9} {str(w['profit_factor']):>7} {w['total_r']:>8}")

    print("\n=== $1,000 account net P&L, risk levels 0.5% / 1% / 5% ===")
    print(f"{'Period':6} {'Orig@0.5%':>10} {'Orig@1%':>9} {'Orig@5%':>9}   {'Wide@0.5%':>10} {'Wide@1%':>9} {'Wide@5%':>9}")
    for label in ["1mo", "3mo", "6mo", "1y", "3y", "5y"]:
        o_r, w_r = results["orig"][label]["total_r"], results["wide"][label]["total_r"]
        o_vals = [round(o_r * rl * STARTING_BALANCE_1000, 2) for rl in RISK_LEVELS]
        w_vals = [round(w_r * rl * STARTING_BALANCE_1000, 2) for rl in RISK_LEVELS]
        print(f"{label:6} {o_vals[0]:>10} {o_vals[1]:>9} {o_vals[2]:>9}   {w_vals[0]:>10} {w_vals[1]:>9} {w_vals[2]:>9}")

    with open("artifacts/first_fvg_wide_sl_spread_compare.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("\nJSON written to artifacts/first_fvg_wide_sl_spread_compare.json")


if __name__ == "__main__":
    main()
