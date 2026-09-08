"""Sweeps XAUUSD ORB Liquidity-Sweep's OR-candle size and entry window per symbol.

`bar_minutes` sets the opening-range candle size and the entry window starts
one such candle after 09:30 NY, so the two parameters are coupled: at
bar_minutes=15 with the default 10:00 cutoff the window holds exactly ONE bar,
which cannot fit a sweep + displacement + FVG + entry, and the run yields zero
trades. That is mechanics, not a bug -- so larger candles are only meaningful
alongside a wider window, and this sweep pairs them accordingly.

On XAUUSD (2026-09-08) the live config (5m / 10:00) scored PF 1.173 while
15m / 12:00 scored PF 1.365 on 1.7x the sample, which is what prompted running
the same grid across every symbol.

Usage:
    python -m scripts.liqsweep_timeframe_sweep
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, time as dtime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.two_strategy_symbol_sweep import SYMBOLS, DATA_DIR, recent_spread, window_stats, fmt
from scripts.xauusd_orb_liquidity_sweep_backtest import run_backtest as sweep_backtest

# (or_candle_minutes, entry_window_end). (15, 10:00) is omitted: structurally
# zero trades, see module docstring.
GRID = [(5, "10:00"), (5, "12:00"), (15, "11:00"), (15, "12:00")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--tp-r", type=float, default=2.0)
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    today = date.today()
    w1y, w2m = today - timedelta(days=365), today - timedelta(days=61)

    print(f"LiqSweep timeframe grid, tp_r={args.tp_r:g}, her simvol oz 2026 spread-i ile")
    print(f"{'SYMBOL':8s} {'OR':>4s} {'ENTRY_END':>10s} {'SPR':>6s}  "
          f"{'FULL HISTORY':^26s}  {'LAST 1Y':^26s}  {'LAST 2M':^26s}")
    print("-" * 118, flush=True)

    for sym in symbols:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            print(f"{sym:8s} -- data yoxdur", flush=True)
            continue
        sp = recent_spread(csv)
        for bm, end in GRID:
            h, m = map(int, end.split(":"))
            tr, _ = sweep_backtest(
                str(csv), tp_r=args.tp_r, spread_points=sp, enable_breakout=False,
                bar_minutes=bm, entry_window_end=dtime(h, m), entry_fill_mode="next_open",
            )
            print(f"{sym:8s} {bm:>3d}m {end:>10s} {sp:>6.3f}  "
                  f"{fmt(window_stats(tr, None))}  {fmt(window_stats(tr, w1y))}  {fmt(window_stats(tr, w2m))}",
                  flush=True)
        print("-" * 118, flush=True)


if __name__ == "__main__":
    main()
