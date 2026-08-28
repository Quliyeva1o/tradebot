"""SR Daily Bias strategy, backtested with spread, across every symbol /
timeframe / TP-variant combination this repo has ever tested it against, so
the "which config actually survives real costs" question can be answered
with one consistent methodology instead of five ad-hoc scripts.

Reuses the exact per-bar simulation logic already validated in
scripts/sr_daily_bias_backtest.py (fixed-3R TP) and
scripts/sr_daily_bias_backtest_liquidity_tp.py (nearest-liquidity TP, the
variant strategy/sr_daily_bias.py's live class mirrors) -- reimplemented
here ONCE (not copy-pasted per symbol) so both TP modes share one core loop.
Correctness-reviewed against both source scripts before running the sweep:
daily bias uses only the last FULLY CLOSED prior-day bar (no lookahead, same
pattern as scripts/backtest_common.htf_bias_known_from), pivot highs/lows are
placed at their CONFIRMATION bar (pivot + swing_len), and SL/TP are only
checked starting the bar AFTER entry (entry itself fills at that bar's close,
so there is no same-bar-as-entry stop-out to miss, unlike the FVG family of
strategies where entry can happen mid-zone-touch within a bar that still has
remaining range left to check).

Spread model:
  - NAS100: the per-bar "spread" column in data/history/NAS100_M1.csv reads
    exactly 0.0 for every bar before 2024 (the broker only started recording
    live spread then) -- confirmed in the First FVG spread work
    (FIRST_FVG_15M_SPREAD_REPORT.md). A fixed 3.0-point round-trip constant
    is used instead, matching scripts/robustness_analysis.SPREAD_BY_SYMBOL.
  - XAUUSD / EURUSD / GBPUSD / USDJPY: freshly re-downloaded via
    `python -m data.download_history` (2026-08-28), their per-bar spread
    column IS populated with realistic, non-zero, time-varying values across
    the full history (checked: means of ~15-45 points depending on symbol/
    year, consistent with this account's current live mt5.symbol_info()
    spread) -- so the ACTUAL historical spread is used per trade, not a
    constant, which is more accurate than NAS100's fallback.
  - Cost applied the same way as scripts/robustness_analysis.py and the
    First FVG spread work: cost_r = spread_price / risk_distance, subtracted
    from r_multiple once per trade (entry side only).

Usage:
    python -m scripts.sr_daily_bias_spread_sweep
    python -m scripts.sr_daily_bias_spread_sweep --symbols NAS100,XAUUSD --timeframes 15,30
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

SYMBOLS = ["NAS100", "XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
TIMEFRAMES = [5, 15, 30, 60]
VARIANTS = ["fixed3r", "liquidity"]  # "fixedNr" for any N, e.g. "fixed2r" -- parsed by _fixed_rr_of()


def _fixed_rr_of(variant: str) -> float:
    """Parses the R multiple out of a "fixedNr" variant name, e.g.
    "fixed2r" -> 2.0, "fixed3r" -> 3.0. Lets the sweep compare arbitrary
    fixed-R targets without hardcoding a second near-duplicate variant.
    """
    return float(variant[len("fixed"):-1])

# Fixed round-trip spread override for symbols whose historical spread column
# is unusable (broken/zeroed) -- see module docstring. Symbols NOT listed
# here use their real per-bar historical spread instead.
FIXED_SPREAD_OVERRIDE = {"NAS100": 3.0}

# --- Strategy config (mirrors the .pine script's input defaults, identical
# to both scripts/sr_daily_bias_backtest*.py) ---
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
FIXED_RR = 3.0  # only used when variant == "fixed3r"
MIN_REWARD_ATR = 0.5  # only used when variant == "liquidity"
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
    spread_price: float
    r_multiple_gross: float
    r_multiple: float
    pnl_usd: float


def load_m1_with_spread(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append(
                (ny_ts, float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row["volume"]), float(row.get("spread") or 0.0))
            )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "spread"])
    return df.set_index("ts").sort_index()


def resample_tf(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    out = df.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum", "spread": "mean"}
    )
    return out.dropna(subset=["open"])


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return wilder_smooth(tr, period)


def compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = wilder_smooth(tr, period)
    plus_di = 100 * wilder_smooth(pd.Series(plus_dm, index=df.index), period) / atr
    minus_di = 100 * wilder_smooth(pd.Series(minus_dm, index=df.index), period) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder_smooth(dx.fillna(0), period)


def compute_pivots(df: pd.DataFrame, left: int, right: int) -> tuple[pd.Series, pd.Series]:
    n = len(df)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    for i in range(left, n - right):
        window_h = highs[i - left: i + right + 1]
        if highs[i] == window_h.max():
            ph[i + right] = highs[i]
        window_l = lows[i - left: i + right + 1]
        if lows[i] == window_l.min():
            pl[i + right] = lows[i]
    return pd.Series(ph, index=df.index), pd.Series(pl, index=df.index)


def run_backtest(symbol: str, tf_minutes: int, variant: str, m1: pd.DataFrame) -> list[Trade]:
    bars = resample_tf(m1, tf_minutes)

    daily = resample_tf(m1, 1440)
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
    spread_col = bars["spread"].to_numpy()

    fixed_spread = FIXED_SPREAD_OVERRIDE.get(symbol)

    n = len(bars)
    resistance = np.nan
    support = np.nan
    broken_res_level, broken_res_bar = np.nan, None
    broken_sup_level, broken_sup_bar = np.nan, None
    active_highs: list[float] = []
    active_lows: list[float] = []

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = None
    pos_entry_time = pos_entry_price = pos_entry_type = pos_spread = None

    risk_amount = STARTING_BALANCE * RISK_PCT
    warmup = max(ATR_LEN, ADX_LEN, VOL_SMA_LEN, SWING_LEN * 2) + 5

    for i in range(warmup, n):
        if not np.isnan(piv_h[i]):
            resistance = piv_h[i]
            active_highs.append(piv_h[i])
        if not np.isnan(piv_l[i]):
            support = piv_l[i]
            active_lows.append(piv_l[i])
        active_highs = [lvl for lvl in active_highs if lvl > h[i]]
        active_lows = [lvl for lvl in active_lows if lvl < l[i]]

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
                r_gross = move / risk_dist
                cost_r = (pos_spread / risk_dist) if risk_dist > 0 else 0.0
                r_net = r_gross - cost_r
                trades.append(Trade(
                    pos_entry_time, pos_dir, pos_entry_type, pos_entry_price, pos_sl, pos_tp,
                    idx[i], exit_price, exit_reason, round(pos_spread, 6),
                    round(r_gross, 4), round(r_net, 4), round(r_net * risk_amount, 2),
                ))
                in_position = False

        if broken_res_bar is not None and i - broken_res_bar > RETEST_MAX_BARS:
            broken_res_level, broken_res_bar = np.nan, None
        if broken_sup_bar is not None and i - broken_sup_bar > RETEST_MAX_BARS:
            broken_sup_level, broken_sup_bar = np.nan, None

        d = idx[i].date()
        bias = daily_bias_by_date.get(d)
        if bias is None or bias == 0:
            continue
        if np.isnan(atr[i]) or np.isnan(support) or np.isnan(resistance):
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

        win_c = c[max(0, i - BREAKOUT_CONFIRM_BARS + 1): i + 1]
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

        retest_long_level, retest_short_level = broken_res_level, broken_sup_level
        retest_long = bias == 1 and broken_res_bar is not None and retest_long_level - TOUCH_TOLERANCE_ATR * atr[i] <= l[i] <= retest_long_level + TOUCH_TOLERANCE_ATR * atr[i] and bull_rej
        retest_short = bias == -1 and broken_sup_bar is not None and retest_short_level - TOUCH_TOLERANCE_ATR * atr[i] <= h[i] <= retest_short_level + TOUCH_TOLERANCE_ATR * atr[i] and bear_rej
        if retest_long:
            broken_res_level, broken_res_bar = np.nan, None
        if retest_short:
            broken_sup_level, broken_sup_bar = np.nan, None

        long_setup = long_bounce or bullish_breakout or retest_long
        short_setup = short_bounce or bearish_breakout or retest_short

        if in_position:
            continue

        spread_price = fixed_spread if fixed_spread is not None else (
            0.0 if np.isnan(spread_col[i]) else float(spread_col[i])
        )

        if long_setup:
            sl_base = support if long_bounce else (resistance if bullish_breakout else retest_long_level)
            sl_price = sl_base - SL_BUFFER_ATR * atr[i]
            risk_dist = c[i] - sl_price
            if not (MIN_RISK_ATR * atr[i] <= risk_dist <= MAX_RISK_ATR * atr[i]):
                continue
            if variant.startswith("fixed"):
                tp = c[i] + risk_dist * _fixed_rr_of(variant)
            else:
                liq_targets = [lvl for lvl in active_highs if lvl > c[i]]
                tp = min(liq_targets) if liq_targets else None
                if tp is None or (tp - c[i]) < MIN_REWARD_ATR * atr[i]:
                    continue
            entry_type = "Bounce" if long_bounce else ("Breakout" if bullish_breakout else "Retest")
            in_position = True
            pos_dir, pos_sl, pos_tp = "LONG", sl_price, tp
            pos_entry_time, pos_entry_price, pos_entry_type, pos_spread = idx[i], c[i], entry_type, spread_price
        elif short_setup:
            sl_base = resistance if short_bounce else (support if bearish_breakout else retest_short_level)
            sl_price = sl_base + SL_BUFFER_ATR * atr[i]
            risk_dist = sl_price - c[i]
            if not (MIN_RISK_ATR * atr[i] <= risk_dist <= MAX_RISK_ATR * atr[i]):
                continue
            if variant.startswith("fixed"):
                tp = c[i] - risk_dist * _fixed_rr_of(variant)
            else:
                liq_targets = [lvl for lvl in active_lows if lvl < c[i]]
                tp = max(liq_targets) if liq_targets else None
                if tp is None or (c[i] - tp) < MIN_REWARD_ATR * atr[i]:
                    continue
            entry_type = "Bounce" if short_bounce else ("Breakout" if bearish_breakout else "Retest")
            in_position = True
            pos_dir, pos_sl, pos_tp = "SHORT", sl_price, tp
            pos_entry_time, pos_entry_price, pos_entry_type, pos_spread = idx[i], c[i], entry_type, spread_price

    return trades


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "net_pnl": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    return {
        "trades": n,
        "win_rate": round(wins / n * 100, 1),
        "profit_factor": (round(pf, 3) if gl > 0 else None),
        "net_pnl": round(sum(t.pnl_usd for t in trades), 2),
    }


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_type", "entry_price", "stop", "target",
                    "exit_time", "exit_price", "exit_reason", "spread_price",
                    "r_multiple_gross", "r_multiple_net", "pnl_usd_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_type, t.entry_price, t.stop, t.target,
                        t.exit_time, t.exit_price, t.exit_reason, t.spread_price,
                        t.r_multiple_gross, t.r_multiple, t.pnl_usd])


def period_breakdown(trades: list[Trade], end_date) -> dict:
    end = pd.Timestamp(end_date)
    windows = {
        "5y": pd.DateOffset(years=5), "1y": pd.DateOffset(years=1),
        "3mo": pd.DateOffset(months=3), "1mo": pd.DateOffset(months=1),
    }
    window_starts = {label: (end - off).date().isoformat() for label, off in windows.items()}

    def day_of(t: Trade) -> str:
        return t.entry_time.date().isoformat()

    window_summaries = {
        label: summarize([t for t in trades if window_starts[label] <= day_of(t) <= end.date().isoformat()])
        for label in windows
    }

    y1_trades = [t for t in trades if window_starts["1y"] <= day_of(t) <= end.date().isoformat()]
    monthly: dict[str, list[Trade]] = {}
    for t in y1_trades:
        monthly.setdefault(day_of(t)[:7], []).append(t)
    monthly_summary = {ym: summarize(ts) for ym, ts in sorted(monthly.items())}

    m1_trades = [t for t in trades if window_starts["1mo"] <= day_of(t) <= end.date().isoformat()]
    daily_by_day: dict[str, list[Trade]] = {}
    for t in m1_trades:
        daily_by_day.setdefault(day_of(t), []).append(t)
    daily_summary = {d: summarize(ts) for d, ts in sorted(daily_by_day.items())}

    y5_start = pd.Timestamp(window_starts["5y"])
    half_year_summary = {}
    cursor = y5_start
    while cursor < end:
        nxt = cursor + pd.DateOffset(months=6)
        label = f"{cursor.date().isoformat()}..{min(nxt, end).date().isoformat()}"
        sub = [t for t in trades if cursor.date().isoformat() <= day_of(t) < nxt.date().isoformat()]
        half_year_summary[label] = summarize(sub)
        cursor = nxt

    return {
        "windows": window_summaries,
        "monthly_last_1y": monthly_summary,
        "daily_last_1mo": daily_summary,
        "half_year_last_5y": half_year_summary,
        "window_starts": window_starts,
        "end_date": end.date().isoformat(),
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(str(t) for t in TIMEFRAMES))
    parser.add_argument("--variants", default=",".join(VARIANTS))
    args = parser.parse_args()

    symbols = args.symbols.split(",")
    timeframes = [int(t) for t in args.timeframes.split(",")]
    variants = args.variants.split(",")

    all_results: dict[str, dict] = {}
    m1_cache: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        if symbol not in m1_cache:
            m1_cache[symbol] = load_m1_with_spread(f"data/history/{symbol}_M1.csv")
        m1 = m1_cache[symbol]
        for tf in timeframes:
            for variant in variants:
                key = f"{symbol}_{tf}m_{variant}"
                trades = run_backtest(symbol, tf, variant, m1)
                write_trades_csv(trades, f"artifacts/sr_sweep_{key}_trades.csv")
                if not trades:
                    print(f"{key}: 0 trades")
                    all_results[key] = {"symbol": symbol, "timeframe": tf, "variant": variant, "trades": 0}
                    continue
                end_date = m1.index.max().date()
                breakdown = period_breakdown(trades, end_date)
                summary_5y = breakdown["windows"]["5y"]
                summary_1y = breakdown["windows"]["1y"]
                print(f"{key}: n_total={len(trades)}  5y={summary_5y}  1y={summary_1y}")
                all_results[key] = {
                    "symbol": symbol, "timeframe": tf, "variant": variant,
                    "total_trades": len(trades), **breakdown,
                }

    with open("artifacts/sr_daily_bias_spread_sweep.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print("\nFull sweep JSON written to artifacts/sr_daily_bias_spread_sweep.json")


if __name__ == "__main__":
    main()
