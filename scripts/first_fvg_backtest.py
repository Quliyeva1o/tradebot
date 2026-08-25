"""Backtest of the "Ilk FVG Strategiyasi (New York acilisi)" on NAS100 M1 data.

Rules (as specified by the user):
0. Daily bias filter (added on request): same PD-midline bias as the
   bias/liquidity backtest -- prev day's cash-session [09:30-16:00 NY]
   (high+low)/2. An opposite-direction FVG that formed earlier in a window is
   skipped entirely; the "first FVG" is the first one matching the bias.
1. Detection: 1-minute chart (back from the M5 experiment, on request), TWO
   independent session windows scanned per day, each an anchor point of its
   own (added on request -- see "Two session windows" below):
     - "ny_open": 09:30-10:00 NY (candles at :30..:55)
     - "midnight": 00:00-00:30 NY
   Find the first Fair Value Gap (3-candle imbalance) whose middle
   (displacement) candle is a genuine energetic expansion candle.
2. Extend the FVG zone forward for the rest of the trading day.
3. Entry: price returns and touches the FVG zone later in the session
   ("direct touch" variant -- see caveat below).
4. SL: just beyond the FAR edge of the FVG box.
5. TP: nearest opposite-side liquidity pool (PDH/PDL or an unmitigated daily
   swing high/low) -- same liquidity model used in the bias/liquidity
   backtest, reused here via import.
6. One trade per SESSION WINDOW (not one per day) -- see below.

Two session windows, and how bias is computed for each (design decision,
flagged since the user only said "also consider 00:00"):
Both windows share the SAME reference midline: the previous TRADING day's
cash-session [09:30-16:00 NY] (high+low)/2 -- there is no lookahead problem
either way, since that previous day is fully closed before either window.
What differs is which candle's open is compared against that midline:
"ny_open" uses the 09:30 open (as before); "midnight" uses the 00:00 open of
the SAME calendar date. Using the (not-yet-existing) 09:30 open to bias a
00:00 event would be lookahead; anchoring each window to its own open avoids
that. This means the two windows can disagree on bias on a given day (e.g.
midnight session-> SHORT, NY-open session -> LONG), and BOTH are traded
independently -- there is no rule blocking one because the other already
fired, and no shared "one trade per day" cap between them (only within each
window). Each trade is tagged with which window produced it.

Uses this repo's own smc/fvg.py (FVGDetector) and smc/displacement.py
(DisplacementDetector) rather than reimplementing gap/displacement logic.

Assumptions made explicit (no numeric value was given by the user for
these -- flagged in the report so they can be tuned):
- Entry variant: DIRECT TOUCH at the FVG's near edge (the "aşağı timeframe
  təsdiqi" / lower-timeframe-confirmation variant is not implemented -- v1).
- min FVG gap: 3 points (median M1 bar range is ~10.5pts; smaller gaps are
  noise). SL buffer beyond the far edge: 5 points.
- Displacement: DisplacementDetector default (candle True Range >= 2x ATR-14,
  body >= 50% of range).
- No exit rule was specified beyond "SL / TP later in the session" -- added
  an end-of-day (last bar of that NY calendar date) flat-close as a backstop,
  reason "EOD", so trades don't run on indefinitely.
- No RR-minimum filter or profit cap applied (none was specified for this
  strategy, unlike the earlier bias/liquidity one).
- Data: M1 history is capped by MT5's terminal-side "max bars" limit to
  2026-05-12 onward (~3.5 months), not the full 6 months M5 covers.

"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from scripts.bias_liquidity_backtest import (
    build_daily_ohlc,
    cash_session_high_low,
    find_swing_points,
    nearest_unmitigated_swing,
)
from smc.displacement import DisplacementDetector
from smc.fvg import FVGDetector, FVGDirection
from core.models import Bar as CoreBar

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

INPUT_CSV = "data/history/NAS100_M1.csv"
TRADES_OUT = "artifacts/first_fvg_trades.csv"

# (label, bias_reference_time, window_start, window_end) -- all times NY (America/New_York)
SESSIONS = [
    ("midnight", time(0, 0), time(0, 0), time(0, 30)),
]
CASH_SESSION_START = time(9, 30)
CASH_SESSION_END = time(16, 0)
MIN_GAP_POINTS = 3.0
ENTRY_MODE = "touch"  # "touch" = fill at the FVG's near edge on first touch; "confirmation" = wait for a candle to tag the zone AND close back outside it in the trade direction, fill at that close
PRICE_DECIMALS = 2
USE_BIAS_FILTER = False  # set False to trade the first FVG regardless of daily PD-midline bias
REQUIRE_DISPLACEMENT = False  # set True to require the FVG's middle candle to be a DisplacementDetector hit (ATR>=2x)
FIXED_TP_R = 2.5  # stable 2.5R take-profit instead of the liquidity-hunt target; None = liquidity-based
ATR_MULTIPLIER = 2.0
ATR_PERIOD = 14
SWING_LOOKBACK_DAYS = 15
RISK_PCT = 0.01
STARTING_BALANCE = 100_000.0


@dataclass
class NyBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    date: object
    session: str
    direction: str
    fvg_upper: float
    fvg_lower: float
    fvg_formed_time: datetime
    entry_time: datetime
    entry_price: float
    stop: float
    target: float
    target_type: str
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
            true_utc = broker_local.astimezone(ZoneInfo("UTC"))
            ny_ts = true_utc.astimezone(NY)
            bars.append(
                NyBar(
                    ts=ny_ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    bars.sort(key=lambda b: b.ts)
    return bars


def group_by_date(bars: list[NyBar]) -> dict:
    out: dict = {}
    for b in bars:
        out.setdefault(b.ts.date(), []).append(b)
    return out


def to_core_bars(bars: list[NyBar]) -> list[CoreBar]:
    return [CoreBar(timestamp=b.ts, open=b.open, high=b.high, low=b.low, close=b.close, volume=0.0, spread=0.0) for b in bars]


def find_first_fvg(
    day_bars: list[NyBar],
    window_start: time,
    window_end: time,
    required_direction: str | None = None,
    context_bars_before: list[NyBar] | None = None,
):
    """Returns (fvg, context_bars) for the first displaced FVG whose middle
    candle falls in [window_start, window_end).

    Args:
        context_bars_before: Extra bars (e.g. the tail of the PREVIOUS
            calendar date) to prepend so ATR-14 has enough warmup when the
            window starts at 00:00 and there's nothing earlier on this date.
        required_direction: If given ("LONG" or "SHORT"), only FVGs matching
            the daily bias direction are considered -- an opposite-direction
            FVG that formed earlier is skipped entirely, not just its trade.
    """
    same_day_context = [b for b in day_bars if b.ts.time() < window_end]
    context = (context_bars_before or []) + same_day_context
    if len(context) < ATR_PERIOD + 3:
        return None
    core_bars = to_core_bars(context)

    disp = DisplacementDetector(atr_multiplier=ATR_MULTIPLIER, atr_period=ATR_PERIOD)
    displaced = {d.timestamp for d in disp.find_displacements(core_bars)}

    fvg_detector = FVGDetector(min_gap_pips=MIN_GAP_POINTS, pip_size=1.0)
    fvgs = fvg_detector.detect_fvgs(core_bars)

    wanted_fvg_direction = None
    if required_direction == "LONG":
        wanted_fvg_direction = FVGDirection.BULLISH
    elif required_direction == "SHORT":
        wanted_fvg_direction = FVGDirection.BEARISH

    candidates = [
        fvg
        for fvg in fvgs
        if window_start <= fvg.timestamp.time() < window_end
        and (not REQUIRE_DISPLACEMENT or fvg.timestamp in displaced)
        and (wanted_fvg_direction is None or fvg.direction == wanted_fvg_direction)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda f: f.timestamp)
    return candidates[0], context


def process_session(
    session_label: str,
    bias_ref_time: time,
    window_start: time,
    window_end: time,
    d,
    i: int,
    trading_days: list,
    by_date: dict,
    daily_hl: dict,
    swing_lows: dict,
    swing_highs: dict,
    risk_amount: float,
    skip,
) -> Trade | None:
    day_bars = by_date[d]

    prev_cash = None
    for j in range(i - 1, -1, -1):
        c = cash_session_high_low(by_date[trading_days[j]])
        if c is not None:
            prev_cash = c
            break
    if prev_cash is None:
        skip(f"{session_label}:no_prev_cash_session")
        return None
    prev_high, prev_low = prev_cash
    midline = (prev_high + prev_low) / 2.0

    bias = None
    if USE_BIAS_FILTER:
        bias_bar = next((b for b in day_bars if b.ts.time() == bias_ref_time), None)
        if bias_bar is None:
            skip(f"{session_label}:no_bias_ref_bar")
            return None
        if bias_bar.open < midline:
            bias = "SHORT"
        elif bias_bar.open > midline:
            bias = "LONG"
        else:
            skip(f"{session_label}:midline_tie")
            return None

    context_before = None
    if window_start == time(0, 0) and i > 0:
        prev_day_bars = by_date[trading_days[i - 1]]
        context_before = [b for b in prev_day_bars if b.ts.time() >= time(22, 0)]

    found = find_first_fvg(day_bars, window_start, window_end, required_direction=bias, context_bars_before=context_before)
    if found is None:
        skip(f"{session_label}:no_fvg{'_matching_bias' if USE_BIAS_FILTER else ''}")
        return None
    fvg, context = found

    direction = "LONG" if fvg.direction == FVGDirection.BULLISH else "SHORT"
    upper, lower = fvg.upper_price, fvg.lower_price
    fvg_end_ts = context[fvg.end_index].ts
    displacement_bar = context[fvg.start_index + 1]  # the middle candle that created the FVG

    rest_of_day = [b for b in day_bars if b.ts > fvg_end_ts]
    entry_bar = None
    entry_price = None
    if ENTRY_MODE == "touch":
        for b in rest_of_day:
            if direction == "LONG" and b.low <= upper:
                entry_bar, entry_price = b, upper
                break
            if direction == "SHORT" and b.high >= lower:
                entry_bar, entry_price = b, lower
                break
    else:  # "confirmation": must tag the zone AND close back outside it in the trade direction
        for b in rest_of_day:
            if direction == "LONG" and b.low <= upper and b.close > upper:
                entry_bar, entry_price = b, b.close
                break
            if direction == "SHORT" and b.high >= lower and b.close < lower:
                entry_bar, entry_price = b, b.close
                break
    if entry_bar is None:
        skip(f"{session_label}:fvg_never_retested")
        return None

    # SL = the low (bullish) / high (bearish) of the CANDLE THAT CREATED the FVG
    # (the middle/displacement candle), not the FVG zone's own boundary --
    # these can differ since the displacement candle's wick may extend
    # beyond the gap's own edge.
    if direction == "LONG":
        stop = displacement_bar.low
    else:
        stop = displacement_bar.high
    risk_dist = abs(entry_price - stop)
    if risk_dist <= 0:
        skip(f"{session_label}:non_positive_risk")
        return None

    if FIXED_TP_R is not None:
        target_price = entry_price - FIXED_TP_R * risk_dist if direction == "SHORT" else entry_price + FIXED_TP_R * risk_dist
        target_type = f"FIXED_{FIXED_TP_R:g}R"
    else:
        candidates: list[tuple[float, str]] = []
        if direction == "SHORT":
            if prev_cash and prev_cash[1] < entry_price:
                candidates.append((prev_cash[1], "PDL"))
            sw = nearest_unmitigated_swing(d, trading_days, swing_lows, swing_highs, daily_hl, "short", entry_price, SWING_LOOKBACK_DAYS)
            if sw is not None:
                candidates.append((sw, "swing_low"))
        else:
            if prev_cash and prev_cash[0] > entry_price:
                candidates.append((prev_cash[0], "PDH"))
            sw = nearest_unmitigated_swing(d, trading_days, swing_lows, swing_highs, daily_hl, "long", entry_price, SWING_LOOKBACK_DAYS)
            if sw is not None:
                candidates.append((sw, "swing_high"))

        if not candidates:
            skip(f"{session_label}:no_liquidity_candidate")
            return None

        if direction == "SHORT":
            target_price, target_type = max(candidates, key=lambda c: c[0])
        else:
            target_price, target_type = min(candidates, key=lambda c: c[0])

    # Same-bar instant-stop check: the entry bar itself may have already
    # blown through the far edge before/as it touched the near edge.
    exit_price = exit_time = exit_reason = None
    if direction == "LONG" and entry_bar.low <= stop:
        exit_price, exit_reason, exit_time = stop, "SL", entry_bar.ts
    elif direction == "SHORT" and entry_bar.high >= stop:
        exit_price, exit_reason, exit_time = stop, "SL", entry_bar.ts

    if exit_price is None:
        future_bars = [b for b in day_bars if b.ts > entry_bar.ts]
        for fb in future_bars:
            if direction == "LONG":
                hit_sl = fb.low <= stop
                hit_tp = fb.high >= target_price
            else:
                hit_sl = fb.high >= stop
                hit_tp = fb.low <= target_price
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

    realized_move = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    r_multiple = realized_move / risk_dist
    pnl_usd = r_multiple * risk_amount

    return Trade(
        date=d, session=session_label, direction=direction, fvg_upper=round(upper, PRICE_DECIMALS), fvg_lower=round(lower, PRICE_DECIMALS),
        fvg_formed_time=fvg.timestamp, entry_time=entry_bar.ts, entry_price=round(entry_price, PRICE_DECIMALS),
        stop=round(stop, PRICE_DECIMALS), target=round(target_price, PRICE_DECIMALS), target_type=target_type,
        exit_time=exit_time, exit_price=round(exit_price, PRICE_DECIMALS), exit_reason=exit_reason,
        r_multiple=round(r_multiple, 3), pnl_usd=round(pnl_usd, 2),
    )


def run_backtest() -> list[Trade]:
    bars = load_bars(INPUT_CSV)
    by_date = group_by_date(bars)
    trading_days = sorted(by_date.keys())
    daily_hl = build_daily_ohlc(by_date)
    daily_chrono = [(d, daily_hl[d][0], daily_hl[d][1]) for d in trading_days]
    swing_lows, swing_highs = find_swing_points(daily_chrono)

    trades: list[Trade] = []
    risk_amount = STARTING_BALANCE * RISK_PCT
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for i, d in enumerate(trading_days):
        if d.weekday() >= 5 or i == 0:
            continue
        for session_label, bias_ref_time, window_start, window_end in SESSIONS:
            trade = process_session(
                session_label, bias_ref_time, window_start, window_end, d, i,
                trading_days, by_date, daily_hl, swing_lows, swing_highs, risk_amount, skip,
            )
            if trade is not None:
                trades.append(trade)

    print("Skip funnel:", skip_counts, f"(weekdays considered: {sum(1 for d in trading_days if d.weekday() < 5) - 1})")
    return trades


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "session", "direction", "fvg_upper", "fvg_lower", "fvg_formed_time_ny",
            "entry_time_ny", "entry_price", "stop", "target", "target_type",
            "exit_time_ny", "exit_price", "exit_reason", "r_multiple", "pnl_usd",
        ])
        for t in trades:
            w.writerow([
                t.date, t.session, t.direction, t.fvg_upper, t.fvg_lower,
                t.fvg_formed_time.strftime("%Y-%m-%d %H:%M"),
                t.entry_time.strftime("%Y-%m-%d %H:%M"), t.entry_price, t.stop, t.target, t.target_type,
                t.exit_time.strftime("%Y-%m-%d %H:%M"), t.exit_price, t.exit_reason,
                t.r_multiple, t.pnl_usd,
            ])


def summarize(trades: list[Trade]) -> None:
    n = len(trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    win_rate = len(wins) / n * 100 if n else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_r = sum(t.r_multiple for t in trades)
    print(f"Total trades: {n}")
    print(f"Wins: {len(wins)}  Losses: {len(losses)}")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Gross profit: ${gross_profit:,.2f}  Gross loss: ${gross_loss:,.2f}")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Total R: {total_r:.2f}  Avg R/trade: {(total_r/n if n else 0):.3f}")
    print(f"Net P&L (1% risk, ${STARTING_BALANCE:,.0f} base): ${sum(t.pnl_usd for t in trades):,.2f}")
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    print("Exit reasons:", by_reason)
    by_dir: dict[str, int] = {}
    for t in trades:
        by_dir[t.direction] = by_dir.get(t.direction, 0) + 1
    print("By direction:", by_dir)

    for session_label in {t.session for t in trades}:
        sub = [t for t in trades if t.session == session_label]
        sub_wins = [t for t in sub if t.pnl_usd > 0]
        sub_gp = sum(t.pnl_usd for t in sub_wins)
        sub_gl = abs(sum(t.pnl_usd for t in sub if t.pnl_usd <= 0))
        sub_pf = sub_gp / sub_gl if sub_gl > 0 else float("inf")
        print(
            f"  [{session_label}] trades={len(sub)} win_rate={len(sub_wins)/len(sub)*100:.1f}% "
            f"PF={sub_pf:.2f} net=${sum(t.pnl_usd for t in sub):,.2f}"
        )


if __name__ == "__main__":
    trades = run_backtest()
    write_trades_csv(trades, TRADES_OUT)
    summarize(trades)
    print(f"\nTrade log written to {TRADES_OUT}")
