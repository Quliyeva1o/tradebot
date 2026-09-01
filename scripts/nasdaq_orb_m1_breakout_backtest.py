"""NASDAQ 09:30 NY -- 15M Opening Range + M1 Breakout backtest.

User-specified spec (2026-09-01), implemented literally -- no filters or
rules added beyond what was given. Two open ambiguities in the user's own
spec are resolved by testing BOTH variants, not by picking one silently:

  1. Stop-loss placement: the spec says "Entry -> OR Low" first, then "or
     alternatively ... the 0.5R/0.5 range level ... should be precisely
     mathematically tested", and clarifies that "0.5 of the M15 range"
     means `OR High - 0.5*(OR High - OR Low)` (the OR midpoint). Both are
     run: `--stop-mode full` (OR Low/High) and `--stop-mode half` (OR
     midpoint) -- see `--stop-mode` below.
  2. No entry-window end time is given (unlike this repo's other ORB
     strategies) -- the M1 breakout scan runs from 09:45 through the rest
     of the NY calendar day, uncapped, exactly as specified. Do not add a
     cutoff without the user asking.

Mechanics:
  - Opening Range = the single 09:30-09:45 NY 15-minute candle (High/Low).
  - From 09:45, scan M1 bars (this NY calendar day only) for the FIRST one
    whose CLOSE is outside the OR (> OR High = bullish, < OR Low =
    bearish). Whichever direction triggers first is the ONLY trade for the
    day (spec: max 1 long OR 1 short per day, first valid breakout wins).
  - No lookahead: the OR's High/Low is only used from 09:45 onward (the
    bar that produced it has already closed by then); the breakout is only
    known once an M1 bar CLOSES outside the range, so entry fills at the
    NEXT M1 bar's open (matches this repo's real MARKET-order convention,
    execution/fill_simulator.py) -- never at the confirming bar's own
    close.
  - SL: `full` -> OR Low (long) / OR High (short). `half` -> OR midpoint
    (OR High - 0.5*range for long, OR Low + 0.5*range for short).
  - TP = entry +/- 2 * R, R = |entry - SL|.
  - One open position at a time; no new day's breakout is evaluated while
    a prior day's trade is still open. SL/TP never adjusted after entry.
    No EOD force-close (not specified) -- a trade runs until its real
    SL/TP is hit, however many days that takes.
  - Optional spread cost (round-trip points, net of R) -- see
    --spread-points; 0 = gross (no cost).

Data note: this account has no real NQ/MNQ futures history -- NAS100 (the
CFD index this whole repo already uses for "NASDAQ") is used instead. See
module docstring context in the conversation for why.

Usage:
    python -m scripts.nasdaq_orb_m1_breakout_backtest --stop-mode full
    python -m scripts.nasdaq_orb_m1_breakout_backtest --stop-mode half --spread-points 3.0
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, time as dtime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.backtest_common import NY, load_m1, resample

OR_START = dtime(9, 30)
SCAN_START = dtime(9, 45)
TP_R = 2.0


@dataclass
class Trade:
    day: str
    direction: str  # "LONG" | "SHORT"
    or_high: float
    or_low: float
    breakout_time: object
    entry_time: object
    entry_price: float
    stop: float
    target: float
    exit_time: object
    exit_price: float
    exit_reason: str  # "TP" | "SL"
    r_multiple: float


def run_backtest(input_csv: str, stop_mode: str, spread_points: float, tp_r: float = TP_R, direction: str = "both") -> list[Trade]:
    m1 = load_m1(input_csv)
    m15 = resample(m1, 15)
    m15.index = m15.index.tz_convert(NY)
    m1_ny = m1.copy()
    m1_ny.index = m1_ny.index.tz_convert(NY)

    o1, h1, l1, c1 = (m1_ny[c].to_numpy() for c in ("open", "high", "low", "close"))
    idx1 = m1_ny.index
    n1 = len(m1_ny)
    dates1 = idx1.date
    times1 = idx1.time

    or_by_day: dict[date, tuple[float, float]] = {}
    for ts, row in m15.iterrows():
        if ts.time() == OR_START:
            or_by_day[ts.date()] = (float(row.high), float(row.low))

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = pos_entry_price = None
    pos_entry_time = pos_or_high = pos_or_low = pos_breakout_time = None

    current_day: date | None = None
    day_or: tuple[float, float] | None = None
    day_trade_taken = False

    i = 0
    while i < n1:
        d, t = dates1[i], times1[i]

        if d != current_day:
            current_day = d
            day_or = or_by_day.get(d)
            day_trade_taken = False

        # --- manage an open position on every bar ---
        if in_position:
            hit_sl = (l1[i] <= pos_sl) if pos_dir == "LONG" else (h1[i] >= pos_sl)
            hit_tp = (h1[i] >= pos_tp) if pos_dir == "LONG" else (l1[i] <= pos_tp)
            if hit_sl or hit_tp:
                exit_price, exit_reason = (pos_sl, "SL") if hit_sl else (pos_tp, "TP")
                risk = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                cost_r = spread_points / risk if risk > 0 else 0.0
                r_mult = move / risk - cost_r
                trades.append(Trade(
                    str(pos_entry_time.date()), pos_dir, pos_or_high, pos_or_low, pos_breakout_time,
                    pos_entry_time, pos_entry_price, pos_sl, pos_tp, idx1[i], exit_price, exit_reason,
                    round(r_mult, 4),
                ))
                in_position = False
            i += 1
            continue

        if day_or is None or day_trade_taken or t < SCAN_START:
            i += 1
            continue

        or_high, or_low = day_or
        bullish = c1[i] > or_high and direction in ("both", "long")
        bearish = c1[i] < or_low and direction in ("both", "short")
        if not (bullish or bearish):
            i += 1
            continue

        if i + 1 >= n1 or dates1[i + 1] != d:
            # no next bar (or next bar rolls into a new day) to fill a real market entry on
            day_trade_taken = True
            i += 1
            continue

        trade_dir = "LONG" if bullish else "SHORT"
        entry_price = float(o1[i + 1])
        if stop_mode == "full":
            sl = or_low if trade_dir == "LONG" else or_high
        else:  # "half" -- OR midpoint
            mid = or_high - 0.5 * (or_high - or_low)
            sl = mid
        risk = abs(entry_price - sl)
        day_trade_taken = True
        if risk <= 0:
            i += 1
            continue

        tp = entry_price + tp_r * risk if trade_dir == "LONG" else entry_price - tp_r * risk
        in_position = True
        pos_dir, pos_sl, pos_tp = trade_dir, sl, tp
        pos_entry_price, pos_entry_time = entry_price, idx1[i + 1]
        pos_or_high, pos_or_low, pos_breakout_time = or_high, or_low, idx1[i]
        i += 1

    return trades


def _is_fvg(prev2_high: float, cur_low: float) -> bool:
    """Pure 3-candle FVG geometry (bar[i-2].high vs bar[i].low), no
    displacement/strength filter -- the user asked for "the first FVG",
    not "the first strong/displacement FVG", so none is added here."""
    return cur_low > prev2_high


def run_backtest_fvg_retest(input_csv: str, spread_points: float, tp_r: float = TP_R, direction: str = "both") -> list[Trade]:
    """2026-09-01 clarification: breakout of the OR is no longer itself the
    entry -- it only sets DIRECTION. Entry is the first touch of the first
    M1 FVG that forms outside the OR (in the breakout direction); this
    doubles as the "retest of the broken OR High/Low" the user described,
    since a fresh FVG typically sits close to the level that was just
    broken. Stop = the low (long) / high (short) of the FVG's own middle
    (displacement) candle -- "the candle that created the FVG" -- no ATR
    buffer added, since none was specified.
    """
    m1 = load_m1(input_csv)
    m15 = resample(m1, 15)
    m15.index = m15.index.tz_convert(NY)
    m1_ny = m1.copy()
    m1_ny.index = m1_ny.index.tz_convert(NY)

    o1, h1, l1, c1 = (m1_ny[c].to_numpy() for c in ("open", "high", "low", "close"))
    idx1 = m1_ny.index
    n1 = len(m1_ny)
    dates1 = idx1.date
    times1 = idx1.time

    or_by_day: dict[date, tuple[float, float]] = {}
    for ts, row in m15.iterrows():
        if ts.time() == OR_START:
            or_by_day[ts.date()] = (float(row.high), float(row.low))

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = pos_entry_price = None
    pos_entry_time = pos_or_high = pos_or_low = pos_breakout_time = None

    current_day: date | None = None
    day_or: tuple[float, float] | None = None
    day_trade_taken = False
    # per-day breakout/FVG-hunt state
    breakout_dir: str | None = None       # None | "LONG" | "SHORT"
    breakout_time = None
    fvg_low = fvg_high = fvg_stop = None  # set once the qualifying FVG is found

    i = 0
    while i < n1:
        d, t = dates1[i], times1[i]

        if d != current_day:
            current_day = d
            day_or = or_by_day.get(d)
            day_trade_taken = False
            breakout_dir = None
            breakout_time = None
            fvg_low = fvg_high = fvg_stop = None

        if in_position:
            hit_sl = (l1[i] <= pos_sl) if pos_dir == "LONG" else (h1[i] >= pos_sl)
            hit_tp = (h1[i] >= pos_tp) if pos_dir == "LONG" else (l1[i] <= pos_tp)
            if hit_sl or hit_tp:
                exit_price, exit_reason = (pos_sl, "SL") if hit_sl else (pos_tp, "TP")
                risk = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                cost_r = spread_points / risk if risk > 0 else 0.0
                r_mult = move / risk - cost_r
                trades.append(Trade(
                    str(pos_entry_time.date()), pos_dir, pos_or_high, pos_or_low, pos_breakout_time,
                    pos_entry_time, pos_entry_price, pos_sl, pos_tp, idx1[i], exit_price, exit_reason,
                    round(r_mult, 4),
                ))
                in_position = False
            i += 1
            continue

        if day_or is None or day_trade_taken or t < SCAN_START:
            i += 1
            continue

        or_high, or_low = day_or

        # Stage 1: find breakout direction (first M1 close outside the OR)
        if breakout_dir is None:
            if c1[i] > or_high and direction in ("both", "long"):
                breakout_dir, breakout_time = "LONG", idx1[i]
            elif c1[i] < or_low and direction in ("both", "short"):
                breakout_dir, breakout_time = "SHORT", idx1[i]
            i += 1
            continue

        # Stage 2: hunt for the first qualifying FVG outside the OR, then its first retest touch
        if fvg_stop is None:
            if i >= 2:
                if breakout_dir == "LONG" and l1[i] > or_high and _is_fvg(h1[i - 2], l1[i]):
                    fvg_low, fvg_stop = float(l1[i]), float(l1[i - 1])  # near edge, displacement candle's low
                elif breakout_dir == "SHORT" and h1[i] < or_low and l1[i - 2] > h1[i]:
                    fvg_high, fvg_stop = float(h1[i]), float(h1[i - 1])  # near edge, displacement candle's high
            i += 1
            continue

        # Stage 3: wait for first retest touch into the FVG zone
        if breakout_dir == "LONG":
            touched = l1[i] <= fvg_low
        else:
            touched = h1[i] >= fvg_high

        if not touched:
            i += 1
            continue

        entry_price = fvg_low if breakout_dir == "LONG" else fvg_high
        sl = fvg_stop
        risk = abs(entry_price - sl)
        day_trade_taken = True
        if risk <= 0:
            i += 1
            continue
        tp = entry_price + tp_r * risk if breakout_dir == "LONG" else entry_price - tp_r * risk
        in_position = True
        pos_dir, pos_sl, pos_tp = breakout_dir, sl, tp
        pos_entry_price, pos_entry_time = entry_price, idx1[i]
        pos_or_high, pos_or_low, pos_breakout_time = or_high, or_low, breakout_time
        i += 1

    return trades


def run_backtest_break_retest(input_csv: str, spread_points: float, tp_r: float = TP_R, direction: str = "both") -> list[Trade]:
    """2026-09-01 "Variant A -- Break + Retest": breakout is direction-only
    (no entry), then wait for price to come back and RETEST the broken OR
    level -- confirmed once a bar dips to/through it and CLOSES back on the
    breakout side (the level "holds" as new support/resistance). Entry =
    next bar's open after that confirming close (no lookahead, matches this
    script's other variants). Stop = the lowest low (long) / highest high
    (short) seen from the breakout bar through the retest-confirming bar
    inclusive -- "the retest's swing low/high", not just one candle's
    extreme, since a pullback can take more than one bar. No displacement
    filter on the breakout itself (not specified -- a plain close beyond
    the OR is enough, same as --stop-mode full's breakout definition).
    """
    m1 = load_m1(input_csv)
    m15 = resample(m1, 15)
    m15.index = m15.index.tz_convert(NY)
    m1_ny = m1.copy()
    m1_ny.index = m1_ny.index.tz_convert(NY)

    o1, h1, l1, c1 = (m1_ny[c].to_numpy() for c in ("open", "high", "low", "close"))
    idx1 = m1_ny.index
    n1 = len(m1_ny)
    dates1 = idx1.date
    times1 = idx1.time

    or_by_day: dict[date, tuple[float, float]] = {}
    for ts, row in m15.iterrows():
        if ts.time() == OR_START:
            or_by_day[ts.date()] = (float(row.high), float(row.low))

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = pos_entry_price = None
    pos_entry_time = pos_or_high = pos_or_low = pos_breakout_time = None

    current_day: date | None = None
    day_or: tuple[float, float] | None = None
    day_trade_taken = False
    breakout_dir: str | None = None
    breakout_time = None
    swing_extreme: float | None = None  # running lowest-low (long) / highest-high (short) since breakout

    i = 0
    while i < n1:
        d, t = dates1[i], times1[i]

        if d != current_day:
            current_day = d
            day_or = or_by_day.get(d)
            day_trade_taken = False
            breakout_dir = None
            breakout_time = None
            swing_extreme = None

        if in_position:
            hit_sl = (l1[i] <= pos_sl) if pos_dir == "LONG" else (h1[i] >= pos_sl)
            hit_tp = (h1[i] >= pos_tp) if pos_dir == "LONG" else (l1[i] <= pos_tp)
            if hit_sl or hit_tp:
                exit_price, exit_reason = (pos_sl, "SL") if hit_sl else (pos_tp, "TP")
                risk = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                cost_r = spread_points / risk if risk > 0 else 0.0
                r_mult = move / risk - cost_r
                trades.append(Trade(
                    str(pos_entry_time.date()), pos_dir, pos_or_high, pos_or_low, pos_breakout_time,
                    pos_entry_time, pos_entry_price, pos_sl, pos_tp, idx1[i], exit_price, exit_reason,
                    round(r_mult, 4),
                ))
                in_position = False
            i += 1
            continue

        if day_or is None or day_trade_taken or t < SCAN_START:
            i += 1
            continue

        or_high, or_low = day_or

        # Stage 1: breakout (direction only, no entry)
        if breakout_dir is None:
            if c1[i] > or_high and direction in ("both", "long"):
                breakout_dir, breakout_time, swing_extreme = "LONG", idx1[i], float(l1[i])
            elif c1[i] < or_low and direction in ("both", "short"):
                breakout_dir, breakout_time, swing_extreme = "SHORT", idx1[i], float(h1[i])
            i += 1
            continue

        # Stage 2: wait for the retest to CONFIRM (touch the level, close back on breakout side)
        if breakout_dir == "LONG":
            swing_extreme = min(swing_extreme, float(l1[i]))
        else:
            swing_extreme = max(swing_extreme, float(h1[i]))

        confirmed = (
            (l1[i] <= or_high and c1[i] >= or_high) if breakout_dir == "LONG"
            else (h1[i] >= or_low and c1[i] <= or_low)
        )
        if not confirmed:
            i += 1
            continue

        if i + 1 >= n1 or dates1[i + 1] != d:
            day_trade_taken = True
            i += 1
            continue

        entry_price = float(o1[i + 1])
        sl = swing_extreme
        risk = abs(entry_price - sl)
        day_trade_taken = True
        if risk <= 0:
            i += 1
            continue
        tp = entry_price + tp_r * risk if breakout_dir == "LONG" else entry_price - tp_r * risk
        in_position = True
        pos_dir, pos_sl, pos_tp = breakout_dir, sl, tp
        pos_entry_price, pos_entry_time = entry_price, idx1[i + 1]
        pos_or_high, pos_or_low, pos_breakout_time = or_high, or_low, breakout_time
        i += 1

    return trades


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "direction", "or_high", "or_low", "breakout_time", "entry_time", "entry_price",
                    "stop", "target", "exit_time", "exit_price", "exit_reason", "r_multiple"])
        for t in trades:
            w.writerow([t.day, t.direction, t.or_high, t.or_low, t.breakout_time, t.entry_time, t.entry_price,
                        t.stop, t.target, t.exit_time, t.exit_price, t.exit_reason, t.r_multiple])


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    total_r = sum(t.r_multiple for t in trades)
    return {
        "trades": n, "win_rate": round(wins / n * 100, 1), "profit_factor": round(pf, 3),
        "total_r": round(total_r, 2), "avg_r": round(total_r / n, 3),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", default="data/history/NAS100_M1.csv")
    parser.add_argument("--stop-mode", choices=["full", "half", "fvg_retest", "break_retest"], default="full",
                         help="full = OR Low/High as stop; half = OR midpoint; "
                              "fvg_retest = breakout sets direction only, entry = first touch of the first FVG outside the OR; "
                              "break_retest = 'Variant A' (2026-09-01): breakout sets direction only, entry = next "
                              "bar's open after the OR level is retested and holds, stop = the retest's swing low/high")
    parser.add_argument("--spread-points", type=float, default=0.0)
    parser.add_argument("--tp-r", type=float, default=TP_R, help=f"take-profit target as a multiple of risk (default {TP_R})")
    parser.add_argument("--direction", choices=["both", "long", "short"], default="both",
                         help="restrict which breakout direction is even considered -- 'long' does NOT just filter "
                              "the both-direction trade log to LONG rows; a day where a SHORT would have fired first "
                              "stays open for a later LONG breakout instead of being blocked")
    args = parser.parse_args()

    if args.stop_mode == "fvg_retest":
        trades = run_backtest_fvg_retest(args.input_csv, args.spread_points, args.tp_r, args.direction)
    elif args.stop_mode == "break_retest":
        trades = run_backtest_break_retest(args.input_csv, args.spread_points, args.tp_r, args.direction)
    else:
        trades = run_backtest(args.input_csv, args.stop_mode, args.spread_points, args.tp_r, args.direction)
    sym_tag = Path(args.input_csv).stem.replace("_M1", "").lower()
    tp_suffix = "" if args.tp_r == TP_R else f"_{args.tp_r:g}R"
    dir_suffix = "" if args.direction == "both" else f"_{args.direction}"
    out_path = f"artifacts/nasdaq_orb_m1_breakout_{sym_tag}_{args.stop_mode}{tp_suffix}{dir_suffix}_trades.csv"
    Path("artifacts").mkdir(exist_ok=True)
    write_trades_csv(trades, out_path)
    print(f"stop_mode={args.stop_mode} spread={args.spread_points}")
    print(f"result = {summarize(trades)}")
    print(f"Trade log written to {out_path}")

    if trades:
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        print(f"  LONG  n={len(longs)}  {summarize(longs)}")
        print(f"  SHORT n={len(shorts)}  {summarize(shorts)}")
