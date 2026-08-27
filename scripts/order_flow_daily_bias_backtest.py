"""Order Flow + Daily Bias + Trendline strategy backtest.

Implements the spec: Daily Bias (1H/15M structure + PDH/PDL/mid + VWAP) sets
the only tradeable direction; a liquidity sweep at a trendline zone plus a
bar-level Order Flow proxy (delta/CVD/absorption/displacement) triggers a
"pending setup"; entry only fires on a subsequent RETEST of the swept zone.
SL sits behind the sweep extreme; TP is modeled as two legs against the
nearest unmitigated opposite-side liquidity pools, minimum 1:2 R:R or the
trade is skipped (NO TRADE).

Usage:
    python -m scripts.order_flow_daily_bias_backtest --symbol XAUUSD --tf 5
    python -m scripts.order_flow_daily_bias_backtest --all

Design notes / simplifications (documented up front, same convention as
scripts/sr_daily_bias_backtest*.py):

- No real tick/DOM data exists in this repo -- only OHLCV bars from the
  broker feed (see data/csv_provider.py). "Order Flow" here is therefore the
  standard bar-level proxy every retail platform without a real trade tape
  uses: delta[i] = volume[i] * close-location-value(i), CVD = its running
  session sum, absorption = high-volume + narrow-range bar, displacement =
  high-volume + wide-range bar closing beyond the sweep in the trade
  direction, "stacked footprint buying/selling" = 2 consecutive same-sign
  delta bars. This is NOT real footprint/DOM data and is disclosed as such.
- Daily Bias is scored from 3 factors (previous-day-mid position, 1H swing
  structure HH/HL vs LH/LL, 1H session VWAP position) and re-evaluated on
  every newly-CLOSED 1H bar (not fixed once at day open), matching how ICT-
  style bias is actually used intraday. Majority vote (>=2 of 3 agree) sets
  Bullish/Bearish; otherwise Neutral, which requires a stricter Order Flow
  score to trade at all.
- Trendline = the most recent pair of ascending confirmed swing lows
  (bullish/support) or descending swing highs (bearish/resistance) on the
  TARGET timeframe, projected forward with a constant slope. Only one
  (the latest) pair is used at a time, matching "connect the swing
  lows/highs" from the spec; a line is only used once its 2 defining pivots
  are confirmed (its 2 "touches").
- TP is modeled as two legs: TP1 (nearest unmitigated opposite liquidity;
  closes tp1_close_pct of size, moves SL to breakeven) and TP2 (next
  nearest; closes the remainder). TP3 ("bigger timeframe liquidity") is
  computed and logged for reference but not simulated as a 3rd leg.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

SYMBOL_FILES = {
    "XAUUSD": "data/history/XAUUSD_M1.csv",
    "EURUSD": "data/history/EURUSD_M1.csv",
    "NAS100": "data/history/USTEC_M1.csv",
}
TIMEFRAMES = (5, 15, 30, 60)


@dataclass
class Config:
    atr_len: int = 14
    vol_sma_len: int = 20
    vol_mult: float = 1.3

    swing_len: int = 3  # pivot left/right bars on the TARGET timeframe
    swing_len_1h: int = 3  # pivot left/right bars on 1H (market structure)
    trendline_lookback: int = 80  # max bar-age of the swing pair used for a trendline
    trendline_tol_atr: float = 0.30  # "in the trendline zone" tolerance

    equal_level_tol_atr: float = 0.15
    max_active_pool: int = 12  # cap on tracked swing highs/lows (perf + relevance)

    displacement_atr_mult: float = 1.2
    absorption_range_atr_mult: float = 0.6
    aggressive_clv: float = 0.5
    of_confirm_min_score: int = 2  # of the 4 optional OF signals, min required
    of_confirm_min_score_neutral: int = 4  # stricter bar when Daily Bias is Neutral
    of_confirm_window: int = 3  # bars after the sweep in which OF+trendline confluence may land

    retest_max_bars: int = 20
    retest_tol_atr: float = 0.30
    require_retest: bool = True

    sl_buffer_atr: float = 0.15
    min_risk_atr: float = 0.3
    max_risk_atr: float = 8.0
    min_rr: float = 2.0
    tp1_close_pct: float = 0.5
    move_sl_to_be_after_tp1: bool = True
    tp_mode: str = "liquidity"  # "liquidity" (nearest unmitigated pool) or "fixed_r" (tp1_r/tp2_r multiples)
    tp1_r: float = 2.0
    tp2_r: float = 3.0

    daily_bias_neutral_requires_strict_of: bool = True
    use_daily_bias: bool = True  # if False, Daily Bias is ignored -- both directions always allowed

    risk_pct: float = 0.01
    starting_balance: float = 100_000.0


@dataclass
class Trade:
    entry_time: pd.Timestamp
    direction: str
    daily_bias: str
    sweep_kind: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float | None
    tp3: float | None
    of_score: int
    leg1_exit_time: pd.Timestamp
    leg1_exit_price: float
    leg1_exit_reason: str
    leg2_exit_time: pd.Timestamp
    leg2_exit_price: float
    leg2_exit_reason: str
    r_multiple: float
    pnl_usd: float


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def load_m1(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append(
                (ny_ts, float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row["volume"]))
            )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.set_index("ts").sort_index()


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------

def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return wilder_smooth(tr, period)


def compute_pivots(df: pd.DataFrame, left: int, right: int) -> tuple[pd.Series, pd.Series]:
    """Confirmed pivot high/low, value placed at the confirmation bar (pivot+right)."""
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


def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    """Typical-price VWAP, reset every NY calendar day. Causal (row-wise cumsum)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    day = df.index.date
    day_s = pd.Series(day, index=df.index)
    cum_pv = pv.groupby(day_s).cumsum()
    cum_v = df["volume"].groupby(day_s).cumsum().replace(0, np.nan)
    return cum_pv / cum_v


def compute_daily_bias_1h(m1: pd.DataFrame, cfg: Config) -> pd.Series:
    """Bias known-from time = the 1H bar's own close time (bar_start + 1h)."""
    h1 = resample(m1, 60)
    daily = resample(m1, 1440)
    return compute_daily_bias_from_h1(h1, daily, cfg)


def compute_daily_bias_from_h1(h1: pd.DataFrame, daily: pd.DataFrame, cfg: Config) -> pd.Series:
    """Same as compute_daily_bias_1h but takes already-built 1H/daily bars --
    used when a native (broker-downloaded) H1 series is available instead of
    being resampled from M1."""
    pdmid_by_date: dict = {}
    prev_h = prev_l = None
    for d, row in daily.iterrows():
        if prev_h is not None:
            pdmid_by_date[d.date()] = (prev_h + prev_l) / 2.0
        prev_h, prev_l = row["high"], row["low"]

    vwap = compute_session_vwap(h1)
    ph, pl = compute_pivots(h1, cfg.swing_len_1h, cfg.swing_len_1h)

    close = h1["close"].to_numpy()
    ph_arr, pl_arr = ph.to_numpy(), pl.to_numpy()
    idx = h1.index
    n = len(h1)

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    bias_vals = np.zeros(n, dtype=int)

    for i in range(n):
        if not np.isnan(ph_arr[i]):
            swing_highs.append(ph_arr[i])
            if len(swing_highs) > 4:
                swing_highs.pop(0)
        if not np.isnan(pl_arr[i]):
            swing_lows.append(pl_arr[i])
            if len(swing_lows) > 4:
                swing_lows.pop(0)

        structure_score = 0
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]:
                structure_score = 1
            elif swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]:
                structure_score = -1

        d = idx[i].date()
        pdmid = pdmid_by_date.get(d)
        pdmid_score = 0
        if pdmid is not None:
            pdmid_score = 1 if close[i] > pdmid else (-1 if close[i] < pdmid else 0)

        vw = vwap.iloc[i]
        vwap_score = 0
        if not np.isnan(vw):
            vwap_score = 1 if close[i] > vw else (-1 if close[i] < vw else 0)

        total = structure_score + pdmid_score + vwap_score
        bias_vals[i] = 1 if total >= 2 else (-1 if total <= -2 else 0)

    known_from = idx + pd.Timedelta(hours=1)
    return pd.Series(bias_vals, index=known_from)


def bias_lookup(bias_series: pd.Series, ts: pd.Timestamp) -> int | None:
    pos = bias_series.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return int(bias_series.iloc[pos])


BIAS_NAME = {1: "Bullish", -1: "Bearish", 0: "Neutral"}


# --------------------------------------------------------------------------
# Liquidity pool helpers
# --------------------------------------------------------------------------

def equal_clusters(levels: list[float], tol: float) -> list[float]:
    out = []
    lv = sorted(levels)
    used = [False] * len(lv)
    for a in range(len(lv)):
        if used[a]:
            continue
        for b in range(a + 1, len(lv)):
            if used[b]:
                continue
            if abs(lv[b] - lv[a]) <= tol:
                out.append((lv[a] + lv[b]) / 2.0)
                used[a] = used[b] = True
                break
    return out


def nearest_targets(entry: float, active: list[float], anchor: float | None, equal_tol: float,
                     above: bool) -> tuple[float | None, float | None, float | None]:
    """Returns (tp1, tp2, tp3) sorted by distance from entry among active pool
    levels + PDH/PDL/session anchor, all strictly beyond `entry` in the given
    direction."""
    cands = [lvl for lvl in active if (lvl > entry if above else lvl < entry)]
    cands += [c for c in equal_clusters(active, equal_tol) if (c > entry if above else c < entry)]
    if anchor is not None and (anchor > entry if above else anchor < entry):
        cands.append(anchor)
    cands = sorted(set(round(c, 8) for c in cands), reverse=not above)
    tp1 = cands[0] if len(cands) > 0 else None
    tp2 = cands[1] if len(cands) > 1 else None
    tp3 = cands[2] if len(cands) > 2 else (anchor if anchor not in (tp1, tp2) else None)
    return tp1, tp2, tp3


# --------------------------------------------------------------------------
# Main backtest
# --------------------------------------------------------------------------

def run_backtest(symbol: str, tf_minutes: int, cfg: Config, input_csv: str | None = None) -> list[Trade]:
    path = input_csv or SYMBOL_FILES[symbol]
    m1 = load_m1(path)
    bars = resample(m1, tf_minutes).copy()
    daily = resample(m1, 1440)
    h1 = resample(m1, 60)
    bias_series = compute_daily_bias_from_h1(h1, daily, cfg)
    return _run_backtest_core(symbol, tf_minutes, cfg, bars, daily, bias_series)


def run_backtest_native_h1(symbol: str, cfg: Config, h1_csv: str) -> list[Trade]:
    """Runs the 60m backtest directly against a broker-downloaded native H1
    CSV (from data/download_history.py) instead of resampling from M1 --
    identical pipeline, just skips the M1->H1 aggregation step since the
    file's bars already ARE H1. Daily Bias uses this same H1 series."""
    bars = load_m1(h1_csv)  # load_m1 just parses OHLCV+tz; works for any bar size
    daily = resample(bars, 1440)
    bias_series = compute_daily_bias_from_h1(bars, daily, cfg)
    return _run_backtest_core(symbol, 60, cfg, bars, daily, bias_series)


def _run_backtest_core(
    symbol: str, tf_minutes: int, cfg: Config, bars: pd.DataFrame, daily: pd.DataFrame, bias_series: pd.Series,
) -> list[Trade]:
    bars = bars.copy()
    pdhl_by_date: dict = {}
    prev_h = prev_l = None
    for d, row in daily.iterrows():
        if prev_h is not None:
            pdhl_by_date[d.date()] = (prev_h, prev_l, (prev_h + prev_l) / 2.0)
        prev_h, prev_l = row["high"], row["low"]

    bars["atr"] = compute_atr(bars, cfg.atr_len)
    bars["vol_sma"] = bars["volume"].rolling(cfg.vol_sma_len).mean()
    range_ = bars["high"] - bars["low"]
    clv = np.where(range_ > 0, (2 * bars["close"] - bars["high"] - bars["low"]) / range_, 0.0)
    bars["clv"] = clv
    bars["delta"] = bars["volume"] * clv
    ph, pl = compute_pivots(bars, cfg.swing_len, cfg.swing_len)
    bars["pivot_high"] = ph
    bars["pivot_low"] = pl

    idx = bars.index
    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    l = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    vol = bars["volume"].to_numpy()
    atr = bars["atr"].to_numpy()
    vol_sma = bars["vol_sma"].to_numpy()
    rng = range_.to_numpy()
    delta = bars["delta"].to_numpy()
    clv_arr = bars["clv"].to_numpy()
    piv_h = bars["pivot_high"].to_numpy()
    piv_l = bars["pivot_low"].to_numpy()

    n = len(bars)
    warmup = max(cfg.atr_len, cfg.vol_sma_len, cfg.swing_len * 2) + 5

    swing_highs: list[tuple[int, float]] = []  # (bar_idx, value), confirmed
    swing_lows: list[tuple[int, float]] = []
    active_highs: list[float] = []
    active_lows: list[float] = []

    pdh_val = pdl_val = pdmid_val = None
    cur_date = None
    sess_high = -np.inf
    sess_low = np.inf

    trades: list[Trade] = []
    skip_counts: dict[str, int] = {}
    funnel: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    def tick(stage: str) -> None:
        funnel[stage] = funnel.get(stage, 0) + 1

    pending: dict | None = None  # confirmed setup awaiting retest
    recent_sweep_long: dict | None = None  # sweep seen, awaiting OF+trendline confluence
    recent_sweep_short: dict | None = None
    in_position = False
    pos: dict = {}

    risk_amount = cfg.starting_balance * cfg.risk_pct

    for i in range(warmup, n):
        ts = idx[i]
        d = ts.date()
        if d != cur_date:
            cur_date = d
            sess_high, sess_low = h[i], l[i]
            pdhl = pdhl_by_date.get(d)
            if pdhl is not None:
                pdh_val, pdl_val, pdmid_val = pdhl
            else:
                pdh_val = pdl_val = pdmid_val = None
        sess_high_pre, sess_low_pre = sess_high, sess_low

        if not np.isnan(piv_h[i]):
            swing_highs.append((i, piv_h[i]))
            active_highs.append(piv_h[i])
            swing_highs = swing_highs[-cfg.max_active_pool:]
        if not np.isnan(piv_l[i]):
            swing_lows.append((i, piv_l[i]))
            active_lows.append(piv_l[i])
            swing_lows = swing_lows[-cfg.max_active_pool:]

        active_highs = [lvl for lvl in active_highs if lvl > h[i]][-cfg.max_active_pool:]
        active_lows = [lvl for lvl in active_lows if lvl < l[i]][-cfg.max_active_pool:]
        if pdh_val is not None and h[i] > pdh_val:
            pdh_val = None
        if pdl_val is not None and l[i] < pdl_val:
            pdl_val = None

        # -- trendline: connect the latest confirmed swing low/high to the most
        # recent EARLIER one that keeps the line ascending/descending (not
        # necessarily the literally-adjacent pair -- a single lower-low pivot
        # in between shouldn't kill an otherwise-valid rising trendline). --
        bull_line = None
        recent_lows = [(bi, v) for bi, v in swing_lows if i - bi <= cfg.trendline_lookback]
        if len(recent_lows) >= 2:
            bi2, v2 = recent_lows[-1]
            for bi1, v1 in reversed(recent_lows[:-1]):
                if v1 < v2:
                    slope = (v2 - v1) / (bi2 - bi1)
                    bull_line = v2 + slope * (i - bi2)
                    break

        bear_line = None
        recent_highs = [(bi, v) for bi, v in swing_highs if i - bi <= cfg.trendline_lookback]
        if len(recent_highs) >= 2:
            bi2, v2 = recent_highs[-1]
            for bi1, v1 in reversed(recent_highs[:-1]):
                if v1 > v2:
                    slope = (v2 - v1) / (bi2 - bi1)
                    bear_line = v2 + slope * (i - bi2)
                    break

        if np.isnan(atr[i]) or atr[i] == 0:
            sess_high, sess_low = max(sess_high, h[i]), min(sess_low, l[i])
            skip("warmup_atr")
            continue

        if cfg.use_daily_bias:
            bias_val = bias_lookup(bias_series, ts)
            if bias_val is None:
                sess_high, sess_low = max(sess_high, h[i]), min(sess_low, l[i])
                skip("no_daily_bias_yet")
                continue
            bias_name = BIAS_NAME[bias_val]
        else:
            bias_val, bias_name = 0, "Ignored"
        tick(f"bias_{bias_name}")

        # -- order flow proxy signals on bar i --
        displacement_up = rng[i] >= cfg.displacement_atr_mult * atr[i] and \
            (vol_sma[i] and vol[i] >= vol_sma[i] * cfg.vol_mult) and \
            rng[i] > 0 and (c[i] - l[i]) / rng[i] >= 0.66
        displacement_dn = rng[i] >= cfg.displacement_atr_mult * atr[i] and \
            (vol_sma[i] and vol[i] >= vol_sma[i] * cfg.vol_mult) and \
            rng[i] > 0 and (h[i] - c[i]) / rng[i] >= 0.66
        absorption = rng[i] <= cfg.absorption_range_atr_mult * atr[i] and \
            (vol_sma[i] and vol[i] >= vol_sma[i] * cfg.vol_mult)
        aggressive_buy = clv_arr[i] >= cfg.aggressive_clv
        aggressive_sell = clv_arr[i] <= -cfg.aggressive_clv
        cvd_flip_up = delta[i] > 0 and delta[i - 1] <= 0
        cvd_flip_dn = delta[i] < 0 and delta[i - 1] >= 0
        stacked_buy = delta[i] > 0 and delta[i - 1] > 0
        stacked_sell = delta[i] < 0 and delta[i - 1] < 0

        of_score_long = sum([absorption, cvd_flip_up, aggressive_buy, stacked_buy])
        of_score_short = sum([absorption, cvd_flip_dn, aggressive_sell, stacked_sell])

        min_score = cfg.of_confirm_min_score
        if cfg.use_daily_bias and bias_val == 0:
            if not cfg.daily_bias_neutral_requires_strict_of:
                sess_high, sess_low = max(sess_high, h[i]), min(sess_low, l[i])
                skip("neutral_bias_disabled")
                continue
            min_score = cfg.of_confirm_min_score_neutral

        eps = atr[i] * 0.01

        # -- sweep + reclaim candidates (LONG: sell-side liquidity) --
        long_candidates: list[tuple[str, float]] = []
        if pdl_val is not None:
            long_candidates.append(("PDL", pdl_val))
        long_candidates.append(("SessionLow", sess_low_pre))
        if active_lows:
            long_candidates.append(("SwingLow", max([x for x in active_lows if x < c[i]], default=active_lows[-1])))
        for lvl in equal_clusters(active_lows, cfg.equal_level_tol_atr * atr[i]):
            long_candidates.append(("EqualLows", lvl))
        if bull_line is not None:
            long_candidates.append(("Trendline", bull_line))

        short_candidates: list[tuple[str, float]] = []
        if pdh_val is not None:
            short_candidates.append(("PDH", pdh_val))
        short_candidates.append(("SessionHigh", sess_high_pre))
        if active_highs:
            short_candidates.append(("SwingHigh", min([x for x in active_highs if x > c[i]], default=active_highs[-1])))
        for lvl in equal_clusters(active_highs, cfg.equal_level_tol_atr * atr[i]):
            short_candidates.append(("EqualHighs", lvl))
        if bear_line is not None:
            short_candidates.append(("Trendline", bear_line))

        sweep_long = None
        for kind, lvl in long_candidates:
            if l[i] < lvl - eps and c[i] > lvl:
                sweep_long = (kind, lvl)
                break
        sweep_short = None
        for kind, lvl in short_candidates:
            if h[i] > lvl + eps and c[i] < lvl:
                sweep_short = (kind, lvl)
                break

        trend_zone_long = bull_line is not None and abs(c[i] - bull_line) <= cfg.trendline_tol_atr * atr[i]
        trend_zone_short = bear_line is not None and abs(c[i] - bear_line) <= cfg.trendline_tol_atr * atr[i]

        if bull_line is not None:
            tick("bull_line_exists")
        if bear_line is not None:
            tick("bear_line_exists")
        if bias_val != -1:
            tick("bias_allows_long")
        if sweep_long is not None:
            tick("sweep_long")
        if sweep_long is not None and displacement_up:
            tick("sweep_long+displacement")
        if sweep_long is not None and displacement_up and of_score_long >= min_score:
            tick("sweep_long+displacement+ofscore")
        if bias_val != 1:
            tick("bias_allows_short")
        if sweep_short is not None:
            tick("sweep_short")
        if sweep_short is not None and displacement_dn:
            tick("sweep_short+displacement")
        if sweep_short is not None and displacement_dn and of_score_short >= min_score:
            tick("sweep_short+displacement+ofscore")

        of_ok_long = displacement_up and of_score_long >= min_score
        of_ok_short = displacement_dn and of_score_short >= min_score
        if of_ok_long:
            tick("of_ok_long")
        if of_ok_short:
            tick("of_ok_short")

        # -- manage open position first --
        # Both legs are always tracked as open from entry; leg2's simulated
        # target falls back to TP1 when no TP2 liquidity level exists (full
        # close at TP1 for the whole position), matching the module docstring.
        if in_position:
            leg1_open, leg2_open = pos["leg1_open"], pos["leg2_open"]
            direction = pos["direction"]
            leg2_target = pos["tp2"] if pos["tp2"] is not None else pos["tp1"]
            leg2_label = "TP2" if pos["tp2"] is not None else "TP1"
            if leg1_open:
                sl_now = pos["sl"]
                hit_sl = l[i] <= sl_now if direction == "LONG" else h[i] >= sl_now
                hit_tp1 = h[i] >= pos["tp1"] if direction == "LONG" else l[i] <= pos["tp1"]
                if hit_sl:
                    pos["leg1_exit"] = (ts, sl_now, "SL")
                    pos["leg1_open"] = False
                    if leg2_open:
                        pos["leg2_exit"] = (ts, sl_now, "SL")
                        pos["leg2_open"] = False
                elif hit_tp1:
                    pos["leg1_exit"] = (ts, pos["tp1"], "TP1")
                    pos["leg1_open"] = False
                    if cfg.move_sl_to_be_after_tp1:
                        pos["sl"] = pos["entry"]
                    hit_tp2_same_bar = (h[i] >= leg2_target) if direction == "LONG" else (l[i] <= leg2_target)
                    if leg2_open and hit_tp2_same_bar:
                        pos["leg2_exit"] = (ts, leg2_target, leg2_label)
                        pos["leg2_open"] = False
            else:
                sl_now = pos["sl"]
                if leg2_open:
                    hit_sl = l[i] <= sl_now if direction == "LONG" else h[i] >= sl_now
                    hit_tp2 = (h[i] >= leg2_target) if direction == "LONG" else (l[i] <= leg2_target)
                    if hit_sl:
                        pos["leg2_exit"] = (ts, sl_now, "SL_BE" if sl_now == pos["entry"] else "SL")
                        pos["leg2_open"] = False
                    elif hit_tp2:
                        pos["leg2_exit"] = (ts, leg2_target, leg2_label)
                        pos["leg2_open"] = False

            if not pos["leg1_open"] and not pos["leg2_open"]:
                risk_dist = pos["risk_dist"]
                mv1 = (pos["leg1_exit"][1] - pos["entry"]) if direction == "LONG" else (pos["entry"] - pos["leg1_exit"][1])
                mv2 = (pos["leg2_exit"][1] - pos["entry"]) if direction == "LONG" else (pos["entry"] - pos["leg2_exit"][1])
                r1, r2 = mv1 / risk_dist, mv2 / risk_dist
                r_total = cfg.tp1_close_pct * r1 + (1 - cfg.tp1_close_pct) * r2
                trades.append(Trade(
                    pos["entry_time"], direction, pos["bias_name"], pos["sweep_kind"], pos["entry"],
                    pos["sl_orig"], pos["tp1"], pos["tp2"], pos["tp3"], pos["of_score"],
                    pos["leg1_exit"][0], pos["leg1_exit"][1], pos["leg1_exit"][2],
                    pos["leg2_exit"][0], pos["leg2_exit"][1], pos["leg2_exit"][2],
                    round(r_total, 3), round(r_total * risk_amount, 2),
                ))
                in_position = False

        # -- new sweep detection (only when flat, no pending, not already tracking) --
        if not in_position and pending is None:
            if recent_sweep_long is None and bias_val != -1 and sweep_long is not None:
                recent_sweep_long = dict(
                    level=sweep_long[1], kind=sweep_long[0], extreme=l[i], atr=atr[i], sweep_bar=i,
                    bias_name=bias_name, trend_ok=False, of_ok=False, of_score=0,
                )
            if recent_sweep_short is None and bias_val != 1 and sweep_short is not None:
                recent_sweep_short = dict(
                    level=sweep_short[1], kind=sweep_short[0], extreme=h[i], atr=atr[i], sweep_bar=i,
                    bias_name=bias_name, trend_ok=False, of_ok=False, of_score=0,
                )

        # -- recent-sweep trackers: sweep, then displacement/OF-score and the
        # trendline-zone touch each just need to occur SOMEWHERE within the
        # next `of_confirm_window` bars (not all on the identical candle) --
        if recent_sweep_long is not None:
            rsl = recent_sweep_long
            if trend_zone_long:
                rsl["trend_ok"] = True
            if of_ok_long:
                rsl["of_ok"] = True
                rsl["of_score"] = max(rsl["of_score"], of_score_long)
            invalid_level = rsl["extreme"] - cfg.sl_buffer_atr * rsl["atr"]
            if c[i] < invalid_level:
                recent_sweep_long = None
            elif rsl["trend_ok"] and rsl["of_ok"]:
                tick("setup_long_total")
                pending = dict(
                    direction="LONG", sweep_level=rsl["level"], sweep_extreme=rsl["extreme"], atr=rsl["atr"],
                    setup_bar=i, bias_name=rsl["bias_name"], sweep_kind=rsl["kind"], of_score=rsl["of_score"],
                )
                recent_sweep_long = None
            elif i - rsl["sweep_bar"] > cfg.of_confirm_window:
                recent_sweep_long = None

        if recent_sweep_short is not None:
            rss = recent_sweep_short
            if trend_zone_short:
                rss["trend_ok"] = True
            if of_ok_short:
                rss["of_ok"] = True
                rss["of_score"] = max(rss["of_score"], of_score_short)
            invalid_level = rss["extreme"] + cfg.sl_buffer_atr * rss["atr"]
            if c[i] > invalid_level:
                recent_sweep_short = None
            elif rss["trend_ok"] and rss["of_ok"]:
                tick("setup_short_total")
                pending = dict(
                    direction="SHORT", sweep_level=rss["level"], sweep_extreme=rss["extreme"], atr=rss["atr"],
                    setup_bar=i, bias_name=rss["bias_name"], sweep_kind=rss["kind"], of_score=rss["of_score"],
                )
                recent_sweep_short = None
            elif i - rss["sweep_bar"] > cfg.of_confirm_window:
                recent_sweep_short = None

        # -- pending setup expiry / invalidation / retest trigger --
        # (runs last so a `pending` just created above by the tracker logic
        # gets evaluated the same bar -- relevant when require_retest=False,
        # which enters immediately on the confirmation bar itself.)
        if pending is not None and not in_position:
            if i - pending["setup_bar"] > cfg.retest_max_bars:
                pending = None
            else:
                direction = pending["direction"]
                invalid_level = pending["sweep_extreme"] - cfg.sl_buffer_atr * pending["atr"] if direction == "LONG" \
                    else pending["sweep_extreme"] + cfg.sl_buffer_atr * pending["atr"]
                invalidated = (c[i] < invalid_level) if direction == "LONG" else (c[i] > invalid_level)
                if invalidated:
                    pending = None
                else:
                    if not cfg.require_retest:
                        retest_ok = True
                    elif i > pending["setup_bar"]:
                        if direction == "LONG":
                            retest_ok = l[i] <= pending["sweep_level"] + cfg.retest_tol_atr * pending["atr"] and c[i] > pending["sweep_level"]
                        else:
                            retest_ok = h[i] >= pending["sweep_level"] - cfg.retest_tol_atr * pending["atr"] and c[i] < pending["sweep_level"]
                    else:
                        retest_ok = False
                    if retest_ok:
                        tick("retest_triggered")
                        entry_price = c[i]
                        if direction == "LONG":
                            sl_price = pending["sweep_extreme"] - cfg.sl_buffer_atr * pending["atr"]
                            risk_dist = entry_price - sl_price
                        else:
                            sl_price = pending["sweep_extreme"] + cfg.sl_buffer_atr * pending["atr"]
                            risk_dist = sl_price - entry_price
                        ok_risk = cfg.min_risk_atr * pending["atr"] <= risk_dist <= cfg.max_risk_atr * pending["atr"]
                        if not ok_risk or risk_dist <= 0:
                            skip("risk_out_of_bounds")
                            pending = None
                        else:
                            above = direction == "LONG"
                            if cfg.tp_mode == "fixed_r":
                                sign = 1 if above else -1
                                tp1 = entry_price + sign * cfg.tp1_r * risk_dist
                                tp2 = entry_price + sign * cfg.tp2_r * risk_dist
                                tp3 = None
                            else:
                                anchor = pdh_val if above else pdl_val
                                tp1, tp2, tp3 = nearest_targets(
                                    entry_price, active_highs if above else active_lows, anchor,
                                    cfg.equal_level_tol_atr * pending["atr"], above,
                                )
                            if tp1 is None:
                                skip("no_liquidity_target")
                                pending = None
                            else:
                                reward1 = (tp1 - entry_price) if above else (entry_price - tp1)
                                if reward1 / risk_dist < cfg.min_rr:
                                    skip("reward_too_small")
                                    pending = None
                                else:
                                    in_position = True
                                    pos = dict(
                                        direction=direction, entry=entry_price, entry_time=ts,
                                        sl=sl_price, sl_orig=sl_price, tp1=tp1, tp2=tp2, tp3=tp3,
                                        risk_dist=risk_dist, leg1_open=True, leg2_open=True,
                                        bias_name=pending["bias_name"], sweep_kind=pending["sweep_kind"],
                                        of_score=pending["of_score"],
                                    )
                                    pending = None

        sess_high, sess_low = max(sess_high, h[i]), min(sess_low, l[i])

    print(f"{symbol} {tf_minutes}m bars={n} trades={len(trades)} skip_funnel={skip_counts}")
    print(f"  funnel={funnel}")
    return trades


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def write_trades_csv(trades: list[Trade], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "entry_time", "direction", "daily_bias", "sweep_kind", "entry_price", "sl", "tp1", "tp2", "tp3",
            "of_score", "leg1_exit_time", "leg1_exit_price", "leg1_exit_reason",
            "leg2_exit_time", "leg2_exit_price", "leg2_exit_reason", "r_multiple", "pnl_usd",
        ])
        for t in trades:
            w.writerow([
                t.entry_time, t.direction, t.daily_bias, t.sweep_kind, t.entry_price, t.sl, t.tp1, t.tp2, t.tp3,
                t.of_score, t.leg1_exit_time, t.leg1_exit_price, t.leg1_exit_reason,
                t.leg2_exit_time, t.leg2_exit_price, t.leg2_exit_reason, t.r_multiple, t.pnl_usd,
            ])


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(trades=0, win_rate=0.0, profit_factor=0.0, total_r=0.0, avg_r=0.0, net_pnl=0.0)
    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = abs(sum(t.pnl_usd for t in losses))
    pf = gp / gl if gl > 0 else float("inf")
    total_r = sum(t.r_multiple for t in trades)
    return dict(
        trades=n, win_rate=round(len(wins) / n * 100, 1), profit_factor=round(pf, 2),
        total_r=round(total_r, 2), avg_r=round(total_r / n, 3),
        net_pnl=round(sum(t.pnl_usd for t in trades), 2),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSD", choices=sorted(SYMBOL_FILES))
    parser.add_argument("--tf", type=int, default=5, choices=TIMEFRAMES)
    parser.add_argument("--all", action="store_true", help="run every symbol x timeframe combo")
    args = parser.parse_args()

    cfg = Config()
    combos = list(itertools.product(SYMBOL_FILES, TIMEFRAMES)) if args.all else [(args.symbol, args.tf)]

    summary_rows = []
    for sym, tf in combos:
        trades = run_backtest(sym, tf, cfg)
        out_path = f"artifacts/order_flow_daily_bias_trades_{sym}_{tf}m.csv"
        write_trades_csv(trades, out_path)
        stats = summarize(trades)
        stats.update(symbol=sym, tf=f"{tf}m")
        summary_rows.append(stats)
        print(f"  -> {stats}")

    if args.all:
        summary_path = "artifacts/order_flow_daily_bias_summary.csv"
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["symbol", "tf", "trades", "win_rate", "profit_factor", "total_r", "avg_r", "net_pnl"])
            w.writeheader()
            for row in summary_rows:
                w.writerow(row)
        print(f"\nSummary written to {summary_path}")
