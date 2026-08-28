"""ICT Power of Three (PO3) strategy, backtested with spread, across every
symbol/timeframe combination -- extends the existing 12-combo sweep
(scripts/po3_sweep.py: XAUUSD/EURUSD/NAS100) to 5 symbols (adds GBPUSD/
USDJPY, matching the SR and Order Flow sweeps) and adds spread cost for the
first time.

CORRECTNESS FIX included (not just spread): scripts/po3_backtest.py enters
mid-bar at a retracement into an FVG zone (entry = the zone's near edge, hit
via `l[i] <= upper`/`h[i] >= lower` on bar i), then relies on the position
loop's SL/TP check -- but that check runs at the TOP of the loop, for the
PREVIOUS bar's position, so a brand-new position opened partway through bar
i's own range was never checked against that SAME bar's remaining high/low.
This is exactly the class of bug SESSION_HANDOFF.md #2.2 found and fixed in
the First FVG backtest, and #3.3 already flags it as PRESENT but LATENT in
PO3 ("Checked: 0 occurrences in the current trade set, latent only"). That
check was only run against the old 3-symbol/~6mo dataset with 92 total
trades; re-tested here against 5 symbols x 6 years, so it needed re-checking
before trusting the results, not just re-checking the doc's old claim.
Fixed here by checking the entry bar's own high/low against SL/TP
immediately after opening a position (SL checked first, same conservative
convention as every other backtest in this repo), before falling through to
future bars.

Spread model: identical convention to every other spread-aware sweep in
this repo -- cost_r = spread_price / risk_distance, subtracted once per
trade (entry side). NAS100 uses the fixed 3.0-point round-trip constant;
XAUUSD/EURUSD/GBPUSD/USDJPY use their real historical per-bar spread.

Usage:
    python -m scripts.po3_spread_sweep
    python -m scripts.po3_spread_sweep --symbols NAS100,XAUUSD --timeframes 15,30
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.backtest_common import NY, compute_atr, compute_pivots, htf_bias_to_index, resample

BROKER_TZ = ZoneInfo("Europe/Bucharest")

SYMBOLS = ["NAS100", "XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
TIMEFRAMES = [5, 15, 30, 60]
FIXED_SPREAD_OVERRIDE = {"NAS100": 3.0}
RISK_PCT = 0.01
STARTING_BALANCE = 100_000.0

ATR_LEN = 14
SWING_LEN = 3
RANGE_START = "09:30"
RANGE_END = "10:00"
MIN_GAP_POINTS_ATR = 0.05
SWEEP_LOOKBACK_BARS = 20
DISPLACEMENT_ATR_MULT = 2.0
DISPLACEMENT_LOOKBACK_BARS = 5
MSS_LOOKBACK_BARS = 10
FVG_RETEST_MAX_BARS = 20
SL_BUFFER_ATR = 0.15
MIN_RR = 2.0


@dataclass
class NetTrade:
    entry_time: pd.Timestamp
    direction: str
    exit_reason: str
    spread_price: float
    r_multiple_gross: float
    r_multiple: float
    pnl_usd: float


@lru_cache(maxsize=8)
def load_m1_with_spread(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append(
                (ny_ts, float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row.get("volume") or 0.0), float(row.get("spread") or 0.0))
            )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "spread"])
    return df.set_index("ts").sort_index()


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


DAILY_BIAS_VOTE_THRESHOLD = 2  # both of the 2 votes (1H structure, PDH/PDL zone) must agree


def compute_daily_bias(m1: pd.DataFrame, levels: dict) -> pd.Series:
    h1 = resample(m1, 60)
    h1.index = h1.index.tz_convert(NY)
    vote_structure = structure_bias_votes(h1, swing_len=3)
    day_key = h1.index.date
    pdmid = pd.Series([levels["pdmid"].get(d, np.nan) for d in day_key], index=h1.index)
    vote_zone = np.sign(pdmid - h1["close"]).fillna(0)
    total = vote_structure.to_numpy() + vote_zone.to_numpy()
    t = DAILY_BIAS_VOTE_THRESHOLD
    bias_1h = pd.Series(np.where(total >= t, 1, np.where(total <= -t, -1, 0)), index=h1.index)
    return htf_bias_to_index(bias_1h, 60, m1.index)


def run_backtest(symbol: str, tf_minutes: int, m1: pd.DataFrame) -> list[NetTrade]:
    bars = resample(m1, tf_minutes)
    bars.index = bars.index.tz_convert(NY)
    # backtest_common.resample() only aggregates OHLCV, dropping spread --
    # resample it separately (mean, same rule) and reattach rather than
    # touching that shared function (used by several other strategies'
    # backtests). m1 is already NY-tz (see load_m1_with_spread), matching
    # bars.index after the tz_convert above (a no-op in that case), so a
    # plain reindex lines the two series up.
    bars["spread"] = m1["spread"].resample(f"{tf_minutes}min", label="left", closed="left").mean().reindex(bars.index)

    o, h, l, c = (bars[col].to_numpy() for col in ("open", "high", "low", "close"))
    spread_col = bars["spread"].to_numpy() if "spread" in bars.columns else np.zeros(len(bars))
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

    fixed_spread = FIXED_SPREAD_OVERRIDE.get(symbol)
    trades: list[NetTrade] = []
    in_position = False
    pos_dir = pos_sl = pos_tp = pos_spread = None
    pos_entry_time = pos_entry_price = None

    warmup = max(ATR_LEN, SWING_LEN * 2, 20) + 5
    sweep_since: dict[str, int] = {}
    displacement_since: dict[str, int] = {}
    mss_since: dict[str, int] = {}
    fvg_bull: list[tuple[int, float, float]] = []
    fvg_bear: list[tuple[int, float, float]] = []
    recent_swing_high = recent_swing_low = None

    def close_trade(exit_i: int, exit_price: float, exit_reason: str) -> None:
        risk_dist = abs(pos_entry_price - pos_sl)
        move = (exit_price - pos_entry_price) if pos_dir == "LONG" else (pos_entry_price - exit_price)
        r_gross = move / risk_dist
        cost_r = (pos_spread / risk_dist) if risk_dist > 0 else 0.0
        r_net = r_gross - cost_r
        trades.append(NetTrade(
            pos_entry_time, pos_dir, exit_reason, round(pos_spread, 6),
            round(r_gross, 4), round(r_net, 4), round(r_net * STARTING_BALANCE * RISK_PCT, 2),
        ))

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
                close_trade(i, exit_price, exit_reason)
                in_position = False

        if after_range[i]:
            sell_side = [v for v in (range_low[i], asia_low[i], london_low[i], pdl[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))]
            buy_side = [v for v in (range_high[i], asia_high[i], london_high[i], pdh[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))]
            if sell_side and l[i] <= min(sell_side) and c[i] > min(sell_side):
                sweep_since["sell"] = i
            if buy_side and h[i] >= max(buy_side) and c[i] < max(buy_side):
                sweep_since["buy"] = i

        range_ = h[i] - l[i]
        bullish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] > o[i]
        bearish_displacement = range_ > 0 and not np.isnan(atr[i]) and range_ >= DISPLACEMENT_ATR_MULT * atr[i] and abs(c[i] - o[i]) >= 0.5 * range_ and c[i] < o[i]
        if bullish_displacement:
            displacement_since["bull"] = i
        if bearish_displacement:
            displacement_since["bear"] = i

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

        if i >= 2:
            b0h, b0l = h[i - 2], l[i - 2]
            b2h, b2l = h[i], l[i]
            min_gap = MIN_GAP_POINTS_ATR * atr[i] if not np.isnan(atr[i]) else 0.0
            if b2l - b0h >= min_gap:
                fvg_bull.append((i, b0h, b2l))
            if b0l - b2h >= min_gap:
                fvg_bear.append((i, b2h, b0l))
        fvg_bull = [f for f in fvg_bull if i - f[0] <= FVG_RETEST_MAX_BARS and l[i] > f[1] * 0.999]
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

        spread_price = fixed_spread if fixed_spread is not None else (
            0.0 if np.isnan(spread_col[i]) else float(spread_col[i])
        )

        if long_ok:
            sweep_low = min([v for v in (range_low[i], asia_low[i], london_low[i], pdl[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))], default=l[i])
            sl_price = min(sweep_low, l[i]) - SL_BUFFER_ATR * atr[i]
            entry = long_fvg_retest[1]
            risk_dist = entry - sl_price
            targets = [lvl for lvl in active_highs if lvl > entry]
            tp = min(targets) if targets else None
            if risk_dist <= 0 or tp is None:
                continue
            rr = (tp - entry) / risk_dist
            if rr < MIN_RR:
                continue
            pos_dir, pos_sl, pos_tp = "LONG", sl_price, tp
            pos_entry_time, pos_entry_price, pos_spread = bars.index[i], entry, spread_price
            # CORRECTNESS FIX (see module docstring): same-bar stop-out check
            # on the entry bar itself -- SL checked first.
            if l[i] <= pos_sl:
                close_trade(i, pos_sl, "SL")
            elif h[i] >= pos_tp:
                close_trade(i, pos_tp, "TP")
            else:
                in_position = True
        elif short_ok:
            sweep_high = max([v for v in (range_high[i], asia_high[i], london_high[i], pdh[i]) if v is not None and not (isinstance(v, float) and np.isnan(v))], default=h[i])
            sl_price = max(sweep_high, h[i]) + SL_BUFFER_ATR * atr[i]
            entry = short_fvg_retest[0]
            risk_dist = sl_price - entry
            targets = [lvl for lvl in active_lows if lvl < entry]
            tp = max(targets) if targets else None
            if risk_dist <= 0 or tp is None:
                continue
            rr = (entry - tp) / risk_dist
            if rr < MIN_RR:
                continue
            pos_dir, pos_sl, pos_tp = "SHORT", sl_price, tp
            pos_entry_time, pos_entry_price, pos_spread = bars.index[i], entry, spread_price
            if h[i] >= pos_sl:
                close_trade(i, pos_sl, "SL")
            elif l[i] <= pos_tp:
                close_trade(i, pos_tp, "TP")
            else:
                in_position = True

    return trades


def summarize(trades: list[NetTrade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "net_pnl": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    return {
        "trades": n, "win_rate": round(wins / n * 100, 1),
        "profit_factor": (round(pf, 3) if gl > 0 else None),
        "net_pnl": round(sum(t.pnl_usd for t in trades), 2),
    }


def write_net_trades_csv(trades: list[NetTrade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "exit_reason", "spread_price", "r_multiple_gross", "r_multiple_net", "pnl_usd_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.exit_reason, t.spread_price, t.r_multiple_gross, t.r_multiple, t.pnl_usd])


def period_breakdown(trades: list[NetTrade], end_date) -> dict:
    end = pd.Timestamp(end_date)
    windows = {
        "5y": pd.DateOffset(years=5), "1y": pd.DateOffset(years=1),
        "3mo": pd.DateOffset(months=3), "1mo": pd.DateOffset(months=1),
    }
    window_starts = {label: (end - off).date().isoformat() for label, off in windows.items()}

    def day_of(t: NetTrade) -> str:
        return t.entry_time.date().isoformat()

    window_summaries = {
        label: summarize([t for t in trades if window_starts[label] <= day_of(t) <= end.date().isoformat()])
        for label in windows
    }

    y1_trades = [t for t in trades if window_starts["1y"] <= day_of(t) <= end.date().isoformat()]
    monthly: dict[str, list[NetTrade]] = {}
    for t in y1_trades:
        monthly.setdefault(day_of(t)[:7], []).append(t)
    monthly_summary = {ym: summarize(ts) for ym, ts in sorted(monthly.items())}

    m1_trades = [t for t in trades if window_starts["1mo"] <= day_of(t) <= end.date().isoformat()]
    daily_by_day: dict[str, list[NetTrade]] = {}
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
        "windows": window_summaries, "monthly_last_1y": monthly_summary,
        "daily_last_1mo": daily_summary, "half_year_last_5y": half_year_summary,
        "window_starts": window_starts, "end_date": end.date().isoformat(),
    }


def main() -> None:
    global MSS_LOOKBACK_BARS, MIN_RR, DAILY_BIAS_VOTE_THRESHOLD
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(str(t) for t in TIMEFRAMES))
    parser.add_argument("--output-json", default="artifacts/po3_spread_sweep.json")
    parser.add_argument("--mss-lookback-bars", type=int, default=None,
                         help="Overrides MSS_LOOKBACK_BARS (default 10) -- how many bars after a "
                         "displacement a Market Structure Shift can still count. Widening this is "
                         "one of the 4 hard-gate relaxations SESSION_HANDOFF.md suggested to see "
                         "whether PO3 can produce a statistically meaningful trade count.")
    parser.add_argument("--min-rr", type=float, default=None,
                         help="Overrides MIN_RR (default 2.0), the minimum reward:risk gate.")
    parser.add_argument("--bias-threshold", type=int, default=None,
                         help="Overrides DAILY_BIAS_VOTE_THRESHOLD (default 2 -- BOTH of the 2 "
                         "daily-bias votes, 1H structure and PDH/PDL zone, must agree). Set 1 to "
                         "trade on either vote alone. Diagnostics on NAS100 60m show 'neutral_bias' "
                         "rejects ~83%% of all bars -- an order of magnitude more than the 4 "
                         "documented hard gates combined -- so THIS, not MSS/RR, is the actual "
                         "bottleneck on trade frequency.")
    args = parser.parse_args()

    if args.mss_lookback_bars is not None:
        MSS_LOOKBACK_BARS = args.mss_lookback_bars
        print(f"NOTE: MSS_LOOKBACK_BARS overridden to {MSS_LOOKBACK_BARS} (default is 10).")
    if args.min_rr is not None:
        MIN_RR = args.min_rr
        print(f"NOTE: MIN_RR overridden to {MIN_RR} (default is 2.0).")
    if args.bias_threshold is not None:
        DAILY_BIAS_VOTE_THRESHOLD = args.bias_threshold
        print(f"NOTE: DAILY_BIAS_VOTE_THRESHOLD overridden to {DAILY_BIAS_VOTE_THRESHOLD} (default is 2).")

    symbols = args.symbols.split(",")
    timeframes = [int(t) for t in args.timeframes.split(",")]

    all_results: dict[str, dict] = {}

    for symbol in symbols:
        m1 = load_m1_with_spread(f"data/history/{symbol}_M1.csv")
        for tf in timeframes:
            key = f"{symbol}_{tf}m"
            trades = run_backtest(symbol, tf, m1)
            write_net_trades_csv(trades, f"artifacts/po3_sweep_{key}_trades.csv")

            if not trades:
                print(f"{key}: 0 trades")
                all_results[key] = {"symbol": symbol, "timeframe": tf, "trades": 0}
                continue

            end_date = max(t.entry_time for t in trades).date()
            breakdown = period_breakdown(trades, end_date)
            print(f"{key}: n_total={len(trades)}  5y={breakdown['windows']['5y']}  1y={breakdown['windows']['1y']}")
            all_results[key] = {"symbol": symbol, "timeframe": tf, "total_trades": len(trades), **breakdown}

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull sweep JSON written to {args.output_json}")


if __name__ == "__main__":
    main()
