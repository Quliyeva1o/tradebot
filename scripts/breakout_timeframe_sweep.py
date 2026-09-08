"""Sweeps NASDAQ ORB M1 Breakout's opening-range length and breakout-scan bar size.

The spec fixes the opening range at 09:30-09:45 NY and scans M1 bars for the
breakout close; `run_backtest`'s or_minutes/scan_minutes parameters (added
2026-09-08) let both be varied. The scan window always opens one opening-range
candle after 09:30, so or_minutes shifts the entry window with it.

A coarser scan bar also coarsens SL/TP hit detection -- both levels can fall
inside one bar and the engine resolves SL first -- so higher scan_minutes
values are slightly pessimistic, not optimistic. R is held at 4.0, the target
that won Sweep 1 on XAUUSD across all three windows; refine R on whatever
timeframe pair wins here rather than sweeping both at once.

Usage:
    python -m scripts.breakout_timeframe_sweep
    python -m scripts.breakout_timeframe_sweep --symbols XAUUSD --tp-r 3.0
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.nasdaq_orb_m1_breakout_backtest import run_backtest as orb_backtest
from scripts.two_strategy_symbol_sweep import SYMBOLS, DATA_DIR, recent_spread, window_stats, fmt

GRID = [(5, 1), (5, 5), (15, 1), (15, 5), (30, 1), (30, 5), (30, 15), (60, 5), (60, 15)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--tp-r", type=float, default=4.0)
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    today = date.today()
    w1y, w2m = today - timedelta(days=365), today - timedelta(days=61)

    print(f"Breakout timeframe grid, tp_r={args.tp_r:g}, LONG-only, full stop-mode, her simvol oz 2026 spread-i ile")
    print(f"{'SYMBOL':8s} {'OR':>4s} {'SCAN':>5s} {'SPR':>7s}  "
          f"{'FULL HISTORY':^26s}  {'LAST 1Y':^26s}  {'LAST 2M':^26s}")
    print("-" * 118, flush=True)

    for sym in symbols:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            print(f"{sym:8s} -- data yoxdur", flush=True)
            continue
        sp = recent_spread(csv)
        for or_m, scan_m in GRID:
            tr = orb_backtest(str(csv), "full", sp, args.tp_r, "long",
                              or_minutes=or_m, scan_minutes=scan_m)
            print(f"{sym:8s} {or_m:>3d}m {scan_m:>4d}m {sp:>7.3f}  "
                  f"{fmt(window_stats(tr, None))}  {fmt(window_stats(tr, w1y))}  {fmt(window_stats(tr, w2m))}",
                  flush=True)
        print("-" * 118, flush=True)


if __name__ == "__main__":
    main()
