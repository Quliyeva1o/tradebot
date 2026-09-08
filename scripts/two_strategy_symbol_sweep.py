"""Sweeps both surviving strategies across every viable FundingPips symbol and R-target.

Runs NASDAQ ORB M1 Breakout (LONG-only, `full` stop-mode) and XAUUSD ORB
Liquidity-Sweep (Setup B only, next_open fill) against each symbol's own M1
history, each with that symbol's own recent real spread rather than a shared
constant, at several R-targets, and reports full-history plus last-1-year and
last-2-month windows.

Risk-per-trade is deliberately NOT a sweep dimension: it scales position size
linearly and changes no trade's entry, exit or R-multiple, so PF/win-rate/netR
are identical at 0.5% and 2%. Sizing is a separate (Monte Carlo / ruin-risk)
decision, not a signal-quality one.

The windowed columns matter as much as the full-history one: NDX100
(2026-09-08) looked fine over 6.7 years yet was net-negative over the last two
months, so a config only counts as viable when both agree.

Usage:
    python -m scripts.two_strategy_symbol_sweep
    python -m scripts.two_strategy_symbol_sweep --symbols XAUUSD,GER40
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.nasdaq_orb_m1_breakout_backtest import run_backtest as orb_backtest
from scripts.xauusd_orb_liquidity_sweep_backtest import run_backtest as sweep_backtest

SYMBOLS = ["XAUUSD", "GER40", "NDX100", "DJI30", "SPX500", "JP225", "FTSE100"]
BREAKOUT_RS = [2.0, 3.0, 4.0]
SWEEP_RS = [2.0, 3.0]
DATA_DIR = Path("data/history/fundingpips")


def recent_spread(csv_path: Path, year: str = "2026") -> float:
    """That symbol's own mean spread over `year`, used as its flat cost constant.

    Index CFDs on this feed carry a zero-inflated spread column for their early
    years, so an all-history mean would understate today's real cost.
    """
    total = count = 0.0
    with csv_path.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            if line.startswith(year):
                parts = line.rstrip("\n").split(",")
                if len(parts) >= 7 and parts[6]:
                    total += float(parts[6])
                    count += 1
    return round(total / count, 4) if count else 0.0


def window_stats(trades: list, start: date | None) -> dict:
    rows = [t for t in trades if start is None or date.fromisoformat(str(t.day)[:10]) >= start]
    n = len(rows)
    if n == 0:
        return {"n": 0}
    rs = [t.r_multiple for t in rows]
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    return {
        "n": n,
        "wr": round(sum(1 for r in rs if r > 0) / n * 100, 1),
        "pf": round(gp / gl, 3) if gl > 0 else float("inf"),
        "netr": round(sum(rs), 1),
    }


def fmt(s: dict) -> str:
    if s["n"] == 0:
        return f"{'n=0':^26s}"
    return f"n={s['n']:>4} PF={s['pf']:>5.3f} R={s['netr']:>+7.1f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    today = date.today()
    w1y, w2m = today - timedelta(days=365), today - timedelta(days=61)

    print(f"{'SYMBOL':8s} {'STRATEGY':14s} {'SPR':>6s}  {'FULL HISTORY':^26s}  {'LAST 1Y':^26s}  {'LAST 2M':^26s}")
    print("-" * 118, flush=True)

    for sym in symbols:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            print(f"{sym:8s} -- data yoxdur ({csv})", flush=True)
            continue
        sp = recent_spread(csv)

        for r in BREAKOUT_RS:
            tr = orb_backtest(str(csv), "full", sp, r, "long")
            print(f"{sym:8s} {f'Breakout {r:g}R':14s} {sp:>6.3f}  "
                  f"{fmt(window_stats(tr, None))}  {fmt(window_stats(tr, w1y))}  {fmt(window_stats(tr, w2m))}",
                  flush=True)

        for r in SWEEP_RS:
            tr, _ = sweep_backtest(
                str(csv), tp_r=r, spread_points=sp, enable_breakout=False,
                entry_fill_mode="next_open",
            )
            print(f"{sym:8s} {f'LiqSweep {r:g}R':14s} {sp:>6.3f}  "
                  f"{fmt(window_stats(tr, None))}  {fmt(window_stats(tr, w1y))}  {fmt(window_stats(tr, w2m))}",
                  flush=True)
        print("-" * 118, flush=True)


if __name__ == "__main__":
    main()
