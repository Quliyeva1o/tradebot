"""First-FVG-per-"silver bullet"-window backtest (TradingView "First FVG [joshuuu]" rules).

That indicator is closed-source, so this reimplements what its published
description states: within each of three NY windows (03:00-04:00, 10:00-11:00,
14:00-15:00) it marks the FIRST 3-candle fair value gap, and ships an R:R tool
for the resulting trade. It is NOT the 09:30-anchored First FVG this repo
already tested and rejected -- the window is the whole difference, so this is a
new question, not a re-run.

Mechanics reuse the repo's existing First FVG primitives unchanged
(scripts/nas100_first_fvg_15m_backtest.find_first_fvg / simulate_trade):
gap = low[i] > high[i-2] (bullish) or high[i] < low[i-2] (bearish); entry on
the first retrace to the gap's near edge; stop at the middle candle's body
extreme; target tp_r x risk. The one addition here is that the FVG must FORM
inside the window while the trade is still free to run to the end of the day.

Spread is charged per symbol from its own recent real average, as everywhere
else in this sweep family.

Usage:
    python -m scripts.silver_bullet_fvg_backtest
    python -m scripts.silver_bullet_fvg_backtest --symbols XAUUSD --tp-r 3.0
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, time as dtime, timedelta
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.backtest_common import NY, load_m1, resample
from scripts.nas100_first_fvg_15m_backtest import find_first_fvg, simulate_trade
from scripts.two_strategy_symbol_sweep import SYMBOLS, DATA_DIR, recent_spread

# The indicator's three "silver bullet" windows, NY local time.
WINDOWS = {
    "03:00-04:00": (dtime(3, 0), dtime(4, 0)),
    "10:00-11:00": (dtime(10, 0), dtime(11, 0)),
    "14:00-15:00": (dtime(14, 0), dtime(15, 0)),
}
TIMEFRAMES = [5, 15]


def run_window(bars: pd.DataFrame, start: dtime, end: dtime, tp_r: float, spread: float) -> list[dict]:
    """One trade per day at most: the first FVG forming inside [start, end)."""
    out: list[dict] = []
    for _day, day_df in bars.groupby(pd.Series(bars.index.date, index=bars.index)):
        rest = day_df[day_df.index.time >= start]
        if rest.empty:
            continue
        window = rest[rest.index.time < end]
        if len(window) < 3:
            continue

        fvg = find_first_fvg(window)
        if fvg is None:
            continue

        # find_first_fvg indexed into `window`; simulate_trade needs the same
        # bar's position inside the wider `rest` frame the trade runs on.
        confirm_pos = rest.index.get_loc(fvg["confirm_time"])
        trade = simulate_trade(rest, {**fvg, "confirm_i": confirm_pos}, long_only=False, tp_r=tp_r)
        if trade is None:
            continue

        risk = abs(trade.entry_price - trade.stop)
        if risk <= 0:
            continue
        out.append({"day": trade.day, "r": trade.r_multiple - spread / risk})
    return out


def stats(rows: list[dict], since: date | None) -> str:
    sel = [r for r in rows if since is None or date.fromisoformat(str(r["day"])[:10]) >= since]
    if not sel:
        return f"{'n=0':^26s}"
    rs = [r["r"] for r in sel]
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    return f"n={len(sel):>4} PF={pf:>5.3f} R={sum(rs):>+7.1f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--tp-r", type=float, default=2.0)
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    today = date.today()
    w1y, w2m = today - timedelta(days=365), today - timedelta(days=61)

    print(f"Silver-bullet First FVG, tp_r={args.tp_r:g}, her simvol oz 2026 spread-i ile")
    print(f"{'SYMBOL':8s} {'TF':>4s} {'WINDOW':>12s} {'SPR':>7s}  "
          f"{'FULL HISTORY':^26s}  {'LAST 1Y':^26s}  {'LAST 2M':^26s}")
    print("-" * 122, flush=True)

    for sym in symbols:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            print(f"{sym:8s} -- data yoxdur", flush=True)
            continue
        sp = recent_spread(csv)
        m1 = load_m1(str(csv))
        for tf in TIMEFRAMES:
            bars = resample(m1, tf)
            bars.index = bars.index.tz_convert(NY)
            for label, (start, end) in WINDOWS.items():
                rows = run_window(bars, start, end, args.tp_r, sp)
                print(f"{sym:8s} {tf:>3d}m {label:>12s} {sp:>7.3f}  "
                      f"{stats(rows, None)}  {stats(rows, w1y)}  {stats(rows, w2m)}", flush=True)
        print("-" * 122, flush=True)


if __name__ == "__main__":
    main()
