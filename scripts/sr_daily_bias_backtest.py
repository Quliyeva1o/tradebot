"""Python port of pine scriptlerim/SR_Daily_Bias_Strategy.pine, for backtesting
against this repo's own NAS100 M1 history since TradingView's Strategy Tester
isn't available here. Mirrors the Pine script's logic bar-for-bar (same ATR-
based distances, same three entry types, same risk rules) so results are a
faithful proxy for what the Pine script would do -- not a re-derivation of a
different strategy.

Usage: set TIMEFRAME_MINUTES below (5, 15, 30, or 60) and run as a module:
    python -m scripts.sr_daily_bias_backtest

Design notes (mirroring the Pine script's own documented simplifications):
- Daily Bias uses the LAST FULLY CLOSED daily bar strictly before the current
  calendar day (conservative non-lookahead choice; Pine's request.security
  with lookahead_off returns the still-forming daily bar intrabar, which this
  offline backtest cannot cheaply replicate bar-by-bar without look-ahead
  risk, so it uses the safer "yesterday's daily close" instead).
- Pivot high/low (Support/Resistance) confirmed leftBars/rightBars after the
  pivot, same lag as ta.pivothigh/pivotlow.
- All distances are ATR-multiples, matching the Pine script's instrument-
  agnostic design.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

INPUT_CSV = "data/history/NAS100_M1.csv"
TIMEFRAME_MINUTES = 30  # 5, 15, 30, or 60 -- change and rerun for each variant
TRADES_OUT = f"artifacts/sr_daily_bias_trades_{TIMEFRAME_MINUTES}m.csv"

# --- Strategy config (mirrors the .pine script's input defaults) ---
DAILY_BIAS_EMA_LEN = 20
BIAS_NEUTRAL_PCT = 0.15
SWING_LEN = 10
MIN_SR_DIST_ATR = 1.5
TOUCH_TOLERANCE_ATR = 0.25
REJECTION_WICK_RATIO = 0.4
REJECTION_CLOSE_POS = 0.6
REQUIRE_VOL_ON_BOUNCE = True
BREAKOUT_BUFFER_ATR = 0.15
BREAKOUT_CONFIRM_BARS = 1
RETEST_MAX_BARS = 30
VOL_SMA_LEN = 20
VOL_MULTIPLIER = 1.3
USE_ADX_FILTER = True
ADX_LEN = 14
ADX_THRESHOLD = 35.0
ATR_LEN = 14
SL_BUFFER_ATR = 0.2
MIN_RISK_ATR = 0.3
MAX_RISK_ATR = 6.0
RR = 3.0
RISK_PCT = 0.01
STARTING_BALANCE = 100_000.0


@dataclass
class Trade:
    entry_time: pd.Timestamp
    direction: str
    entry_type: str
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float


def load_m1(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append((ny_ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts").sort_index()
    return df


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return wilder_smooth(tr, period)


def compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = wilder_smooth(tr, period)
    plus_di = 100 * wilder_smooth(pd.Series(plus_dm, index=df.index), period) / atr
    minus_di = 100 * wilder_smooth(pd.Series(minus_dm, index=df.index), period) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder_smooth(dx.fillna(0), period)


def compute_pivots(df: pd.DataFrame, left: int, right: int) -> tuple[pd.Series, pd.Series]:
    """Returns (pivot_high, pivot_low) series, indexed same as df, with the
    pivot value placed at the CONFIRMATION bar (pivot_bar + right), matching
    ta.pivothigh/pivotlow's non-repainting timing.
    """
    n = len(df)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    for i in range(left, n - right):
        window_h = highs[i - left : i + right + 1]
        if highs[i] == window_h.max():
            ph[i + right] = highs[i]
        window_l = lows[i - left : i + right + 1]
        if lows[i] == window_l.min():
            pl[i + right] = lows[i]
    return pd.Series(ph, index=df.index), pd.Series(pl, index=df.index)


def run_backtest(tf_minutes: int) -> list[Trade]:
    m1 = load_m1(INPUT_CSV)
    bars = resample(m1, tf_minutes)

    daily = resample(m1, 1440)
    daily_ema = daily["close"].ewm(span=DAILY_BIAS_EMA_LEN, adjust=False).mean()
    daily_bias_by_date: dict = {}
    prev_close = None
    prev_ema = None
    for d, close_ in daily["close"].items():
        if prev_close is not None:
            upper = prev_ema * (1 + BIAS_NEUTRAL_PCT / 100)
            lower = prev_ema * (1 - BIAS_NEUTRAL_PCT / 100)
            bias = 1 if prev_close > upper else (-1 if prev_close < lower else 0)
            daily_bias_by_date[d.date()] = bias
        prev_close = close_
        prev_ema = daily_ema.loc[d]

    bars = bars.copy()
    bars["atr"] = compute_atr(bars, ATR_LEN)
    bars["adx"] = compute_adx(bars, ADX_LEN)
    bars["vol_sma"] = bars["volume"].rolling(VOL_SMA_LEN).mean()
    ph, pl = compute_pivots(bars, SWING_LEN, SWING_LEN)
    bars["pivot_high"] = ph
    bars["pivot_low"] = pl

    idx = bars.index
    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    l = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    vol = bars["volume"].to_numpy()
    atr = bars["atr"].to_numpy()
    adx = bars["adx"].to_numpy()
    vol_sma = bars["vol_sma"].to_numpy()
    piv_h = bars["pivot_high"].to_numpy()
    piv_l = bars["pivot_low"].to_numpy()

    n = len(bars)
    resistance = np.nan
    support = np.nan
    broken_res_level, broken_res_bar = np.nan, None
    broken_sup_level, broken_sup_bar = np.nan, None

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = None
    pos_entry_time = pos_entry_price = pos_entry_type = None
    skip_counts: dict[str, int] = {}

    def skip(reason):
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    risk_amount = STARTING_BALANCE * RISK_PCT
    warmup = max(ATR_LEN, ADX_LEN, VOL_SMA_LEN, SWING_LEN * 2) + 5

    for i in range(warmup, n):
        if not np.isnan(piv_h[i]):
            resistance = piv_h[i]
        if not np.isnan(piv_l[i]):
            support = piv_l[i]

        if in_position:
            hit_sl = (l[i] <= pos_sl) if pos_dir == "LONG" else (h[i] >= pos_sl)
            hit_tp = (h[i] >= pos_tp) if pos_dir == "LONG" else (l[i] <= pos_tp)
            exit_price = exit_reason = None
            if hit_sl:
                exit_price, exit_reason = pos_sl, "SL"
            elif hit_tp:
                exit_price, exit_reason = pos_tp, "TP"
            if exit_price is not None:
                risk_dist = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                r_mult = move / risk_dist
                trades.append(Trade(
                    pos_entry_time, pos_dir, pos_entry_type, pos_entry_price, pos_sl, pos_tp,
                    idx[i], exit_price, exit_reason, round(r_mult, 3), round(r_mult * risk_amount, 2),
                ))
                in_position = False
            # NOTE: deliberately no `continue` here -- broken-level marking/
            # expiry and setup detection below still run every bar (matching
            # the Pine script's unconditional per-bar evaluation), only the
            # final "open a new trade" step is gated on `not in_position`.

        if broken_res_bar is not None and i - broken_res_bar > RETEST_MAX_BARS:
            broken_res_level, broken_res_bar = np.nan, None
        if broken_sup_bar is not None and i - broken_sup_bar > RETEST_MAX_BARS:
            broken_sup_level, broken_sup_bar = np.nan, None

        d = idx[i].date()
        bias = daily_bias_by_date.get(d)
        if bias is None:
            skip("no_daily_bias_yet")
            continue
        if bias == 0:
            skip("neutral_bias")
            continue
        if np.isnan(atr[i]) or np.isnan(support) or np.isnan(resistance):
            skip("warmup")
            continue

        sr_dist_ok = (resistance - support) >= MIN_SR_DIST_ATR * atr[i]
        vol_confirmed = vol[i] >= vol_sma[i] * VOL_MULTIPLIER if not np.isnan(vol_sma[i]) else False
        strong_trend = USE_ADX_FILTER and not np.isnan(adx[i]) and adx[i] >= ADX_THRESHOLD

        range_ = h[i] - l[i]
        bull_rej = range_ > 0 and (min(o[i], c[i]) - l[i]) >= REJECTION_WICK_RATIO * range_ and ((c[i] - l[i]) / range_) >= REJECTION_CLOSE_POS
        bear_rej = range_ > 0 and (h[i] - max(o[i], c[i])) >= REJECTION_WICK_RATIO * range_ and ((h[i] - c[i]) / range_) >= REJECTION_CLOSE_POS

        touched_support = l[i] <= support + TOUCH_TOLERANCE_ATR * atr[i]
        touched_resistance = h[i] >= resistance - TOUCH_TOLERANCE_ATR * atr[i]

        long_bounce = bias == 1 and sr_dist_ok and touched_support and bull_rej and (not REQUIRE_VOL_ON_BOUNCE or vol_confirmed) and not strong_trend
        short_bounce = bias == -1 and sr_dist_ok and touched_resistance and bear_rej and (not REQUIRE_VOL_ON_BOUNCE or vol_confirmed) and not strong_trend

        win_c = c[max(0, i - BREAKOUT_CONFIRM_BARS + 1) : i + 1]
        closes_above_res = sr_dist_ok and win_c.min() > resistance + BREAKOUT_BUFFER_ATR * atr[i]
        closes_below_sup = sr_dist_ok and win_c.max() < support - BREAKOUT_BUFFER_ATR * atr[i]
        fresh_up = closes_above_res and not (c[i - 1] > resistance + BREAKOUT_BUFFER_ATR * atr[i])
        fresh_down = closes_below_sup and not (c[i - 1] < support - BREAKOUT_BUFFER_ATR * atr[i])

        bullish_breakout = bias == 1 and fresh_up and vol_confirmed
        bearish_breakout = bias == -1 and fresh_down and vol_confirmed

        if bullish_breakout:
            broken_res_level, broken_res_bar = resistance, i
        if bearish_breakout:
            broken_sup_level, broken_sup_bar = support, i

        retest_long = bias == 1 and broken_res_bar is not None and l[i] <= broken_res_level + TOUCH_TOLERANCE_ATR * atr[i] and l[i] >= broken_res_level - TOUCH_TOLERANCE_ATR * atr[i] and bull_rej
        retest_short = bias == -1 and broken_sup_bar is not None and h[i] >= broken_sup_level - TOUCH_TOLERANCE_ATR * atr[i] and h[i] <= broken_sup_level + TOUCH_TOLERANCE_ATR * atr[i] and bear_rej
        if retest_long:
            broken_res_level, broken_res_bar = np.nan, None
        if retest_short:
            broken_sup_level, broken_sup_bar = np.nan, None

        long_setup = long_bounce or bullish_breakout or retest_long
        short_setup = short_bounce or bearish_breakout or retest_short

        if in_position:
            continue  # a position is (still) open -- state above is tracked, but no new entry this bar

        if long_setup:
            sl_base = support if long_bounce else (resistance if bullish_breakout else broken_res_level)
            sl_price = sl_base - SL_BUFFER_ATR * atr[i]
            risk_dist = c[i] - sl_price
            if MIN_RISK_ATR * atr[i] <= risk_dist <= MAX_RISK_ATR * atr[i]:
                entry_type = "Bounce" if long_bounce else ("Breakout" if bullish_breakout else "Retest")
                in_position = True
                pos_dir, pos_sl, pos_tp = "LONG", sl_price, c[i] + risk_dist * RR
                pos_entry_time, pos_entry_price, pos_entry_type = idx[i], c[i], entry_type
            else:
                skip("risk_out_of_bounds")
        elif short_setup:
            sl_base = resistance if short_bounce else (support if bearish_breakout else broken_sup_level)
            sl_price = sl_base + SL_BUFFER_ATR * atr[i]
            risk_dist = sl_price - c[i]
            if MIN_RISK_ATR * atr[i] <= risk_dist <= MAX_RISK_ATR * atr[i]:
                entry_type = "Bounce" if short_bounce else ("Breakout" if bearish_breakout else "Retest")
                in_position = True
                pos_dir, pos_sl, pos_tp = "SHORT", sl_price, c[i] - risk_dist * RR
                pos_entry_time, pos_entry_price, pos_entry_type = idx[i], c[i], entry_type
            else:
                skip("risk_out_of_bounds")

    print(f"TF={tf_minutes}m bars={n} skip_funnel={skip_counts}")
    return trades


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_type", "entry_price", "stop", "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "pnl_usd"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_type, t.entry_price, t.stop, t.target, t.exit_time, t.exit_price, t.exit_reason, t.r_multiple, t.pnl_usd])


def summarize(trades: list[Trade]) -> None:
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = abs(sum(t.pnl_usd for t in losses))
    pf = gp / gl if gl > 0 else float("inf")
    total_r = sum(t.r_multiple for t in trades)
    print(f"Total trades: {n}  Wins: {len(wins)}  Losses: {len(losses)}  Win rate: {len(wins)/n*100:.1f}%")
    print(f"Profit factor: {pf:.2f}  Total R: {total_r:.2f}  Net P&L: ${sum(t.pnl_usd for t in trades):,.2f}")
    by_type: dict[str, int] = {}
    for t in trades:
        by_type[t.entry_type] = by_type.get(t.entry_type, 0) + 1
    print("By entry type:", by_type)


if __name__ == "__main__":
    trades = run_backtest(TIMEFRAME_MINUTES)
    write_trades_csv(trades, TRADES_OUT)
    summarize(trades)
    print(f"Trade log written to {TRADES_OUT}")
