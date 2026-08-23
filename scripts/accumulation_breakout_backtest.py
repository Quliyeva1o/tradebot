"""Backtest of "NY-Open Akkumulyasiya Sindirmasi + Retest" (with daily bias
filter) on USTEC (NAS100), following the user's exact 5-step procedure.

STEP 1 -- Daily bias (computed once as of the END of the previous trading
day, no lookahead). Revision history: the first version of this backtest
made (b) liquidity-confluence and (c) premium/discount HARD gates stacked
on top of (a) structure, which produced only 2 trades in 2 years (75% of
days died on the premium/discount gate alone) -- unusable. That also didn't
match how this same framework was actually applied earlier in this
conversation on a single live day, where a premium/discount "conflict" was
logged as a risk note, not an automatic reject. This version fixes that
inconsistency: (a) structure is the sole DECIDING factor for bias; (b), (c),
and the new (d) HTF order-block/FVG check are computed and recorded on every
trade for transparency, but do not gate entry.
  a) HTF (daily) structure: a BOS/CHoCH state machine over 3-bar fractal
     daily swings -- a close beyond the most recent confirmed swing
     high/low flips (or continues) the structure direction. This IS the
     bias (BULLISH/BEARISH); undetermined structure -> NEUTRAL -> no trade.
  b) Liquidity (informational): whether PDH/PDL/Asia/London high-low was
     already swept pre-09:30, recorded in the trade log's notes.
  c) Premium/Discount (informational): last-10-day range midpoint vs the
     09:30 open, recorded (not gated).
  d) HTF FVG (informational): whether an unmitigated daily FVG sits near
     the 09:30 price (simplified proxy for "order block" -- full order-block
     detection was out of scope here), recorded (not gated).

STEP 2 -- Accumulation: candle count is NOT fixed -- slide a start index
forward from 09:30 on the 1m chart; at each start, try a 2-candle window,
extend up to 8 while the group stays "tight" (group range <=
COMPRESSION_MULTIPLIER x median single-candle range, AND no single candle's
body exceeds MAX_BODY_FRACTION of the group range, so one big directional
candle can't sneak into the group). First start index that yields a valid
2+ candle window wins.

STEP 3 -- Engulf breakout: first candle (within BREAKOUT_SEARCH_CANDLES
after the accumulation) whose body fully engulfs the immediately preceding
candle's body AND closes beyond the accumulation boundary in the bias
direction. An opposite-direction engulf breakout is logged but not traded.

STEP 4 -- Retest: level A = the broken accumulation boundary; level B = the
FVG the breakout candle created (bars breakout-1/breakout/breakout+1), or
its body's 50% level if no FVG forms. DIRECT-TOUCH entry (documented
choice, matches this conversation's earlier FVG backtests) at whichever
level is touched first within RETEST_WINDOW_CANDLES after the breakout.

STEP 5 -- SL beyond the accumulation's opposite boundary (or the breakout
candle's own wick if that's farther) + buffer. TP = nearest opposite-side
liquidity (PDH/PDL or an unmitigated daily swing), same model reused from
the bias/liquidity backtest. RR >= 2 required.

One trade/day (only the first qualifying accumulation->breakout->retest
chain per day is evaluated).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scripts.bias_liquidity_backtest import (
    cash_session_high_low,
    find_swing_points,
    nearest_unmitigated_swing,
)
from smc.fvg import FVGDetector, FVGDirection
from core.models import Bar as CoreBar

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")
UTC = ZoneInfo("UTC")

INPUT_CSV = "data/history/USTEC_M1.csv"
TRADES_OUT = "artifacts/accumulation_breakout_trades.csv"

STRUCTURE_LOOKBACK_DAYS = 20
PD_RANGE_LOOKBACK_DAYS = 10
COMPRESSION_MULTIPLIER = 4.0
MAX_BODY_FRACTION = 0.5
MIN_ACCUM_CANDLES = 2
MAX_ACCUM_CANDLES = 8
ACCUM_SEARCH_CANDLES = 20   # how far into the session to look for a compression start
BREAKOUT_SEARCH_CANDLES = 10
RETEST_WINDOW_CANDLES = 5
MIN_GAP_POINTS = 3.0        # for the FVG (level B) detector
SL_BUFFER_POINTS = 10.0
MIN_RR = 2.0
SWING_LOOKBACK_DAYS = 15
RISK_PCT = 0.01
STARTING_BALANCE = 100_000.0
FIXED_RISK_USD = 200.0  # 1R = $200 (overrides RISK_PCT * STARTING_BALANCE); None = use the % calc
TEST_START_DATE = date(2025, 8, 21)  # last ~1 year (data ends 2026-08-21)
TEST_END_DATE = date(2026, 8, 21)
FIXED_TP_R = None  # None = liquidity-based target (PDH/PDL, Asia/London high-low, swing); a number = stable NR
MAX_RR_CAP = 3.0  # cap the liquidity-based TP at this many R even if the target implies more; None = no cap


@dataclass
class NyBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    date: date
    bias: str
    structure: str
    liquidity_swept: bool
    premium_discount: str
    htf_fvg_nearby: bool
    accum_candles: int
    accum_high: float
    accum_low: float
    breakout_time: datetime
    retest_level_a: float
    retest_level_b_low: float
    retest_level_b_high: float
    entry_time: datetime
    entry_price: float
    entry_level: str
    stop: float
    target: float
    target_type: str
    rr: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float


def load_bars(path: str) -> list[NyBar]:
    bars: list[NyBar] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            true_utc = broker_local.astimezone(UTC)
            ny_ts = true_utc.astimezone(NY)
            bars.append(NyBar(ts=ny_ts, open=float(row["open"]), high=float(row["high"]),
                               low=float(row["low"]), close=float(row["close"])))
    bars.sort(key=lambda b: b.ts)
    return bars


def group_by_date(bars: list[NyBar]) -> dict[date, list[NyBar]]:
    out: dict[date, list[NyBar]] = {}
    for b in bars:
        out.setdefault(b.ts.date(), []).append(b)
    return out


def build_daily_bars(by_date: dict[date, list[NyBar]]) -> dict[date, dict]:
    out = {}
    for d, bars in by_date.items():
        out[d] = {"high": max(b.high for b in bars), "low": min(b.low for b in bars),
                   "open": bars[0].open, "close": bars[-1].close}
    return out


def compute_structure_state(trading_days: list[date], daily: dict[date, dict]) -> dict[date, str | None]:
    """BOS/CHoCH state machine. Returns {date: structure_direction_as_of_that_days_close}."""
    state: str | None = None
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    state_as_of: dict[date, str | None] = {}

    for i, d in enumerate(trading_days):
        # Confirm any new fractal swing at i-1 (needs i-2, i-1, i)
        if i >= 2:
            h0, h1, h2 = daily[trading_days[i - 2]]["high"], daily[trading_days[i - 1]]["high"], daily[trading_days[i]]["high"]
            l0, l1, l2 = daily[trading_days[i - 2]]["low"], daily[trading_days[i - 1]]["low"], daily[trading_days[i]]["low"]
            if h1 > h0 and h1 > h2:
                last_swing_high = h1
            if l1 < l0 and l1 < l2:
                last_swing_low = l1

        close = daily[d]["close"]
        if last_swing_high is not None and close > last_swing_high:
            state = "BULLISH"
        if last_swing_low is not None and close < last_swing_low:
            state = "BEARISH"

        state_as_of[d] = state

    return state_as_of


def find_accumulation(day_bars: list[NyBar], median_range: float):
    """median_range here is a LOCAL baseline -- "açılış şamlarının orta ölçüsü"
    (the average size of THAT DAY's own opening candles), not a fixed global
    constant. A global 2-year median (~6.1pt) made the compression threshold
    far tighter than NAS100's typically volatile NY open ever satisfies (81%
    of days failed); a per-day adaptive baseline lets calmer/more volatile
    days each get their own realistic "normal candle size" reference.
    """
    for start in range(0, min(ACCUM_SEARCH_CANDLES, len(day_bars) - MIN_ACCUM_CANDLES)):
        window = day_bars[start:start + MIN_ACCUM_CANDLES]
        grp_high = max(b.high for b in window)
        grp_low = min(b.low for b in window)
        span = grp_high - grp_low
        max_body = max(abs(b.close - b.open) for b in window)
        if span <= COMPRESSION_MULTIPLIER * median_range and (span == 0 or max_body <= MAX_BODY_FRACTION * span):
            # try extending
            end = start + MIN_ACCUM_CANDLES
            while end < len(day_bars) and (end - start) < MAX_ACCUM_CANDLES:
                candidate = day_bars[start:end + 1]
                c_high = max(b.high for b in candidate)
                c_low = min(b.low for b in candidate)
                c_span = c_high - c_low
                c_max_body = max(abs(b.close - b.open) for b in candidate)
                if c_span <= COMPRESSION_MULTIPLIER * median_range and c_max_body <= MAX_BODY_FRACTION * c_span:
                    grp_high, grp_low = c_high, c_low
                    end += 1
                else:
                    break
            return start, end, grp_high, grp_low
    return None


def is_engulfing(prev_bar: NyBar, bar: NyBar) -> bool:
    prev_lo, prev_hi = min(prev_bar.open, prev_bar.close), max(prev_bar.open, prev_bar.close)
    lo, hi = min(bar.open, bar.close), max(bar.open, bar.close)
    return lo <= prev_lo and hi >= prev_hi and (bar.close != bar.open)


def to_core_bars(bars: list[NyBar]) -> list[CoreBar]:
    return [CoreBar(timestamp=b.ts, open=b.open, high=b.high, low=b.low, close=b.close, volume=0.0, spread=0.0) for b in bars]


def run_backtest() -> list[Trade]:
    bars = load_bars(INPUT_CSV)
    by_date = group_by_date(bars)
    # Weekday-only: excludes Sunday-evening reopen dates, which have only a
    # partial session and would otherwise pollute daily OHLC/structure/PDH-PDL
    # (trading_days[i-1] then correctly means "the previous WEEKDAY", e.g.
    # Friday before a Monday -- the ICT-conventional PDH/PDL reference).
    trading_days = sorted(d for d in by_date.keys() if d.weekday() < 5)
    daily = build_daily_bars(by_date)
    structure_as_of = compute_structure_state(trading_days, daily)

    daily_chrono = [(d, daily[d]["high"], daily[d]["low"]) for d in trading_days]
    swing_lows, swing_highs = find_swing_points(daily_chrono)
    daily_hl = {d: (daily[d]["high"], daily[d]["low"]) for d in trading_days}

    # Trailing 5-trading-day median 1m bar range, per day -- a locally
    # adaptive "normal candle size" baseline for the compression test that
    # does NOT use the very candles being tested (avoids the circularity of
    # grading a window's tightness against its own average).
    ROLLING_DAYS = 5
    recent_median_range: dict[date, float] = {}
    for i, d in enumerate(trading_days):
        window_days = trading_days[max(0, i - ROLLING_DAYS):i]
        if not window_days:
            recent_median_range[d] = None
            continue
        ranges = [b.high - b.low for dd in window_days for b in by_date[dd]]
        recent_median_range[d] = sorted(ranges)[len(ranges) // 2] if ranges else None


    trades: list[Trade] = []
    risk_amount = FIXED_RISK_USD if FIXED_RISK_USD is not None else STARTING_BALANCE * RISK_PCT
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for i, d in enumerate(trading_days):
        if d.weekday() >= 5 or i < STRUCTURE_LOOKBACK_DAYS:
            continue
        if TEST_START_DATE is not None and d < TEST_START_DATE:
            continue
        if TEST_END_DATE is not None and d > TEST_END_DATE:
            continue

        structure = structure_as_of.get(trading_days[i - 1])
        if structure is None:
            skip("neutral:no_structure")
            continue
        bias = "LONG" if structure == "BULLISH" else "SHORT"

        day_bars = by_date[d]
        bar_930_list = [b for b in day_bars if b.ts.time() >= time(9, 30)]
        bar_930 = next((b for b in day_bars if b.ts.time() == time(9, 30)), None)
        if bar_930 is None or not bar_930_list:
            skip("neutral:no_930_bar")
            continue

        # (c) Premium/Discount -- informational only, recorded per trade.
        pd_window = trading_days[max(0, i - PD_RANGE_LOOKBACK_DAYS):i]
        pd_high = max(daily[dd]["high"] for dd in pd_window)
        pd_low = min(daily[dd]["low"] for dd in pd_window)
        midpoint = (pd_high + pd_low) / 2.0
        premium_discount = "PREMIUM" if bar_930.open > midpoint else "DISCOUNT"

        prev_day = trading_days[i - 1]
        prev_cash = cash_session_high_low_bars(by_date[prev_day])
        if prev_cash is None:
            skip("no_prev_cash_session")
            continue
        pdh, pdl = prev_cash

        pre_930 = [b for b in day_bars if b.ts.time() < time(9, 30)]
        if not pre_930:
            skip("no_pre_930_data")
            continue
        pre_930_high = max(b.high for b in pre_930)
        pre_930_low = min(b.low for b in pre_930)

        # (b) Liquidity swept pre-09:30 -- informational only, recorded per trade.
        # Asia session = the calendar evening immediately before `d` (e.g. Sunday
        # 20:00-00:00 for a Monday) -- looked up by absolute timestamp, not via
        # trading_days[i-1] (which is the previous WEEKDAY, e.g. Friday, wrong here).
        asia_start = datetime.combine(d - timedelta(days=1), time(20, 0), NY)
        asia_end = datetime.combine(d, time(0, 0), NY)
        asia_bars = [b for b in bars if asia_start <= b.ts < asia_end]
        london_bars = [b for b in day_bars if time(2, 0) <= b.ts.time() < time(5, 0)]
        asia_high = max((b.high for b in asia_bars), default=None)
        asia_low = min((b.low for b in asia_bars), default=None)
        london_high = max((b.high for b in london_bars), default=None)
        london_low = min((b.low for b in london_bars), default=None)
        if bias == "SHORT":
            highs_to_check = [x for x in [pdh, asia_high, london_high] if x is not None]
            liquidity_swept = any(pre_930_high >= h for h in highs_to_check)
        else:
            lows_to_check = [x for x in [pdl, asia_low, london_low] if x is not None]
            liquidity_swept = any(pre_930_low <= l for l in lows_to_check)

        # (d) HTF FVG nearby -- simplified proxy (full order-block detection
        # was out of scope): is there an unmitigated daily FVG whose zone
        # contains or is within 1% of the 09:30 open? Informational only.
        daily_window_bars = [
            CoreBar(timestamp=datetime.combine(dd, time(0, 0), NY), open=daily[dd]["open"],
                    high=daily[dd]["high"], low=daily[dd]["low"], close=daily[dd]["close"], volume=0.0)
            for dd in trading_days[max(0, i - STRUCTURE_LOOKBACK_DAYS):i]
        ]
        daily_fvgs = FVGDetector(min_gap_pips=1.0, pip_size=1.0).detect_fvgs(daily_window_bars)
        htf_fvg_nearby = any(
            f.lower_price - bar_930.open * 0.01 <= bar_930.open <= f.upper_price + bar_930.open * 0.01
            for f in daily_fvgs
        )

        # --- STEP 2: accumulation ---
        baseline_range = recent_median_range.get(d)
        if baseline_range is None:
            skip("no_rolling_baseline")
            continue
        found = find_accumulation(bar_930_list, baseline_range)
        if found is None:
            skip("no_accumulation")
            continue
        acc_start, acc_end, acc_high, acc_low = found
        accum_candles = acc_end - acc_start

        # --- STEP 3: engulf breakout ---
        breakout_idx = None
        for j in range(acc_end, min(acc_end + BREAKOUT_SEARCH_CANDLES, len(bar_930_list) - 1)):
            bar = bar_930_list[j]
            prev_bar = bar_930_list[j - 1]
            if not is_engulfing(prev_bar, bar):
                continue
            if bias == "SHORT" and bar.close < acc_low:
                breakout_idx = j
                break
            if bias == "LONG" and bar.close > acc_high:
                breakout_idx = j
                break
            # engulf but wrong direction relative to bias -> logged, not traded, keep scanning
        if breakout_idx is None:
            skip("no_valid_breakout")
            continue

        breakout_bar = bar_930_list[breakout_idx]

        # --- STEP 4: retest levels A and B ---
        level_a = acc_low if bias == "SHORT" else acc_high

        b_low = b_high = None
        if 1 <= breakout_idx < len(bar_930_list) - 1:
            triple = to_core_bars([bar_930_list[breakout_idx - 1], breakout_bar, bar_930_list[breakout_idx + 1]])
            fvgs = FVGDetector(min_gap_pips=MIN_GAP_POINTS, pip_size=1.0).detect_fvgs(triple)
            wanted = FVGDirection.BEARISH if bias == "SHORT" else FVGDirection.BULLISH
            match = next((f for f in fvgs if f.direction == wanted), None)
            if match:
                b_low, b_high = match.lower_price, match.upper_price
        if b_low is None:
            body_mid = (breakout_bar.open + breakout_bar.close) / 2.0
            b_low = b_high = body_mid

        retest_candidates = [level_a, (b_low + b_high) / 2.0]

        entry_bar = None
        entry_price = None
        entry_level = None
        window = bar_930_list[breakout_idx + 1: breakout_idx + 1 + RETEST_WINDOW_CANDLES]
        for b in window:
            if bias == "SHORT":
                touched_a = b.high >= level_a
                touched_b = b.high >= b_low
            else:
                touched_a = b.low <= level_a
                touched_b = b.low <= b_high
            if touched_a and touched_b:
                # whichever is nearer to the breakout close is "touched first" in practice;
                # for a SHORT (price below, retracing up) the LOWER level is nearer -> touches first
                if bias == "SHORT":
                    entry_price, entry_level = (level_a, "A") if level_a <= b_low else (b_low, "B")
                else:
                    entry_price, entry_level = (level_a, "A") if level_a >= b_high else (b_high, "B")
                entry_bar = b
                break
            if touched_a:
                entry_price, entry_level, entry_bar = level_a, "A", b
                break
            if touched_b:
                entry_price, entry_level, entry_bar = (b_low if bias == "SHORT" else b_high), "B", b
                break
        if entry_bar is None:
            skip("retest_timeout")
            continue

        # --- STEP 5: SL / TP ---
        wick_extreme = breakout_bar.high if bias == "SHORT" else breakout_bar.low
        if bias == "SHORT":
            opp_boundary = max(acc_high, wick_extreme)
            stop = opp_boundary + SL_BUFFER_POINTS
        else:
            opp_boundary = min(acc_low, wick_extreme)
            stop = opp_boundary - SL_BUFFER_POINTS
        risk_dist = abs(entry_price - stop)
        if risk_dist <= 0:
            skip("non_positive_risk")
            continue

        if FIXED_TP_R is not None:
            target_price = entry_price - FIXED_TP_R * risk_dist if bias == "SHORT" else entry_price + FIXED_TP_R * risk_dist
            target_type = f"FIXED_{FIXED_TP_R:g}R"
            rr = FIXED_TP_R
        else:
            candidates: list[tuple[float, str]] = []
            if bias == "SHORT":
                if pdl < entry_price:
                    candidates.append((pdl, "PDL"))
                if asia_low is not None and asia_low < entry_price:
                    candidates.append((asia_low, "Asia_low"))
                if london_low is not None and london_low < entry_price:
                    candidates.append((london_low, "London_low"))
                sw = nearest_unmitigated_swing(d, trading_days, swing_lows, swing_highs, daily_hl, "short", entry_price, SWING_LOOKBACK_DAYS)
                if sw is not None:
                    candidates.append((sw, "swing_low"))
            else:
                if pdh > entry_price:
                    candidates.append((pdh, "PDH"))
                if asia_high is not None and asia_high > entry_price:
                    candidates.append((asia_high, "Asia_high"))
                if london_high is not None and london_high > entry_price:
                    candidates.append((london_high, "London_high"))
                sw = nearest_unmitigated_swing(d, trading_days, swing_lows, swing_highs, daily_hl, "long", entry_price, SWING_LOOKBACK_DAYS)
                if sw is not None:
                    candidates.append((sw, "swing_high"))
            if not candidates:
                skip("no_liquidity_target")
                continue
            if bias == "SHORT":
                target_price, target_type = max(candidates, key=lambda c: c[0])
            else:
                target_price, target_type = min(candidates, key=lambda c: c[0])

            reward_dist = abs(target_price - entry_price)
            rr = reward_dist / risk_dist
            if rr < MIN_RR:
                skip("rr_below_min")
                continue

            if MAX_RR_CAP is not None and rr > MAX_RR_CAP:
                target_price = entry_price - MAX_RR_CAP * risk_dist if bias == "SHORT" else entry_price + MAX_RR_CAP * risk_dist
                target_type = f"{target_type}_capped_{MAX_RR_CAP:g}R"
                rr = MAX_RR_CAP

        # --- simulate exit ---
        exit_price = exit_time = exit_reason = None
        if bias == "SHORT" and entry_bar.high >= stop:
            exit_price, exit_reason, exit_time = stop, "SL", entry_bar.ts
        elif bias == "LONG" and entry_bar.low <= stop:
            exit_price, exit_reason, exit_time = stop, "SL", entry_bar.ts

        if exit_price is None:
            future_bars = [b for b in day_bars if b.ts > entry_bar.ts]
            for fb in future_bars:
                if bias == "SHORT":
                    hit_sl, hit_tp = fb.high >= stop, fb.low <= target_price
                else:
                    hit_sl, hit_tp = fb.low <= stop, fb.high >= target_price
                if hit_sl:
                    exit_price, exit_reason = stop, "SL"
                elif hit_tp:
                    exit_price, exit_reason = target_price, "TP"
                if exit_price is not None:
                    exit_time = fb.ts
                    break

        if exit_price is None:
            last_bar = day_bars[-1]
            exit_price, exit_reason, exit_time = last_bar.close, "EOD", last_bar.ts

        realized_move = (entry_price - exit_price) if bias == "SHORT" else (exit_price - entry_price)
        r_multiple = realized_move / risk_dist
        pnl_usd = r_multiple * risk_amount

        trades.append(Trade(
            date=d, bias=bias, structure=structure,
            liquidity_swept=liquidity_swept, premium_discount=premium_discount, htf_fvg_nearby=htf_fvg_nearby,
            accum_candles=accum_candles,
            accum_high=round(acc_high, 2), accum_low=round(acc_low, 2), breakout_time=breakout_bar.ts,
            retest_level_a=round(level_a, 2), retest_level_b_low=round(b_low, 2), retest_level_b_high=round(b_high, 2),
            entry_time=entry_bar.ts, entry_price=round(entry_price, 2), entry_level=entry_level,
            stop=round(stop, 2), target=round(target_price, 2), target_type=target_type, rr=round(rr, 2),
            exit_time=exit_time, exit_price=round(exit_price, 2), exit_reason=exit_reason,
            r_multiple=round(r_multiple, 3), pnl_usd=round(pnl_usd, 2),
        ))

    print("Skip funnel:", skip_counts, f"(weekdays considered: {sum(1 for d in trading_days if d.weekday() < 5) - STRUCTURE_LOOKBACK_DAYS})")
    return trades


def cash_session_high_low_bars(day_bars: list[NyBar]) -> tuple[float, float] | None:
    session = [b for b in day_bars if time(9, 30) <= b.ts.time() < time(16, 0)]
    if not session:
        return None
    return max(b.high for b in session), min(b.low for b in session)


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "bias", "htf_structure", "liquidity_swept", "premium_discount", "htf_fvg_nearby",
            "accum_candles", "accum_high", "accum_low",
            "breakout_time_ny", "retest_A", "retest_B_low", "retest_B_high",
            "entry_time_ny", "entry_price", "entry_level", "stop", "target", "target_type", "rr",
            "exit_time_ny", "exit_price", "exit_reason", "r_multiple", "pnl_usd",
        ])
        for t in trades:
            w.writerow([
                t.date, t.bias, t.structure, t.liquidity_swept, t.premium_discount, t.htf_fvg_nearby,
                t.accum_candles, t.accum_high, t.accum_low,
                t.breakout_time.strftime("%Y-%m-%d %H:%M"), t.retest_level_a, t.retest_level_b_low, t.retest_level_b_high,
                t.entry_time.strftime("%Y-%m-%d %H:%M"), t.entry_price, t.entry_level, t.stop, t.target, t.target_type, t.rr,
                t.exit_time.strftime("%Y-%m-%d %H:%M"), t.exit_price, t.exit_reason, t.r_multiple, t.pnl_usd,
            ])


def summarize(trades: list[Trade]) -> None:
    n = len(trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = abs(sum(t.pnl_usd for t in losses))
    wr = len(wins) / n * 100 if n else 0.0
    pf = gp / gl if gl > 0 else float("inf")
    print(f"Total trades: {n}")
    print(f"Win rate: {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Net P&L: ${sum(t.pnl_usd for t in trades):,.2f}")
    by_bias: dict[str, int] = {}
    for t in trades:
        by_bias[t.bias] = by_bias.get(t.bias, 0) + 1
    print("By bias:", by_bias)
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    print("Exit reasons:", by_reason)


if __name__ == "__main__":
    trades = run_backtest()
    write_trades_csv(trades, TRADES_OUT)
    summarize(trades)
    print(f"\nTrade log written to {TRADES_OUT}")
