"""XAUUSD 09:30 Opening-Range Breakout + Liquidity-Sweep backtest.

Ports the user's full written spec (2026-08-31) to a mechanical, no-discretion
state machine. Every entry is decided purely from OHLC of closed M5 bars --
there is no "this looks like a breakout" judgement call anywhere.

Spec summary (see the user's message for the full text):
  - Symbol/TF: XAUUSD, M5 (resampled from M1 -- no native M5 file in
    data/history/, matches every other script in this repo).
  - Session: New York, DST-aware (backtest_common.NY = zoneinfo, not a fixed
    UTC offset -- exactly the pitfall the spec calls out).
  - Opening Range = the single 09:30-09:35 M5 candle's high/low.
  - Entry window: 09:35-10:00 NY (bars labelled 09:35/09:40/09:45/09:50/09:55
    -- 5 bars). No NEW setup may start outside this window; a trade already
    open when 10:00 passes keeps running (the spec only forbids NEW entries
    after 10:00, it does not say to flatten open trades).
  - Setup A (default/primary per the user: "B variantını əsas strategiya
    edərdim" -- conservative retest, not the aggressive immediate-breakout
    entry): OR High/Low broken with a BODY close beyond the range plus a
    displacement candle, then a later bar dips back to touch the broken
    level and closes back on the breakout side (retest holds) -> entry at
    that bar's close.
  - Setup B: OR Low/High swept (wick through, close back inside) then a
    displacement candle opens a same-direction 3-candle FVG, then a later
    bar retests the FVG -> entry at that bar's close.
  - SL: retest/sweep bar's extreme +/- an ATR buffer (spec: "retest low -
    kiçik buffer" / "sweep wick-in altında"). ATR-scaled rather than a fixed
    $ buffer since XAUUSD's own volatility varies year to year.
  - TP: fixed 2R (spec explicitly prefers this for the first backtest pass
    over a discretionary liquidity target: "backtest zamanı qaydanı
    dəyişməmək üçün əvvəlcə sabit 2R test etmək daha düzgündür").
  - Max 2 trades/day, each of the 4 setup-types (buy/sell x breakout/
    reversal) fires at most ONCE per day -- this structurally satisfies the
    spec's "eyni səviyyədə revenge trade qadağandır" rule, since a stopped-
    out breakout can never re-arm the same level the same day.
  - Risk: position sizing is `risk_pct` of STARTING_BALANCE (fixed-$ risk
    per trade, matching every other script's convention in this repo, e.g.
    scripts/nas100_first_fvg_15m_backtest.py) -- NOT compounding balance.

Explicitly NOT implemented (flagged rather than silently skipped):
  - News filter (CPI/NFP/FOMC). No economic-calendar data exists anywhere in
    this repo (grepped for CPI/NFP/FOMC across the whole codebase -- zero
    hits) and none was provided, so this cannot be backtested faithfully.
    `--news-filter` would either need external calendar data wired in, or
    silently do nothing while claiming to filter -- worse than omitting it.
    Treat these results as a ceiling that live news-driven whipsaws would
    likely erode.
  - The spec's optional "London High / pre-market liquidity" TP override
    (section 5) -- fixed 2R is used per the spec's own stated preference.
  - The spec's "liquidity proximity" pre-filter (section 9: skip if OR High
    sits within $5 of London High etc.) -- no session-level extremes are
    computed here. Worth adding as a follow-up filter once this base version
    is validated, not baked in blind.

Usage:
    python -m scripts.xauusd_orb_liquidity_sweep_backtest
    python -m scripts.xauusd_orb_liquidity_sweep_backtest --risk-pct 0.01 --spread-points 0.39
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import time as dtime

import numpy as np
import pandas as pd

from scripts.backtest_common import NY, compute_atr, load_m1, resample

STARTING_BALANCE = 100_000.0
RISK_PCT = 0.005          # 0.5% -- spec's lower bound of "0.5-1%"; override with --risk-pct
TP_R = 2.0                # fixed 2R per the spec's own stated preference (section 5)
MAX_TRADES_PER_DAY = 2

OR_START = dtime(9, 30)
ENTRY_WINDOW_START = dtime(9, 35)
ENTRY_WINDOW_END = dtime(10, 0)

ATR_LEN = 14
DISPLACEMENT_ATR_MULT = 1.2   # min candle range, as an ATR multiple, to count as "displacement"
FVG_MIN_GAP_ATR = 0.05         # ATR-scaled min 3-candle gap (same convention as scripts/po3_backtest.py)
SL_BUFFER_ATR = 0.1            # buffer beyond retest/sweep extreme, ATR-scaled
MAX_RISK_ATR_MULT = 3.0        # spec: "SL həddindən artıq böyükdürsə -> trade yoxdur"
REVERSAL_LOOKBACK_BARS = 4     # sweep -> displacement/FVG must complete within this many bars (window is only 5 bars wide)


@dataclass
class Trade:
    day: str
    setup_type: str       # "breakout" | "reversal"
    direction: str         # "LONG" | "SHORT"
    signal_time: pd.Timestamp   # breakout-confirm bar (setup A) or sweep bar (setup B)
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float


def _is_displacement(o: float, h: float, l: float, c: float, atr_val: float, bullish: bool) -> bool:
    if np.isnan(atr_val) or atr_val <= 0:
        return False
    rng = h - l
    if rng <= 0 or rng < DISPLACEMENT_ATR_MULT * atr_val:
        return False
    if abs(c - o) < 0.5 * rng:
        return False
    return (c > o) if bullish else (c < o)


def run_backtest(
    input_csv: str,
    risk_pct: float = RISK_PCT,
    tp_r: float = TP_R,
    max_trades_per_day: int = MAX_TRADES_PER_DAY,
    displacement_atr_mult: float = DISPLACEMENT_ATR_MULT,
    fvg_min_gap_atr: float = FVG_MIN_GAP_ATR,
    sl_buffer_atr: float = SL_BUFFER_ATR,
    max_risk_atr_mult: float = MAX_RISK_ATR_MULT,
    spread_points: float = 0.0,
    reversal_tp_mode: str = "fixed",  # "fixed" (tp_r) or "liquidity" (opposite OR boundary, floored at tp_r -- spec section 7)
    enable_breakout: bool = True,  # False isolates Setup B exactly as strategy/xauusd_orb_liquidity_sweep.py runs it (that class implements Setup B only -- Setup A was found net-losing and excluded)
    bar_minutes: int = 5,  # OR candle size; entry window auto-derives from this (see entry_window_end)
    entry_window_end: dtime = ENTRY_WINDOW_END,  # NY local time after which no NEW setup may start
    entry_fill_mode: str = "zone_edge",  # "zone_edge" (idealized: fills at the FVG's near edge, as if a resting limit order) or "next_open" (realistic: TradeManager places a MARKET order only once the signal bar closes, so the live/paper fill is next_bar.open -- see execution/fill_simulator.py)
) -> tuple[list[Trade], dict]:
    entry_window_start = (
        dtime(OR_START.hour, OR_START.minute + bar_minutes) if OR_START.minute + bar_minutes < 60
        else dtime(OR_START.hour + 1, OR_START.minute + bar_minutes - 60)
    )
    m1 = load_m1(input_csv)
    bars = resample(m1, bar_minutes)
    bars.index = bars.index.tz_convert(NY)

    o, h, l, c = (bars[col].to_numpy() for col in ("open", "high", "low", "close"))
    idx = bars.index
    n = len(bars)
    atr = compute_atr(bars, ATR_LEN).to_numpy()
    dates = idx.date
    times = idx.time

    funnel = {
        "bars_scanned": n, "days_with_or_bar": 0,
        "buy_breakout_confirmed": 0, "buy_breakout_retest_ok": 0, "buy_breakout_invalidated": 0,
        "sell_breakout_confirmed": 0, "sell_breakout_retest_ok": 0, "sell_breakout_invalidated": 0,
        "buy_sweep_detected": 0, "buy_reversal_fvg_ok": 0,
        "sell_sweep_detected": 0, "sell_reversal_fvg_ok": 0,
        "trades_skipped_bad_risk": 0, "trades_taken": 0,
    }

    trades: list[Trade] = []

    current_date = None
    or_high = or_low = None
    trades_today = 0
    setup_used = {"buy_breakout": False, "sell_breakout": False, "buy_reversal": False, "sell_reversal": False}
    breakout_up_i = breakout_down_i = None
    sweep_down_i = sweep_down_low = None
    sweep_up_i = sweep_up_high = None

    in_position = False
    pos_dir = pos_sl = pos_tp = pos_entry_price = None
    pos_entry_time = pos_setup_type = pos_signal_time = None

    def open_trade(
        direction: str, setup_type: str, signal_i: int, entry_i: int, entry_price: float, sl: float,
        liquidity_target: float | None = None,
    ) -> bool:
        nonlocal in_position, pos_dir, pos_sl, pos_tp, pos_entry_price, pos_entry_time, pos_setup_type, pos_signal_time, trades_today
        risk = abs(entry_price - sl)
        if risk <= 0 or np.isnan(atr[entry_i]) or risk > max_risk_atr_mult * atr[entry_i]:
            funnel["trades_skipped_bad_risk"] += 1
            return False
        tp_fixed = entry_price + tp_r * risk if direction == "LONG" else entry_price - tp_r * risk
        if liquidity_target is not None:
            # Spec section 7: "TP = OR High ... və ya minimum 2R" -- read as
            # a FLOOR, not a straight substitution: use the liquidity level
            # only when it is AT LEAST as far as the fixed-R target, so a
            # liquidity-based TP can never award less than tp_r worth of R.
            reaches_floor = (liquidity_target >= tp_fixed) if direction == "LONG" else (liquidity_target <= tp_fixed)
            tp = liquidity_target if reaches_floor else tp_fixed
        else:
            tp = tp_fixed
        in_position = True
        pos_dir, pos_sl, pos_tp = direction, sl, tp
        pos_entry_price, pos_entry_time = entry_price, idx[entry_i]
        pos_setup_type, pos_signal_time = setup_type, idx[signal_i]
        trades_today += 1
        funnel["trades_taken"] += 1
        return True

    for i in range(ATR_LEN + 1, n):
        d, t = dates[i], times[i]

        if d != current_date:
            current_date = d
            or_high = or_low = None
            trades_today = 0
            setup_used = {k: False for k in setup_used}
            breakout_up_i = breakout_down_i = None
            sweep_down_i = sweep_down_low = None
            sweep_up_i = sweep_up_high = None

        if t == OR_START:
            or_high, or_low = float(h[i]), float(l[i])
            funnel["days_with_or_bar"] += 1
            continue  # OR bar itself is never inside the entry window

        # --- position management: SL/TP check on every bar, in or out of the window ---
        if in_position:
            hit_sl = (l[i] <= pos_sl) if pos_dir == "LONG" else (h[i] >= pos_sl)
            hit_tp = (h[i] >= pos_tp) if pos_dir == "LONG" else (l[i] <= pos_tp)
            if hit_sl or hit_tp:
                exit_price, exit_reason = (pos_sl, "SL") if hit_sl else (pos_tp, "TP")
                risk = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                cost_r = spread_points / risk if risk > 0 else 0.0
                r_mult = move / risk - cost_r
                trades.append(Trade(
                    str(idx[i].date()), pos_setup_type, pos_dir, pos_signal_time, pos_entry_time,
                    pos_entry_price, pos_sl, pos_tp, idx[i], exit_price, exit_reason,
                    round(r_mult, 4), round(r_mult * STARTING_BALANCE * risk_pct, 2),
                ))
                in_position = False

        is_last_bar_of_day = (i == n - 1) or (dates[i + 1] != d)
        if in_position and is_last_bar_of_day:
            risk = abs(pos_entry_price - pos_sl)
            move = (c[i] - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - c[i])
            cost_r = spread_points / risk if risk > 0 else 0.0
            r_mult = move / risk - cost_r
            trades.append(Trade(
                str(idx[i].date()), pos_setup_type, pos_dir, pos_signal_time, pos_entry_time,
                pos_entry_price, pos_sl, pos_tp, idx[i], float(c[i]), "EOD",
                round(r_mult, 4), round(r_mult * STARTING_BALANCE * risk_pct, 2),
            ))
            in_position = False

        if or_high is None or in_position or trades_today >= max_trades_per_day:
            continue
        if not (entry_window_start <= t < entry_window_end):
            continue

        # === Setup A: BUY breakout + retest ===
        if enable_breakout and not setup_used["buy_breakout"]:
            if breakout_up_i is None:
                if c[i] > or_high and _is_displacement(o[i], h[i], l[i], c[i], atr[i], bullish=True):
                    breakout_up_i = i
                    funnel["buy_breakout_confirmed"] += 1
            else:
                if l[i] <= or_high and c[i] >= or_high:
                    sl = l[i] - sl_buffer_atr * atr[i]
                    if open_trade("LONG", "breakout", breakout_up_i, i, float(c[i]), float(sl)):
                        funnel["buy_breakout_retest_ok"] += 1
                    setup_used["buy_breakout"] = True
                    breakout_up_i = None
                    continue
                elif c[i] < or_high:
                    funnel["buy_breakout_invalidated"] += 1
                    setup_used["buy_breakout"] = True
                    breakout_up_i = None

        # === Setup A: SELL breakout + retest ===
        if enable_breakout and not setup_used["sell_breakout"] and not in_position:
            if breakout_down_i is None:
                if c[i] < or_low and _is_displacement(o[i], h[i], l[i], c[i], atr[i], bullish=False):
                    breakout_down_i = i
                    funnel["sell_breakout_confirmed"] += 1
            else:
                if h[i] >= or_low and c[i] <= or_low:
                    sl = h[i] + sl_buffer_atr * atr[i]
                    if open_trade("SHORT", "breakout", breakout_down_i, i, float(c[i]), float(sl)):
                        funnel["sell_breakout_retest_ok"] += 1
                    setup_used["sell_breakout"] = True
                    breakout_down_i = None
                    continue
                elif c[i] > or_low:
                    funnel["sell_breakout_invalidated"] += 1
                    setup_used["sell_breakout"] = True
                    breakout_down_i = None

        # === Setup B: BUY reversal (sweep OR Low -> bullish FVG -> retest) ===
        if not setup_used["buy_reversal"] and not in_position:
            if sweep_down_i is None:
                if l[i] <= or_low and c[i] > or_low:
                    sweep_down_i, sweep_down_low = i, float(l[i])
                    funnel["buy_sweep_detected"] += 1
            elif i - sweep_down_i <= REVERSAL_LOOKBACK_BARS:
                if i >= sweep_down_i + 2:
                    min_gap = fvg_min_gap_atr * atr[i] if not np.isnan(atr[i]) else 0.0
                    disp_ok = _is_displacement(o[i - 1], h[i - 1], l[i - 1], c[i - 1], atr[i], bullish=True)
                    if l[i] - h[i - 2] >= min_gap and disp_ok:
                        zone_top = float(l[i])
                        entry_price = float(o[i + 1]) if entry_fill_mode == "next_open" and i + 1 < n else zone_top
                        sl = sweep_down_low - sl_buffer_atr * atr[i]
                        liq_target = or_high if reversal_tp_mode == "liquidity" else None
                        if open_trade("LONG", "reversal", sweep_down_i, i, entry_price, float(sl), liq_target):
                            funnel["buy_reversal_fvg_ok"] += 1
                        setup_used["buy_reversal"] = True
                        sweep_down_i = sweep_down_low = None
                        continue
            else:
                setup_used["buy_reversal"] = True
                sweep_down_i = sweep_down_low = None

        # === Setup B: SELL reversal (sweep OR High -> bearish FVG -> retest) ===
        if not setup_used["sell_reversal"] and not in_position:
            if sweep_up_i is None:
                if h[i] >= or_high and c[i] < or_high:
                    sweep_up_i, sweep_up_high = i, float(h[i])
                    funnel["sell_sweep_detected"] += 1
            elif i - sweep_up_i <= REVERSAL_LOOKBACK_BARS:
                if i >= sweep_up_i + 2:
                    min_gap = fvg_min_gap_atr * atr[i] if not np.isnan(atr[i]) else 0.0
                    disp_ok = _is_displacement(o[i - 1], h[i - 1], l[i - 1], c[i - 1], atr[i], bullish=False)
                    if l[i - 2] - h[i] >= min_gap and disp_ok:
                        zone_bottom = float(h[i])
                        entry_price = float(o[i + 1]) if entry_fill_mode == "next_open" and i + 1 < n else zone_bottom
                        sl = sweep_up_high + sl_buffer_atr * atr[i]
                        liq_target = or_low if reversal_tp_mode == "liquidity" else None
                        if open_trade("SHORT", "reversal", sweep_up_i, i, entry_price, float(sl), liq_target):
                            funnel["sell_reversal_fvg_ok"] += 1
                        setup_used["sell_reversal"] = True
                        sweep_up_i = sweep_up_high = None
                        continue
            else:
                setup_used["sell_reversal"] = True
                sweep_up_i = sweep_up_high = None

    return trades, funnel


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "setup_type", "direction", "signal_time", "entry_time", "entry_price", "stop",
                    "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "pnl_usd"])
        for tr in trades:
            w.writerow([tr.day, tr.setup_type, tr.direction, tr.signal_time, tr.entry_time, tr.entry_price,
                        tr.stop, tr.target, tr.exit_time, tr.exit_price, tr.exit_reason, tr.r_multiple, tr.pnl_usd])


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
    parser.add_argument("--input-csv", default="data/history/XAUUSD_M1.csv")
    parser.add_argument("--risk-pct", type=float, default=RISK_PCT)
    parser.add_argument("--tp-r", type=float, default=TP_R)
    parser.add_argument("--max-trades-per-day", type=int, default=MAX_TRADES_PER_DAY)
    parser.add_argument("--displacement-atr-mult", type=float, default=DISPLACEMENT_ATR_MULT)
    parser.add_argument("--fvg-min-gap-atr", type=float, default=FVG_MIN_GAP_ATR)
    parser.add_argument("--sl-buffer-atr", type=float, default=SL_BUFFER_ATR)
    parser.add_argument("--max-risk-atr-mult", type=float, default=MAX_RISK_ATR_MULT)
    parser.add_argument("--spread-points", type=float, default=0.0, help="round-trip spread in price points, e.g. 0.39 for XAUUSD (see robustness_analysis.SPREAD_BY_SYMBOL)")
    parser.add_argument("--reversal-tp-mode", choices=["fixed", "liquidity"], default="fixed", help="reversal setup TP: fixed tp_r, or the opposite OR boundary floored at tp_r (spec section 7)")
    parser.add_argument("--enable-breakout", action="store_true", default=True)
    parser.add_argument("--no-enable-breakout", dest="enable_breakout", action="store_false", help="Setup-B-only, matching strategy/xauusd_orb_liquidity_sweep.py exactly")
    parser.add_argument("--bar-minutes", type=int, default=5, help="OR candle size in minutes; entry window auto-derives (OR_START+bar_minutes .. entry-window-end)")
    parser.add_argument("--entry-window-end", default="10:00", help="NY local HH:MM after which no NEW setup may start")
    args = parser.parse_args()
    ew_h, ew_m = (int(x) for x in args.entry_window_end.split(":"))

    trades, funnel = run_backtest(
        args.input_csv, args.risk_pct, args.tp_r, args.max_trades_per_day,
        args.displacement_atr_mult, args.fvg_min_gap_atr, args.sl_buffer_atr,
        args.max_risk_atr_mult, args.spread_points, args.reversal_tp_mode,
        args.enable_breakout, args.bar_minutes, dtime(ew_h, ew_m),
    )
    out_path = "artifacts/xauusd_orb_liquidity_sweep_trades.csv"
    write_trades_csv(trades, out_path)
    stats = summarize(trades)

    print(f"funnel = {funnel}")
    print(f"result = {stats}")
    print(f"Trade log written to {out_path}")

    if trades:
        breakout = [t for t in trades if t.setup_type == "breakout"]
        reversal = [t for t in trades if t.setup_type == "reversal"]
        print(f"  breakout n={len(breakout)}  {summarize(breakout)}")
        print(f"  reversal n={len(reversal)}  {summarize(reversal)}")
