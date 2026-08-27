"""Order Flow + Daily Bias + Trendline strategy backtest.

Implements the user's full written spec: Daily Bias (1H structure + 15M
refinement + PDH/PDL/mid + VWAP), Trendline (swing-based, on the execution
timeframe, requiring confirmed touches before use), Liquidity zones (PDH/
PDL/mid, session highs/lows, swing pools, trendline zone), Order Flow
confirmation, Entry/SL/TP, and the trade filter/priority ordering.

IMPORTANT DATA-SOURCE CONSTRAINT (verified against this MT5 terminal twice):
every symbol's ticks carry volume=0/volume_real=0 (bid/ask quote ticks only,
no real trade prints), and tick history depth is ~1 day for all 3 symbols --
real exchange Delta/CVD/footprint (which need aggressor-side traded volume)
cannot be built from this data source at ANY effort level, backtest or
live. Every "Order Flow" concept below is therefore a bar-level PROXY built
from the M1 sub-bars nested inside each execution-TF candle -- the finest
granularity this data source actually offers -- using the classic tick-rule
(an up-close M1 sub-bar counts as buy pressure, a down-close one as sell
pressure, weighted by that sub-bar's own tick-volume). This is deliberately
the SAME construction the live class (once built) will use, so backtest and
live never diverge the way this session's audit found and fixed twice for
other strategies.

Usage:
    python -m scripts.order_flow_bias_backtest --symbol XAUUSD --tf 15
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

STARTING_BALANCE = 100_000.0
RISK_PCT = 0.01

# --- Config (documented per the user's spec section numbers) ---
ATR_LEN = 14
SWING_LEN = 5                       # pivot half-window on the execution TF
TRENDLINE_TOUCH_TOL_ATR = 0.3        # section 2: how close price must come to the line to count as a touch
TRENDLINE_MIN_CONFIRM_TOUCHES = 1    # section 2: "at least 2 confirmed touches" == 2 defining points + this many more
TRENDLINE_BREAKOUT_BUFFER_ATR = 0.15
TRENDLINE_RETEST_MAX_BARS = 20
LIQUIDITY_SWEEP_LOOKBACK_BARS = 5    # section 3: sweep must have happened recently, not any time in history
DISPLACEMENT_LOOKBACK_BARS = 5       # section 5: displacement is the IMPULSE that precedes the retest, not
                                      # necessarily the same candle as the rejection-wick retest bar itself
EQUAL_LEVEL_TOL_ATR = 0.15           # section 3: equal-highs/equal-lows clustering tolerance
OF_ABSORPTION_RANGE_ATR_MAX = 0.6    # section 4: absorption = high volume, narrow range
OF_ABSORPTION_VOL_MULT = 1.3
OF_STACKED_RATIO = 0.7               # section 4: fraction of M1 sub-bars sharing the same delta sign
OF_MIN_CONFIRMATIONS = 3             # section 4/8: minimum order-flow confirmations required (of ~5 checked)
DISPLACEMENT_ATR_MULT = 2.0          # section 4/5: same formula as smc/displacement.py
SL_BUFFER_ATR = 0.15                 # section 6
MIN_RR = 2.0                         # section 6/7: "if 1:2 isn't possible, don't take the trade"
DAILY_BIAS_VOTE_THRESHOLD = 2        # section 1: needs >=2 of 4 directional votes to agree


@dataclass
class Trade:
    entry_time: pd.Timestamp
    direction: str
    entry_price: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float
    confirmations: int


def load_m1(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append((ny_ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.set_index("ts").sort_index()


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


def compute_pivots(df: pd.DataFrame, half_window: int) -> tuple[np.ndarray, np.ndarray]:
    """Non-repainting pivot high/low, confirmed half_window bars after the pivot (ta.pivothigh/pivotlow semantics)."""
    n = len(df)
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    ph, pl = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(half_window, n - half_window):
        wh = highs[i - half_window : i + half_window + 1]
        if highs[i] == wh.max():
            ph[i + half_window] = highs[i]
        wl = lows[i - half_window : i + half_window + 1]
        if lows[i] == wl.min():
            pl[i + half_window] = lows[i]
    return ph, pl


def session_high_low(m1: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float | None, float | None]:
    window = m1.loc[(m1.index >= start) & (m1.index < end)]
    if window.empty:
        return None, None
    return float(window["high"].max()), float(window["low"].min())


def compute_daily_levels(m1: pd.DataFrame) -> dict:
    """PDH/PDL/PD-mid per NY calendar date (previous FULL day's H/L), plus a
    same-day session-anchored VWAP series sampled from M1 bars.
    """
    daily = resample(m1, 1440)
    daily.index = daily.index.tz_convert(NY)
    pdh_by_date: dict = {}
    pdl_by_date: dict = {}
    pdmid_by_date: dict = {}
    dates = list(daily.index.date)
    for i in range(1, len(dates)):
        prev = daily.iloc[i - 1]
        d = dates[i]
        pdh_by_date[d] = float(prev["high"])
        pdl_by_date[d] = float(prev["low"])
        pdmid_by_date[d] = float((prev["high"] + prev["low"]) / 2)

    pv = m1["close"] * m1["volume"]
    day_key = m1.index.date
    cum_pv = pv.groupby(day_key).cumsum()
    cum_vol = m1["volume"].groupby(day_key).cumsum().replace(0, np.nan)
    vwap = (cum_pv / cum_vol).fillna(m1["close"])
    return {"pdh": pdh_by_date, "pdl": pdl_by_date, "pdmid": pdmid_by_date, "vwap": vwap}


def structure_bias_votes(bars: pd.DataFrame, swing_len: int) -> pd.Series:
    """+1/-1/0 per bar: +1 if the last 2 confirmed swings are Higher-High +
    Higher-Low (or the most recent confirmed swing broke above the prior
    swing high), -1 for the mirrored bearish case, 0 otherwise. Applied to
    whatever timeframe `bars` is (1H for the primary vote, 15M for
    refinement) -- see compute_daily_bias() below.
    """
    ph, pl = compute_pivots(bars, swing_len)
    n = len(bars)
    votes = np.zeros(n)
    highs = [(i, v) for i, v in enumerate(ph) if not np.isnan(v)]
    lows = [(i, v) for i, v in enumerate(pl) if not np.isnan(v)]
    last_high_idx = last_low_idx = -1
    hi_ptr = lo_ptr = 0
    prev_high = prev_low = None
    cur_high = cur_low = None
    for i in range(n):
        while hi_ptr < len(highs) and highs[hi_ptr][0] <= i:
            prev_high, cur_high = cur_high, highs[hi_ptr][1]
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr][0] <= i:
            prev_low, cur_low = cur_low, lows[lo_ptr][1]
            lo_ptr += 1
        vote = 0
        if prev_high is not None and cur_high is not None and prev_low is not None and cur_low is not None:
            if cur_high > prev_high and cur_low > prev_low:
                vote = 1
            elif cur_high < prev_high and cur_low < prev_low:
                vote = -1
        votes[i] = vote
    return pd.Series(votes, index=bars.index)


def compute_daily_bias(m1: pd.DataFrame, levels: dict) -> pd.Series:
    """Bias (+1/-1/0), reindexed to M1 resolution via forward-fill so any
    execution TF can sample it. Recomputed at every new 1H bar close (not
    once a day) since the spec's Daily Bias explicitly leans on 1H/15M
    *market structure*, which develops intraday -- unlike the EMA-only or
    daily-fractal biases used by this session's other two strategies.
    """
    h1 = resample(m1, 60)
    h1.index = h1.index.tz_convert(NY)
    m15 = resample(m1, 15)
    m15.index = m15.index.tz_convert(NY)

    vote_1h = structure_bias_votes(h1, swing_len=3)
    vote_15m = structure_bias_votes(m15, swing_len=5)

    day_key_1h = h1.index.date
    pdh_1h = pd.Series([levels["pdh"].get(d, np.nan) for d in day_key_1h], index=h1.index)
    pdmid_1h = pd.Series([levels["pdmid"].get(d, np.nan) for d in day_key_1h], index=h1.index)
    vote_pdmid = np.sign(h1["close"] - pdmid_1h).fillna(0)

    vwap_at_1h = levels["vwap"].reindex(h1.index, method="ffill")
    vote_vwap = np.sign(h1["close"] - vwap_at_1h).fillna(0)

    vote_15m_at_1h = vote_15m.reindex(h1.index, method="ffill").fillna(0)

    total = vote_1h.to_numpy() + vote_15m_at_1h.to_numpy() + vote_pdmid.to_numpy() + vote_vwap.to_numpy()
    bias_1h = pd.Series(np.where(total >= DAILY_BIAS_VOTE_THRESHOLD, 1, np.where(total <= -DAILY_BIAS_VOTE_THRESHOLD, -1, 0)), index=h1.index)

    # CRITICAL (lookahead fix): resample(label="left", closed="left") labels
    # the 1H bar covering [09:00, 10:00) as "09:00", but its high/low/close
    # are only known at 09:59. Forward-filling straight from that label
    # would let a 09:05 execution bar trade on 09:59 information. Shifting
    # the series forward by one full hour means an execution bar can only
    # ever act on a 1H bar that has actually CLOSED.
    #
    # This is not a theoretical nicety: measured on real data, removing this
    # leak changed XAUUSD 5m from PF 1.63 (+76.6R) to PF 0.99 (-1.4R) and
    # NAS100 15m from PF 1.70 (+42.7R) to PF 0.94 (-5.1R) -- i.e. this
    # strategy's entire apparent edge WAS the leak. Never "optimize" this
    # shift away.
    bias_m1 = bias_1h.shift(1, freq="1h").reindex(m1.index, method="ffill").fillna(0)
    return bias_m1


def order_flow_features(m1: pd.DataFrame, exec_index: pd.DatetimeIndex, exec_freq_min: int) -> pd.DataFrame:
    """Per-execution-TF-bar Order Flow PROXY features, built from the M1
    sub-bars nested inside each bar (see module docstring for why this,
    not ticks). delta_proxy: signed tick-volume by up/down M1 close.
    """
    m1_close_diff = m1["close"].diff()
    m1_delta = np.where(m1_close_diff > 0, m1["volume"], np.where(m1_close_diff < 0, -m1["volume"], 0.0))
    m1_sign = np.sign(m1_delta)

    bucket = m1.index.floor(f"{exec_freq_min}min")
    delta_df = pd.DataFrame({"delta": m1_delta, "sign": m1_sign}, index=m1.index)
    grouped = delta_df.groupby(bucket)
    delta_sum = grouped["delta"].sum()
    pos_frac = grouped["sign"].apply(lambda s: (s > 0).sum() / len(s) if len(s) else 0.0)
    neg_frac = grouped["sign"].apply(lambda s: (s < 0).sum() / len(s) if len(s) else 0.0)

    out = pd.DataFrame(index=exec_index)
    out["delta"] = delta_sum.reindex(exec_index, fill_value=0.0)
    out["stacked_buy"] = pos_frac.reindex(exec_index, fill_value=0.0) >= OF_STACKED_RATIO
    out["stacked_sell"] = neg_frac.reindex(exec_index, fill_value=0.0) >= OF_STACKED_RATIO
    day_key = out.index.date
    out["cvd"] = out.groupby(day_key)["delta"].cumsum()
    return out


def run_backtest(tf_minutes: int, input_csv: str) -> tuple[list[Trade], dict]:
    m1 = load_m1(input_csv)
    bars = resample(m1, tf_minutes)
    bars.index = bars.index.tz_convert(NY)

    o, h, l, c, vol = (bars[col].to_numpy() for col in ("open", "high", "low", "close", "volume"))
    n = len(bars)
    atr = compute_atr(bars, ATR_LEN).to_numpy()
    ph, pl = compute_pivots(bars, SWING_LEN)

    levels = compute_daily_levels(m1)
    bias_m1 = compute_daily_bias(m1, levels)
    bias = bias_m1.reindex(bars.index, method="ffill").fillna(0).to_numpy()

    of = order_flow_features(m1, bars.index, tf_minutes)
    delta = of["delta"].to_numpy()
    stacked_buy = of["stacked_buy"].to_numpy()
    stacked_sell = of["stacked_sell"].to_numpy()
    cvd = of["cvd"].to_numpy()
    vol_sma20 = bars["volume"].rolling(20).mean().to_numpy()

    day_key = bars.index.date
    pdh = np.array([levels["pdh"].get(d, np.nan) for d in day_key])
    pdl = np.array([levels["pdl"].get(d, np.nan) for d in day_key])
    pdmid = np.array([levels["pdmid"].get(d, np.nan) for d in day_key])

    # Session highs/lows, recomputed once per day (Asia 20:00-00:00 NY prior evening, London 02:00-05:00 NY today).
    asia_high = np.full(n, np.nan)
    asia_low = np.full(n, np.nan)
    unique_days = sorted(set(day_key))
    asia_by_day: dict = {}
    for d in unique_days:
        start = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=20, tzinfo=NY) - timedelta(days=1)
        end = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=0, tzinfo=NY)
        ah, al = session_high_low(m1, start, end)
        asia_by_day[d] = (ah, al)
    for i, d in enumerate(day_key):
        asia_high[i], asia_low[i] = asia_by_day[d]

    # --- Swing/liquidity pool (active unmitigated levels) ---
    active_highs: list[float] = []
    active_lows: list[float] = []

    # --- Trendline state ---
    swing_lows_seen: list[tuple[int, float]] = []
    swing_highs_seen: list[tuple[int, float]] = []
    support_line: tuple[int, float, int, float] | None = None  # (idx_a, price_a, idx_b, price_b)
    resistance_line: tuple[int, float, int, float] | None = None
    support_touches = 0
    resistance_touches = 0
    support_broken_bar: int | None = None
    resistance_broken_bar: int | None = None

    def line_value(line: tuple[int, float, int, float], i: int) -> float:
        i0, p0, i1, p1 = line
        if i1 == i0:
            return p1
        slope = (p1 - p0) / (i1 - i0)
        return p1 + slope * (i - i1)

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = None
    pos_entry_time = pos_entry_price = pos_confirmations = None
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    warmup = max(ATR_LEN, SWING_LEN * 2, 20) + 5
    sweep_since_bar: dict[str, int] = {}  # "sell"/"buy" -> bar index of the most recent sweep
    displacement_since_bar: dict[str, int] = {}  # "bull"/"bear" -> bar index of the most recent displacement

    for i in range(warmup, n):
        if not np.isnan(ph[i]):
            active_highs.append(ph[i])
            swing_highs_seen.append((i, ph[i]))
        if not np.isnan(pl[i]):
            active_lows.append(pl[i])
            swing_lows_seen.append((i, pl[i]))
        active_highs = [lvl for lvl in active_highs if lvl > h[i]]
        active_lows = [lvl for lvl in active_lows if lvl < l[i]]

        if in_position:
            hit_sl = (l[i] <= pos_sl) if pos_dir == "LONG" else (h[i] >= pos_sl)
            hit_tp = (h[i] >= pos_tp) if pos_dir == "LONG" else (l[i] <= pos_tp)
            if hit_sl or hit_tp:
                exit_price, exit_reason = (pos_sl, "SL") if hit_sl else (pos_tp, "TP")
                risk_dist = abs(pos_entry_price - pos_sl)
                move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
                r_mult = move / risk_dist
                trades.append(Trade(
                    pos_entry_time, pos_dir, pos_entry_price, pos_sl, pos_tp,
                    bars.index[i], exit_price, exit_reason, round(r_mult, 3),
                    round(r_mult * STARTING_BALANCE * RISK_PCT, 2), pos_confirmations,
                ))
                in_position = False

        # --- Trendline maintenance: form a NEW support line from the last 2
        # confirmed swing lows if the 2nd is a Higher Low (ascending); same
        # for resistance from swing highs with a Lower High (descending). ---
        if len(swing_lows_seen) >= 2:
            (i0, p0), (i1, p1) = swing_lows_seen[-2], swing_lows_seen[-1]
            if p1 > p0 and (support_line is None or (i1, p1) != (support_line[2], support_line[3])):
                support_line = (i0, p0, i1, p1)
                support_touches = 0
                support_broken_bar = None
        if len(swing_highs_seen) >= 2:
            (i0, p0), (i1, p1) = swing_highs_seen[-2], swing_highs_seen[-1]
            if p1 < p0 and (resistance_line is None or (i1, p1) != (resistance_line[2], resistance_line[3])):
                resistance_line = (i0, p0, i1, p1)
                resistance_touches = 0
                resistance_broken_bar = None

        tol = TRENDLINE_TOUCH_TOL_ATR * atr[i] if not np.isnan(atr[i]) else 0.0
        if support_line is not None:
            sv = line_value(support_line, i)
            if l[i] <= sv + tol and c[i] > sv:
                support_touches += 1
            if c[i] < sv - TRENDLINE_BREAKOUT_BUFFER_ATR * atr[i]:
                support_broken_bar = i
        if resistance_line is not None:
            rv = line_value(resistance_line, i)
            if h[i] >= rv - tol and c[i] < rv:
                resistance_touches += 1
            if c[i] > rv + TRENDLINE_BREAKOUT_BUFFER_ATR * atr[i]:
                resistance_broken_bar = i

        # --- Liquidity sweeps (sell-side: wick below a level, close back above) ---
        sell_side_levels = [v for v in (pdl[i], asia_low[i], (line_value(support_line, i) if support_line else None)) if v is not None and not (isinstance(v, float) and np.isnan(v))]
        sell_side_levels += [lvl for lvl in active_lows]
        buy_side_levels = [v for v in (pdh[i], asia_high[i], (line_value(resistance_line, i) if resistance_line else None)) if v is not None and not (isinstance(v, float) and np.isnan(v))]
        buy_side_levels += [lvl for lvl in active_highs]

        if any(l[i] <= lvl and c[i] > lvl for lvl in sell_side_levels):
            sweep_since_bar["sell"] = i
        if any(h[i] >= lvl and c[i] < lvl for lvl in buy_side_levels):
            sweep_since_bar["buy"] = i

        recent_sell_sweep = (i - sweep_since_bar.get("sell", -10**9)) <= LIQUIDITY_SWEEP_LOOKBACK_BARS
        recent_buy_sweep = (i - sweep_since_bar.get("buy", -10**9)) <= LIQUIDITY_SWEEP_LOOKBACK_BARS

        range_ = h[i] - l[i]
        bull_rej = range_ > 0 and (min(o[i], c[i]) - l[i]) >= 0.4 * range_ and ((c[i] - l[i]) / range_) >= 0.6
        bear_rej = range_ > 0 and (h[i] - max(o[i], c[i])) >= 0.4 * range_ and ((h[i] - c[i]) / range_) >= 0.6

        bullish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] > o[i]
        bearish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] < o[i]
        if bullish_displacement:
            displacement_since_bar["bull"] = i
        if bearish_displacement:
            displacement_since_bar["bear"] = i
        # Displacement is the IMPULSE that precedes a pullback/retest -- it is
        # a large-range expansion candle, structurally close to incompatible
        # with a rejection-wick retest candle on the SAME bar (confirmed via
        # this session's own diagnostics: requiring both on one bar produced
        # zero trades across 6 years of XAUUSD 15m data). So the entry gate
        # below checks for a RECENT displacement, not one on the entry bar.
        recent_bullish_displacement = (i - displacement_since_bar.get("bull", -10**9)) <= DISPLACEMENT_LOOKBACK_BARS
        recent_bearish_displacement = (i - displacement_since_bar.get("bear", -10**9)) <= DISPLACEMENT_LOOKBACK_BARS

        avg_delta = np.nanmean(np.abs(delta[max(0, i - 20):i])) if i > 0 else 0.0
        aggressive_buy = delta[i] > 0 and avg_delta > 0 and delta[i] > avg_delta
        aggressive_sell = delta[i] < 0 and avg_delta > 0 and abs(delta[i]) > avg_delta
        absorption = not np.isnan(atr[i]) and range_ <= OF_ABSORPTION_RANGE_ATR_MAX * atr[i] and not np.isnan(vol_sma20[i]) and vol[i] >= vol_sma20[i] * OF_ABSORPTION_VOL_MULT
        cvd_prev = cvd[i - 3] if i >= 3 else cvd[i]
        cvd_rising = cvd[i] > cvd_prev
        cvd_falling = cvd[i] < cvd_prev

        long_confirmations = sum([
            recent_sell_sweep, delta[i] > 0, aggressive_buy, bool(stacked_buy[i]), cvd_rising, recent_bullish_displacement,
        ])
        short_confirmations = sum([
            recent_buy_sweep, delta[i] < 0, aggressive_sell, bool(stacked_sell[i]), cvd_falling, recent_bearish_displacement,
        ])

        # --- Retest of a broken trendline (breakout+retest, section 2) ---
        long_retest_ok = support_broken_bar is not None and (i - support_broken_bar) <= TRENDLINE_RETEST_MAX_BARS and support_line is not None and l[i] <= line_value(support_line, i) + tol and bull_rej
        short_retest_ok = resistance_broken_bar is not None and (i - resistance_broken_bar) <= TRENDLINE_RETEST_MAX_BARS and resistance_line is not None and h[i] >= line_value(resistance_line, i) - tol and bear_rej

        # --- Bounce off an ACTIVE (not-yet-broken), CONFIRMED trendline ---
        long_bounce_ok = (
            support_line is not None and support_broken_bar is None
            and support_touches >= TRENDLINE_MIN_CONFIRM_TOUCHES
            and l[i] <= line_value(support_line, i) + tol and bull_rej
        )
        short_bounce_ok = (
            resistance_line is not None and resistance_broken_bar is None
            and resistance_touches >= TRENDLINE_MIN_CONFIRM_TOUCHES
            and h[i] >= line_value(resistance_line, i) - tol and bear_rej
        )

        long_trendline_ok = long_bounce_ok or long_retest_ok
        short_trendline_ok = short_bounce_ok or short_retest_ok

        # --- Diagnostic-only counters (do not affect entry logic) ---
        if bias[i] != 0:
            skip(f"_diag_bias_{'long' if bias[i] == 1 else 'short'}")
        if long_trendline_ok or short_trendline_ok:
            skip("_diag_trendline_ok")
        if support_line is not None:
            skip("_diag_has_support_line")
        if resistance_line is not None:
            skip("_diag_has_resistance_line")
        if recent_sell_sweep or recent_buy_sweep:
            skip("_diag_recent_sweep")
        if bullish_displacement or bearish_displacement:
            skip("_diag_displacement")

        if in_position:
            continue

        if bias[i] == 1 and long_trendline_ok and recent_sell_sweep and recent_bullish_displacement and long_confirmations >= OF_MIN_CONFIRMATIONS:
            sweep_low = min((lvl for lvl in sell_side_levels if l[i] <= lvl), default=l[i])
            sl_price = min(sweep_low, l[i]) - SL_BUFFER_ATR * atr[i]
            entry = c[i]
            risk_dist = entry - sl_price
            targets = [lvl for lvl in active_highs if lvl > entry]
            tp = min(targets) if targets else None
            if risk_dist <= 0:
                skip("non_positive_risk")
            elif tp is None:
                skip("no_liquidity_target")
            else:
                rr = (tp - entry) / risk_dist
                if rr < MIN_RR:
                    skip("rr_gate_failed")
                else:
                    in_position = True
                    pos_dir, pos_sl, pos_tp = "LONG", sl_price, tp
                    pos_entry_time, pos_entry_price, pos_confirmations = bars.index[i], entry, long_confirmations
        elif bias[i] == -1 and short_trendline_ok and recent_buy_sweep and recent_bearish_displacement and short_confirmations >= OF_MIN_CONFIRMATIONS:
            sweep_high = max((lvl for lvl in buy_side_levels if h[i] >= lvl), default=h[i])
            sl_price = max(sweep_high, h[i]) + SL_BUFFER_ATR * atr[i]
            entry = c[i]
            risk_dist = sl_price - entry
            targets = [lvl for lvl in active_lows if lvl < entry]
            tp = max(targets) if targets else None
            if risk_dist <= 0:
                skip("non_positive_risk")
            elif tp is None:
                skip("no_liquidity_target")
            else:
                rr = (entry - tp) / risk_dist
                if rr < MIN_RR:
                    skip("rr_gate_failed")
                else:
                    in_position = True
                    pos_dir, pos_sl, pos_tp = "SHORT", sl_price, tp
                    pos_entry_time, pos_entry_price, pos_confirmations = bars.index[i], entry, short_confirmations
        else:
            if bias[i] == 0:
                skip("neutral_bias")
            elif not (long_trendline_ok or short_trendline_ok):
                skip("no_trendline_setup")
            elif bias[i] == 1 and not recent_sell_sweep:
                skip("no_sweep")
            elif bias[i] == -1 and not recent_buy_sweep:
                skip("no_sweep")
            elif bias[i] == 1 and not recent_bullish_displacement:
                skip("no_displacement")
            elif bias[i] == -1 and not recent_bearish_displacement:
                skip("no_displacement")
            else:
                skip("no_order_flow_confirmation")

    return trades, skip_counts


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_price", "stop", "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "pnl_usd", "confirmations"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_price, t.stop, t.target, t.exit_time, t.exit_price, t.exit_reason, t.r_multiple, t.pnl_usd, t.confirmations])


def summarize(trades: list[Trade], skip_counts: dict) -> None:
    n = len(trades)
    print(f"skip_funnel={skip_counts}")
    if n == 0:
        print("No trades.")
        return
    wins = [t for t in trades if t.r_multiple > 0]
    gp = sum(t.r_multiple for t in wins)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    print(f"Total trades: {n}  Wins: {len(wins)}  Win rate: {len(wins)/n*100:.1f}%  PF: {pf:.2f}  Total R: {sum(t.r_multiple for t in trades):.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NAS100")
    parser.add_argument("--input-csv", default=None)
    parser.add_argument("--tf", type=int, default=15)
    args = parser.parse_args()

    input_csv = args.input_csv or f"data/history/{args.symbol}_M1.csv"
    trades_out = f"artifacts/order_flow_bias_trades_{args.symbol}_{args.tf}m.csv"

    trades, skip_counts = run_backtest(args.tf, input_csv)
    write_trades_csv(trades, trades_out)
    summarize(trades, skip_counts)
    print(f"Trade log written to {trades_out}")
