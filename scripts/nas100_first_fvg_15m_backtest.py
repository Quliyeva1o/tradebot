"""NAS100 15m "First FVG after 9:30 NY" backtest.

Spec (as given by the user, 2026-08-27):
  - Timeframe: 15m only.
  - Each NY trading day, scan 15m bars starting at 09:30 NY. The FIRST Fair
    Value Gap (classic 3-candle imbalance) to appear is the day's only setup
    -- later FVGs the same day are ignored.
  - Entry: the first later touch of that FVG zone (price trading back into
    the gap). Entry price = the near edge of the zone (the first point
    price actually reaches), not the midpoint.
  - Stop: the BODY of the candle that created the FVG -- read as the middle
    ("displacement") candle of the 3-candle pattern, the one whose range
    both other candles fail to overlap. Bullish -> body low (min(open,
    close)); bearish -> body high (max(open, close)).
  - Target: 2R, R = |entry - stop|.

Assumptions made where the spec was silent (flagged here rather than
guessed silently):
  1. The "candle that created the FVG" is the MIDDLE candle of the 3-candle
     pattern (the displacement leg), not the entry-touch candle -- this is
     the standard ICT reading and the only candle common to both gap edges.
  2. Entry search is bounded to the SAME NY calendar day as the FVG. If the
     zone is never touched before the session's last bar, the day produces
     no trade. An intraday opening-range setup that can trigger a week
     later would not be the same strategy.
  3. Within any bar that could satisfy both SL and TP, SL is checked first
     (the conservative assumption already used by every other backtest in
     this repo, e.g. scripts/order_flow_daily_bias_backtest.py). Same-bar
     stop-outs on the ENTRY bar itself are checked explicitly -- this repo's
     own audit history (see SESSION_HANDOFF.md #2.2) found that skipping
     that check silently manufactures winners.
  4. The spec describes the bullish case explicitly ("əgər fvg
     bullishdirsə..."); the bearish case is implemented as the mirror image
     so the strategy is not silently long-only. Set --long-only to restrict
     to the literal bullish-only spec.
  5. No spread/commission is deducted (matches every other backtest script
     in this repo -- see SESSION_HANDOFF.md #3.1, spread is a known,
     currently-unhandled cost across the whole codebase, not specific to
     this script).

Usage:
    python -m scripts.nas100_first_fvg_15m_backtest --input-csv data/history_fresh/USTEC_M15.csv
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass

import numpy as np
import pandas as pd

from scripts.backtest_common import NY, load_m1
from scripts.po3_backtest import compute_daily_bias, compute_daily_levels

STARTING_BALANCE = 100_000.0
RISK_PCT = 0.01
TP_R = 2.0  # overridable via run_backtest(..., tp_r=...) / --tp-r
SESSION_START = (9, 30)  # NY


@dataclass
class Trade:
    day: str
    direction: str
    fvg_confirm_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float


def find_first_fvg(day_bars: pd.DataFrame) -> dict | None:
    """First 3-candle FVG confirmed using only bars at/after 09:30.

    day_bars must already be sliced to bars with time >= 09:30 for one NY
    calendar day, in chronological order. Returns None if no FVG forms
    before the session's bars run out.
    """
    h = day_bars["high"].to_numpy()
    l = day_bars["low"].to_numpy()
    o = day_bars["open"].to_numpy()
    c = day_bars["close"].to_numpy()
    idx = day_bars.index
    n = len(day_bars)

    for i in range(2, n):
        if l[i] > h[i - 2]:
            direction = "LONG"
            zone_top, zone_bottom = l[i], h[i - 2]
            mid_body_lo, mid_body_hi = min(o[i - 1], c[i - 1]), max(o[i - 1], c[i - 1])
            return {
                "direction": direction,
                "confirm_i": i,
                "confirm_time": idx[i],
                "zone_top": zone_top,
                "zone_bottom": zone_bottom,
                "stop": mid_body_lo,
                "mid_body_hi": mid_body_hi,
            }
        if h[i] < l[i - 2]:
            direction = "SHORT"
            zone_top, zone_bottom = l[i - 2], h[i]
            mid_body_lo, mid_body_hi = min(o[i - 1], c[i - 1]), max(o[i - 1], c[i - 1])
            return {
                "direction": direction,
                "confirm_i": i,
                "confirm_time": idx[i],
                "zone_top": zone_top,
                "zone_bottom": zone_bottom,
                "stop": mid_body_hi,
                "mid_body_lo": mid_body_lo,
            }
    return None


def simulate_trade(day_bars: pd.DataFrame, fvg: dict, long_only: bool, tp_r: float = TP_R) -> Trade | None:
    direction = fvg["direction"]
    if long_only and direction == "SHORT":
        return None

    h = day_bars["high"].to_numpy()
    l = day_bars["low"].to_numpy()
    idx = day_bars.index
    n = len(day_bars)
    start = fvg["confirm_i"] + 1
    zone_top, zone_bottom = fvg["zone_top"], fvg["zone_bottom"]
    stop = fvg["stop"]

    entry_i = None
    entry_price = None
    for j in range(start, n):
        if direction == "LONG":
            # Price falls back into the gap from above; first contact is
            # the TOP of the zone (the near edge on approach from above).
            if l[j] <= zone_top:
                entry_i = j
                entry_price = min(zone_top, h[j])  # gap may open below the edge
                break
        else:
            # Price rises back into the gap from below; first contact is
            # the BOTTOM of the zone.
            if h[j] >= zone_bottom:
                entry_i = j
                entry_price = max(zone_bottom, l[j])
                break

    if entry_i is None:
        return None

    risk = abs(entry_price - stop)
    if risk <= 0:
        return None
    target = entry_price + tp_r * risk if direction == "LONG" else entry_price - tp_r * risk

    # Same-bar stop-out check on the ENTRY bar itself (see assumption #3).
    exit_i = exit_price = exit_reason = None
    if direction == "LONG":
        if l[entry_i] <= stop:
            exit_i, exit_price, exit_reason = entry_i, stop, "SL"
        elif h[entry_i] >= target:
            exit_i, exit_price, exit_reason = entry_i, target, "TP"
    else:
        if h[entry_i] >= stop:
            exit_i, exit_price, exit_reason = entry_i, stop, "SL"
        elif l[entry_i] <= target:
            exit_i, exit_price, exit_reason = entry_i, target, "TP"

    if exit_i is None:
        for j in range(entry_i + 1, n):
            if direction == "LONG":
                hit_sl = l[j] <= stop
                hit_tp = h[j] >= target
            else:
                hit_sl = h[j] >= stop
                hit_tp = l[j] <= target
            if hit_sl:
                exit_i, exit_price, exit_reason = j, stop, "SL"
                break
            if hit_tp:
                exit_i, exit_price, exit_reason = j, target, "TP"
                break

    if exit_i is None:
        # Session ran out with the trade still open -- close at the last
        # bar's close (EOD flat), never fabricate a fill beyond the data.
        exit_i = n - 1
        exit_price = day_bars["close"].iloc[-1]
        exit_reason = "EOD"

    r_multiple = (
        (exit_price - entry_price) / risk if direction == "LONG"
        else (entry_price - exit_price) / risk
    )
    pnl_usd = r_multiple * STARTING_BALANCE * RISK_PCT

    return Trade(
        day=str(idx[0].date()),
        direction=direction,
        fvg_confirm_time=fvg["confirm_time"],
        entry_time=idx[entry_i],
        entry_price=entry_price,
        stop=stop,
        target=target,
        exit_time=idx[exit_i],
        exit_price=exit_price,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
        pnl_usd=pnl_usd,
    )


def run_backtest(
    input_csv: str, long_only: bool = False, tp_r: float = TP_R, bias_filter: bool = False
) -> tuple[list[Trade], dict]:
    """
    bias_filter: when True, a day's FVG is only tradeable if its direction
    agrees with the HTF Daily Bias at the moment the FVG confirms (LONG
    needs Bullish, SHORT needs Bearish; Neutral or disagreement -> skipped).

    Bias = scripts.po3_backtest.compute_daily_bias, reused as-is rather than
    reimplemented: 1H swing structure vote + PDH/PDL-mid discount/premium
    vote, both must agree, and already lookahead-safe (re-stamped to each 1H
    bar's CLOSE via htf_bias_to_index -- see scripts/backtest_common.py).
    That function only cares about OHLCV + a DatetimeIndex, so feeding it
    M15 bars instead of its usual M1 input is valid: it resamples to 1H and
    1D internally regardless of the input's own bar size.
    """
    m1 = load_m1(input_csv)
    bars = m1  # already 15m bars, no resampling needed
    bars.index = bars.index.tz_convert(NY) if bars.index.tz is not None else bars.index

    bias_m15 = None
    if bias_filter:
        levels = compute_daily_levels(bars)
        bias_m15 = compute_daily_bias(bars, levels)

    funnel = {"days_scanned": 0, "days_with_930_bar": 0, "days_with_fvg": 0,
              "days_bullish": 0, "days_bearish": 0, "days_bias_rejected": 0,
              "days_triggered": 0, "days_untriggered": 0}

    trades: list[Trade] = []
    day_keys = pd.Series(bars.index.date, index=bars.index)
    for day, day_df in bars.groupby(day_keys):
        funnel["days_scanned"] += 1
        session = day_df[(day_df.index.hour > SESSION_START[0]) |
                          ((day_df.index.hour == SESSION_START[0]) & (day_df.index.minute >= SESSION_START[1]))]
        if session.empty or session.index[0].hour != SESSION_START[0] or session.index[0].minute != SESSION_START[1]:
            continue  # no clean 09:30 bar that day (holiday/partial session/gap)
        funnel["days_with_930_bar"] += 1

        fvg = find_first_fvg(session)
        if fvg is None:
            continue
        funnel["days_with_fvg"] += 1
        funnel["days_bullish" if fvg["direction"] == "LONG" else "days_bearish"] += 1

        if bias_filter:
            bias_val = int(bias_m15.loc[fvg["confirm_time"]])
            wants = 1 if fvg["direction"] == "LONG" else -1
            if bias_val != wants:
                funnel["days_bias_rejected"] += 1
                continue

        trade = simulate_trade(session, fvg, long_only, tp_r)
        if trade is None:
            funnel["days_untriggered"] += 1
            continue
        funnel["days_triggered"] += 1
        trades.append(trade)

    return trades, funnel


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "direction", "fvg_confirm_time", "entry_time", "entry_price", "stop",
                    "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "pnl_usd"])
        for t in trades:
            w.writerow([t.day, t.direction, t.fvg_confirm_time, t.entry_time, t.entry_price, t.stop,
                        t.target, t.exit_time, t.exit_price, t.exit_reason,
                        round(t.r_multiple, 4), round(t.pnl_usd, 2)])


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "total_r": 0.0, "avg_r": None, "net_pnl": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    total_r = sum(t.r_multiple for t in trades)
    return {
        "trades": n,
        "win_rate": round(wins / n * 100, 1),
        "profit_factor": round(pf, 3),
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / n, 3),
        "net_pnl": round(sum(t.pnl_usd for t in trades), 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", default="data/history_fresh/USTEC_M15.csv")
    parser.add_argument("--long-only", action="store_true", help="restrict to the literal bullish-only spec")
    parser.add_argument("--tp-r", type=float, default=TP_R, help="take-profit target in R multiples")
    parser.add_argument("--bias-filter", action="store_true", help="only trade FVGs that agree with the HTF Daily Bias")
    args = parser.parse_args()

    trades, funnel = run_backtest(args.input_csv, args.long_only, args.tp_r, args.bias_filter)
    out_path = "artifacts/nas100_first_fvg_15m_trades.csv"
    write_trades_csv(trades, out_path)
    stats = summarize(trades)

    print(f"funnel = {funnel}")
    print(f"result = {stats}")
    print(f"Trade log written to {out_path}")

    if trades:
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        print(f"  LONG  n={len(longs)}  {summarize(longs)}")
        print(f"  SHORT n={len(shorts)}  {summarize(shorts)}")
