"""ICT Power of Three (PO3) strategy backtest.

Implements the user's full written spec: Daily Bias (1H structure + PDH/PDL
discount/premium), NY opening-range Accumulation (09:30-10:00 NY, reusing
the exact window strategy/ny_open_accumulation_breakout.py already
validated), Manipulation (liquidity sweep of the range/session/PDH-PDL),
Confirmation (displacement + Market Structure Shift), Entry (FVG
retracement), SL (beyond the sweep extreme), TP (nearest opposing
liquidity, min 1:2 RR).

Per the spec's own emphasis ("bu 4-dən biri yoxdursa keyfiyyət aşağı
düşür"), Sweep + Displacement + MSS + FVG-retest are treated as HARD
requirements, not a soft score -- the 8-question "A+ setup" checklist in
the spec is a discretionary manual-trading heuristic, not meant as a
literal numeric backtest gate.

Usage:
    python -m scripts.po3_backtest --symbol NAS100 --tf 5
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

ATR_LEN = 14
SWING_LEN = 3                     # short-term swing for MSS (spec: "son short-term lower high qırılır" -- tight/local, not the 5-10 bar swings used for trendlines elsewhere)
RANGE_START = "09:30"
RANGE_END = "10:00"
MIN_GAP_POINTS_ATR = 0.05          # FVG min gap, ATR-scaled so it works across TFs/instruments
SWEEP_LOOKBACK_BARS = 20           # manipulation must follow accumulation within this many bars
DISPLACEMENT_ATR_MULT = 2.0
DISPLACEMENT_LOOKBACK_BARS = 5     # MSS/FVG can follow the displacement bar by a few bars, not only the same one
MSS_LOOKBACK_BARS = 10
FVG_RETEST_MAX_BARS = 20
SL_BUFFER_ATR = 0.15
MIN_RR = 2.0


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
    daily = resample(m1, 1440)
    daily.index = daily.index.tz_convert(NY)
    pdh_by_date, pdl_by_date, pdmid_by_date = {}, {}, {}
    dates = list(daily.index.date)
    for i in range(1, len(dates)):
        prev = daily.iloc[i - 1]
        d = dates[i]
        pdh_by_date[d] = float(prev["high"])
        pdl_by_date[d] = float(prev["low"])
        pdmid_by_date[d] = float((prev["high"] + prev["low"]) / 2)
    return {"pdh": pdh_by_date, "pdl": pdl_by_date, "pdmid": pdmid_by_date}


def structure_bias_votes(bars: pd.DataFrame, swing_len: int) -> pd.Series:
    """Same HH/HL vs LH/LL vote used by scripts/order_flow_bias_backtest.py."""
    ph, pl = compute_pivots(bars, swing_len)
    n = len(bars)
    votes = np.zeros(n)
    highs = [(i, v) for i, v in enumerate(ph) if not np.isnan(v)]
    lows = [(i, v) for i, v in enumerate(pl) if not np.isnan(v)]
    prev_high = prev_low = cur_high = cur_low = None
    hi_ptr = lo_ptr = 0
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
    """1H structure vote + PDH/PDL discount/premium vote -- BOTH must agree
    (bullish: structure up AND price below PD-mid/in discount; bearish:
    mirrored). Reindexed to M1 via forward-fill for any execution TF.
    """
    h1 = resample(m1, 60)
    h1.index = h1.index.tz_convert(NY)
    vote_structure = structure_bias_votes(h1, swing_len=3)

    day_key = h1.index.date
    pdmid = pd.Series([levels["pdmid"].get(d, np.nan) for d in day_key], index=h1.index)
    # Discount = price below PD-mid (favors LONG per ICT convention); premium = above (favors SHORT).
    vote_zone = np.sign(pdmid - h1["close"]).fillna(0)

    total = vote_structure.to_numpy() + vote_zone.to_numpy()
    bias_1h = pd.Series(np.where(total >= 2, 1, np.where(total <= -2, -1, 0)), index=h1.index)

    # CRITICAL (lookahead fix) -- see the identical comment in
    # scripts/order_flow_bias_backtest.py::compute_daily_bias. The 1H bar
    # labelled 09:00 is only complete at 09:59, so its bias may not be
    # forward-filled from the 09:00 label; shift it a full hour so only
    # CLOSED 1H bars are ever used. Measured impact on the sibling
    # strategy: PF 1.63 -> 0.99. Never remove this shift.
    return bias_1h.shift(1, freq="1h").reindex(m1.index, method="ffill").fillna(0)


def run_backtest(tf_minutes: int, input_csv: str) -> tuple[list[Trade], dict]:
    m1 = load_m1(input_csv)
    bars = resample(m1, tf_minutes)
    bars.index = bars.index.tz_convert(NY)

    o, h, l, c = (bars[col].to_numpy() for col in ("open", "high", "low", "close"))
    n = len(bars)
    atr = compute_atr(bars, ATR_LEN).to_numpy()
    ph, pl = compute_pivots(bars, SWING_LEN)

    levels = compute_daily_levels(m1)
    bias_m1 = compute_daily_bias(m1, levels)
    bias = bias_m1.reindex(bars.index, method="ffill").fillna(0).to_numpy()

    day_key = bars.index.date
    pdh = np.array([levels["pdh"].get(d, np.nan) for d in day_key])
    pdl = np.array([levels["pdl"].get(d, np.nan) for d in day_key])

    unique_days = sorted(set(day_key))
    session_by_day: dict = {}
    for d in unique_days:
        rs_h, rs_m = map(int, RANGE_START.split(":"))
        re_h, re_m = map(int, RANGE_END.split(":"))
        r_start = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=rs_h, minute=rs_m, tzinfo=NY)
        r_end = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=re_h, minute=re_m, tzinfo=NY)
        rh, rl = session_high_low(m1, r_start, r_end)
        asia_start = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=20, tzinfo=NY) - timedelta(days=1)
        asia_end = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=0, tzinfo=NY)
        ah, al = session_high_low(m1, asia_start, asia_end)
        london_start = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=2, tzinfo=NY)
        london_end = pd.Timestamp.combine(d, pd.Timestamp.min.time()).replace(hour=5, tzinfo=NY)
        lh, ll = session_high_low(m1, london_start, london_end)
        session_by_day[d] = (rh, rl, ah, al, lh, ll, r_end)

    range_high = np.array([session_by_day[d][0] for d in day_key])
    range_low = np.array([session_by_day[d][1] for d in day_key])
    asia_high = np.array([session_by_day[d][2] for d in day_key])
    asia_low = np.array([session_by_day[d][3] for d in day_key])
    london_high = np.array([session_by_day[d][4] for d in day_key])
    london_low = np.array([session_by_day[d][5] for d in day_key])
    range_end_ts = np.array([session_by_day[d][6] for d in day_key])
    after_range = bars.index.to_numpy() >= pd.DatetimeIndex(range_end_ts).to_numpy()

    active_highs: list[float] = []
    active_lows: list[float] = []

    trades: list[Trade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = None
    pos_entry_time = pos_entry_price = None
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    warmup = max(ATR_LEN, SWING_LEN * 2, 20) + 5
    sweep_since: dict[str, int] = {}
    displacement_since: dict[str, int] = {}
    mss_since: dict[str, int] = {}
    fvg_bull: list[tuple[int, float, float]] = []  # (bar_idx formed, lower, upper)
    fvg_bear: list[tuple[int, float, float]] = []

    recent_swing_high = recent_swing_low = None  # for MSS: most recent confirmed opposite-type pivot

    for i in range(warmup, n):
        if not np.isnan(ph[i]):
            active_highs.append(ph[i])
        if not np.isnan(pl[i]):
            active_lows.append(pl[i])
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
                    round(r_mult * STARTING_BALANCE * RISK_PCT, 2),
                ))
                in_position = False

        # --- Manipulation: sweep of range/session/PDH-PDL, only after the accumulation window closed ---
        if after_range[i]:
            sell_side = [v for v in (range_low[i], asia_low[i], london_low[i], pdl[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))]
            buy_side = [v for v in (range_high[i], asia_high[i], london_high[i], pdh[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))]
            if sell_side and l[i] <= min(sell_side) and c[i] > min(sell_side):
                sweep_since["sell"] = i
            if buy_side and h[i] >= max(buy_side) and c[i] < max(buy_side):
                sweep_since["buy"] = i

        # --- Displacement ---
        range_ = h[i] - l[i]
        bullish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] > o[i]
        bearish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] < o[i]
        if bullish_displacement:
            displacement_since["bull"] = i
        if bearish_displacement:
            displacement_since["bear"] = i

        # --- MSS: displacement's close breaks the most recent confirmed opposite-type swing ---
        if not np.isnan(ph[i]):
            recent_swing_high = ph[i]
        if not np.isnan(pl[i]):
            recent_swing_low = pl[i]
        recent_bull_disp = (i - displacement_since.get("bull", -10**9)) <= DISPLACEMENT_LOOKBACK_BARS
        recent_bear_disp = (i - displacement_since.get("bear", -10**9)) <= DISPLACEMENT_LOOKBACK_BARS
        if recent_bull_disp and recent_swing_high is not None and c[i] > recent_swing_high:
            mss_since["bull"] = i
        if recent_bear_disp and recent_swing_low is not None and c[i] < recent_swing_low:
            mss_since["bear"] = i
        recent_bull_mss = (i - mss_since.get("bull", -10**9)) <= MSS_LOOKBACK_BARS
        recent_bear_mss = (i - mss_since.get("bear", -10**9)) <= MSS_LOOKBACK_BARS

        # --- FVG formation (3-bar imbalance, ATR-scaled min gap) ---
        if i >= 2:
            b0h, b0l = h[i - 2], l[i - 2]
            b2h, b2l = h[i], l[i]
            min_gap = MIN_GAP_POINTS_ATR * atr[i] if not np.isnan(atr[i]) else 0.0
            if b2l - b0h >= min_gap:
                fvg_bull.append((i, b0h, b2l))
            if b0l - b2h >= min_gap:
                fvg_bear.append((i, b2h, b0l))
        fvg_bull = [f for f in fvg_bull if i - f[0] <= FVG_RETEST_MAX_BARS and l[i] > f[1] * 0.999]  # drop stale or already-broken-through
        fvg_bear = [f for f in fvg_bear if i - f[0] <= FVG_RETEST_MAX_BARS and h[i] < f[2] * 1.001]

        recent_sell_sweep = (i - sweep_since.get("sell", -10**9)) <= SWEEP_LOOKBACK_BARS
        recent_buy_sweep = (i - sweep_since.get("buy", -10**9)) <= SWEEP_LOOKBACK_BARS

        long_fvg_retest = None
        for idx, lower, upper in fvg_bull:
            if idx < i and l[i] <= upper:
                long_fvg_retest = (lower, upper)
                break
        short_fvg_retest = None
        for idx, lower, upper in fvg_bear:
            if idx < i and h[i] >= lower:
                short_fvg_retest = (lower, upper)
                break

        if in_position:
            continue

        long_ok = bias[i] == 1 and recent_sell_sweep and recent_bull_disp and recent_bull_mss and long_fvg_retest is not None
        short_ok = bias[i] == -1 and recent_buy_sweep and recent_bear_disp and recent_bear_mss and short_fvg_retest is not None

        if long_ok:
            sweep_low = min([v for v in (range_low[i], asia_low[i], london_low[i], pdl[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))], default=l[i])
            sl_price = min(sweep_low, l[i]) - SL_BUFFER_ATR * atr[i]
            entry = long_fvg_retest[1]  # upper edge of the bullish FVG (near edge for a retracement fill)
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
                    pos_entry_time, pos_entry_price = bars.index[i], entry
        elif short_ok:
            sweep_high = max([v for v in (range_high[i], asia_high[i], london_high[i], pdh[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))], default=h[i])
            sl_price = max(sweep_high, h[i]) + SL_BUFFER_ATR * atr[i]
            entry = short_fvg_retest[0]  # lower edge of the bearish FVG
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
                    pos_entry_time, pos_entry_price = bars.index[i], entry
        else:
            if bias[i] == 0:
                skip("neutral_bias")
            elif bias[i] == 1 and not recent_sell_sweep:
                skip("no_sweep")
            elif bias[i] == -1 and not recent_buy_sweep:
                skip("no_sweep")
            elif bias[i] == 1 and not recent_bull_disp:
                skip("no_displacement")
            elif bias[i] == -1 and not recent_bear_disp:
                skip("no_displacement")
            elif bias[i] == 1 and not recent_bull_mss:
                skip("no_mss")
            elif bias[i] == -1 and not recent_bear_mss:
                skip("no_mss")
            else:
                skip("no_fvg_retest")

    return trades, skip_counts


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_price", "stop", "target", "exit_time", "exit_price", "exit_reason", "r_multiple", "pnl_usd"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_price, t.stop, t.target, t.exit_time, t.exit_price, t.exit_reason, t.r_multiple, t.pnl_usd])


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
    parser.add_argument("--tf", type=int, default=5)
    args = parser.parse_args()

    input_csv = args.input_csv or f"data/history/{args.symbol}_M1.csv"
    trades_out = f"artifacts/po3_trades_{args.symbol}_{args.tf}m.csv"

    trades, skip_counts = run_backtest(args.tf, input_csv)
    write_trades_csv(trades, trades_out)
    summarize(trades, skip_counts)
    print(f"Trade log written to {trades_out}")
