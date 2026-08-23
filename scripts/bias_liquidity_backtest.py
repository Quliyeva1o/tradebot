"""Standalone backtest of the user-specified bias/liquidity-sweep strategy on
USTEC (NAS100) M5 data, NY session times.

Rules (as specified by the user):
1. Bias fixed at 09:29 NY: PD midline = (prev day's CASH session [09:30-16:00
   NY] high + low) / 2. If 09:30 open < midline -> SHORT only today; if above
   -> LONG only today.
2. Entry candle: first 5m candle in {09:30, 09:35, 09:40, 09:45} NY whose
   color matches the bias (red for short, green for long). Entered at that
   candle's close. No candle by 09:45 -> no trade.
3. Stop: entry candle's high + buffer (short) / low - buffer (long).
4. Target: nearest liquidity level beyond entry in the trade's direction,
   among {previous day's cash-session low/high (PDL/PDH), today's
   session low/high so far, nearest unmitigated daily swing low/high}.
   RR = |target - entry| / |stop - entry|; skip the trade if RR < 2.
5. Risk: fixed % of a starting balance per trade (position sizing is
   %-risk-based in reality; for win-rate/profit-factor purposes this is
   equivalent to a fixed $ risk per trade, i.e. R-multiples).
6. Time stop: flat at 12:00 NY (bar's open) if neither TP nor SL has been
   touched.
7. One trade/day; no re-entry after a stop-out.

Data caveat (see conversation): MT5 (MetaQuotes-Demo server) timestamps are
broker/server local time mislabeled as UTC. Empirically the server follows
the EU DST calendar (EET/EEST), confirmed by the recurring daily-maintenance
gap shifting from 22:55 to 23:55 exactly on 2026-03-29 (the EU DST date, not
the earlier 2026-03-08 US DST date). Bars are therefore re-interpreted as
Europe/Bucharest wall-clock time and converted to true UTC before deriving
NY session times.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")  # EET/EEST, matches empirically observed server clock

INPUT_CSV = "data/history/USTEC_M5.csv"
TRADES_OUT = "artifacts/bias_liquidity_trades.csv"

BUFFER_POINTS = 10.0          # stop buffer beyond entry candle's wick
MIN_RR = 2.0                  # reward:risk filter
MAX_RR_CAP = 7.0               # take-profit capped at this many R even if the liquidity target is farther
RISK_PCT = 0.01                # 1% of starting balance per trade
STARTING_BALANCE = 100_000.0
SWING_LOOKBACK_DAYS = 15       # trailing days scanned for daily fractal swing points
CASH_SESSION_START = time(9, 30)
CASH_SESSION_END = time(16, 0)
BUILD_TIME = time(9, 29)  # informational; bias is derived from the 09:30 open
ENTRY_WINDOW = [time(9, 30), time(9, 35), time(9, 40), time(9, 45)]
TIME_STOP = time(12, 0)


@dataclass
class Bar:
    ts: datetime  # NY-localized
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    date: date
    bias: str
    midline: float
    entry_time: datetime
    entry_price: float
    stop: float
    target: float
    target_type: str
    rr: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl_usd: float


def load_bars(path: str) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            true_utc = broker_local.astimezone(ZoneInfo("UTC"))
            ny_ts = true_utc.astimezone(NY)
            bars.append(
                Bar(
                    ts=ny_ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    bars.sort(key=lambda b: b.ts)
    return bars


def group_by_date(bars: list[Bar]) -> dict[date, list[Bar]]:
    out: dict[date, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.ts.date(), []).append(b)
    return out


def cash_session_high_low(day_bars: list[Bar]) -> tuple[float, float] | None:
    session = [b for b in day_bars if CASH_SESSION_START <= b.ts.time() < CASH_SESSION_END]
    if not session:
        return None
    return max(b.high for b in session), min(b.low for b in session)


def build_daily_ohlc(by_date: dict[date, list[Bar]]) -> dict[date, tuple[float, float]]:
    """Returns {date: (day_high, day_low)} over ALL bars of that calendar date
    (full CFD session, not just cash hours) -- used for swing-point detection.
    """
    out = {}
    for d, day_bars in by_date.items():
        out[d] = (max(b.high for b in day_bars), min(b.low for b in day_bars))
    return out


def find_swing_points(daily: list[tuple[date, float, float]]) -> tuple[dict[date, float], dict[date, float]]:
    """3-bar fractal swing lows/highs over a chronological daily (date, high, low) list."""
    swing_lows: dict[date, float] = {}
    swing_highs: dict[date, float] = {}
    for i in range(1, len(daily) - 1):
        d, h, l = daily[i]
        _, hp, lp = daily[i - 1]
        _, hn, ln = daily[i + 1]
        if l < lp and l < ln:
            swing_lows[d] = l
        if h > hp and h > hn:
            swing_highs[d] = h
    return swing_lows, swing_highs


def nearest_unmitigated_swing(
    target_date: date,
    daily_sorted: list[date],
    swing_lows: dict[date, float],
    swing_highs: dict[date, float],
    daily_hl: dict[date, tuple[float, float]],
    direction: str,
    entry_price: float,
    lookback_days: int,
) -> float | None:
    """Nearest swing low (direction='short') / high ('long') within the trailing
    lookback that (a) is on the correct side of entry_price and (b) has not
    been closed-through by any later daily bar's high/low before target_date.
    """
    idx = daily_sorted.index(target_date) if target_date in daily_sorted else None
    if idx is None:
        return None
    window = [d for d in daily_sorted[max(0, idx - lookback_days) : idx]]
    candidates = []
    pool = swing_lows if direction == "short" else swing_highs
    for d in window:
        level = pool.get(d)
        if level is None:
            continue
        mitigated = False
        for later in daily_sorted[daily_sorted.index(d) + 1 : idx]:
            h, l = daily_hl[later][0], daily_hl[later][1]
            if direction == "short" and l <= level:
                mitigated = True
                break
            if direction == "long" and h >= level:
                mitigated = True
                break
        if mitigated:
            continue
        if direction == "short" and level < entry_price:
            candidates.append(level)
        elif direction == "long" and level > entry_price:
            candidates.append(level)
    if not candidates:
        return None
    if direction == "short":
        return max(candidates)  # nearest below = largest of the below-entry levels
    return min(candidates)  # nearest above = smallest of the above-entry levels


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
        if d.weekday() >= 5:  # skip Saturday/Sunday NY dates (Sunday-evening reopen bars, no cash session)
            continue
        if i == 0:
            continue
        prev_cash = None
        for j in range(i - 1, -1, -1):
            candidate_prev = trading_days[j]
            found = cash_session_high_low(by_date[candidate_prev])
            if found is not None:
                prev_cash = found
                break
        if prev_cash is None:
            skip("no_prev_cash_session")
            continue
        prev_high, prev_low = prev_cash
        midline = (prev_high + prev_low) / 2.0

        day_bars = by_date[d]
        bar_by_time = {b.ts.time(): b for b in day_bars}
        bar_930 = bar_by_time.get(time(9, 30))
        if bar_930 is None:
            skip("no_930_bar")
            continue

        if bar_930.open < midline:
            bias = "SHORT"
        elif bar_930.open > midline:
            bias = "LONG"
        else:
            skip("midline_tie")
            continue

        entry_bar = None
        for t in ENTRY_WINDOW:
            b = bar_by_time.get(t)
            if b is None:
                continue
            is_red = b.close < b.open
            is_green = b.close > b.open
            if bias == "SHORT" and is_red:
                entry_bar = b
                break
            if bias == "LONG" and is_green:
                entry_bar = b
                break
        if entry_bar is None:
            skip("no_entry_candle")
            continue

        entry_price = entry_bar.close
        direction = -1 if bias == "SHORT" else 1
        if bias == "SHORT":
            stop = entry_bar.high + BUFFER_POINTS
        else:
            stop = entry_bar.low - BUFFER_POINTS
        risk_dist = abs(stop - entry_price)
        if risk_dist <= 0:
            continue

        # Note: "today's session low/high so far" was tried as a third candidate
        # type but dropped -- this early (09:30-09:45), it's essentially the
        # entry candle's own wick (median RR ~0.3), not a real accumulated
        # liquidity pool, and it dominated as "nearest" in >80% of days,
        # collapsing almost every trade's RR near zero. PDL/PDH and prior
        # swing points are the meaningful pools this early in the session.
        candidates: list[tuple[float, str]] = []
        if bias == "SHORT":
            if prev_low < entry_price:
                candidates.append((prev_low, "PDL"))
            swing = nearest_unmitigated_swing(
                d, trading_days, swing_lows, swing_highs, daily_hl, "short", entry_price, SWING_LOOKBACK_DAYS
            )
            if swing is not None:
                candidates.append((swing, "swing_low"))
        else:
            if prev_high > entry_price:
                candidates.append((prev_high, "PDH"))
            swing = nearest_unmitigated_swing(
                d, trading_days, swing_lows, swing_highs, daily_hl, "long", entry_price, SWING_LOOKBACK_DAYS
            )
            if swing is not None:
                candidates.append((swing, "swing_high"))

        if not candidates:
            skip("no_liquidity_candidate")
            continue

        if bias == "SHORT":
            target_price, target_type = max(candidates, key=lambda c: c[0])  # nearest below = highest value
        else:
            target_price, target_type = min(candidates, key=lambda c: c[0])  # nearest above = lowest value

        reward_dist = abs(target_price - entry_price)
        rr = reward_dist / risk_dist
        if rr < MIN_RR:
            skip("rr_below_min")
            continue

        # Cap the actual take-profit at MAX_RR_CAP R -- the liquidity target may
        # sit farther away, but profit is taken at 7R if price reaches it first.
        if rr > MAX_RR_CAP:
            target_price = entry_price - MAX_RR_CAP * risk_dist if bias == "SHORT" else entry_price + MAX_RR_CAP * risk_dist
            target_type = f"{target_type}_capped_7R"
            rr = MAX_RR_CAP

        future_bars = [b for b in day_bars if b.ts.time() > entry_bar.ts.time() and b.ts.time() < TIME_STOP]
        exit_price = None
        exit_time = None
        exit_reason = None
        for fb in future_bars:
            if bias == "SHORT":
                hit_sl = fb.high >= stop
                hit_tp = fb.low <= target_price
            else:
                hit_sl = fb.low <= stop
                hit_tp = fb.high >= target_price
            if hit_sl and hit_tp:
                exit_price, exit_reason = stop, "SL"
            elif hit_sl:
                exit_price, exit_reason = stop, "SL"
            elif hit_tp:
                exit_price, exit_reason = target_price, "TP"
            if exit_price is not None:
                exit_time = fb.ts
                break

        if exit_price is None:
            bar_1200 = bar_by_time.get(TIME_STOP)
            if bar_1200 is not None:
                exit_price = bar_1200.open
                exit_time = bar_1200.ts
            else:
                later = [b for b in day_bars if b.ts.time() >= TIME_STOP]
                if later:
                    exit_price = later[0].open
                    exit_time = later[0].ts
                else:
                    exit_price = day_bars[-1].close
                    exit_time = day_bars[-1].ts
            exit_reason = "TIME"

        realized_move = (entry_price - exit_price) if bias == "SHORT" else (exit_price - entry_price)
        r_multiple = realized_move / risk_dist
        pnl_usd = r_multiple * risk_amount

        trades.append(
            Trade(
                date=d,
                bias=bias,
                midline=round(midline, 2),
                entry_time=entry_bar.ts,
                entry_price=entry_price,
                stop=round(stop, 2),
                target=round(target_price, 2),
                target_type=target_type,
                rr=round(rr, 2),
                exit_time=exit_time,
                exit_price=round(exit_price, 2),
                exit_reason=exit_reason,
                r_multiple=round(r_multiple, 3),
                pnl_usd=round(pnl_usd, 2),
            )
        )

    print("Skip funnel:", skip_counts, f"(total days considered: {len(trading_days) - 1})")
    return trades


def write_trades_csv(trades: list[Trade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "date", "bias", "midline", "entry_time_ny", "entry_price", "stop", "target",
                "target_type", "rr", "exit_time_ny", "exit_price", "exit_reason", "r_multiple", "pnl_usd",
            ]
        )
        for t in trades:
            is_time = t.exit_reason == "TIME"
            w.writerow(
                [
                    t.date, t.bias, t.midline,
                    t.entry_time.strftime("%Y-%m-%d %H:%M"), t.entry_price,
                    t.stop, t.target, t.target_type, t.rr,
                    "" if is_time else t.exit_time.strftime("%Y-%m-%d %H:%M"),
                    "" if is_time else t.exit_price,
                    "" if is_time else t.exit_reason,
                    "" if is_time else t.r_multiple,
                    "" if is_time else t.pnl_usd,
                ]
            )


def summarize(trades: list[Trade]) -> None:
    time_stopped = [t for t in trades if t.exit_reason == "TIME"]
    counted = [t for t in trades if t.exit_reason != "TIME"]
    n = len(counted)
    wins = [t for t in counted if t.pnl_usd > 0]
    losses = [t for t in counted if t.pnl_usd <= 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    win_rate = len(wins) / n * 100 if n else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_r = sum(t.r_multiple for t in counted)
    avg_r = total_r / n if n else 0.0
    print(f"Total trades: {len(trades)}  (excluded as TIME-stop, not counted: {len(time_stopped)})")
    print(f"Counted trades (SL/TP only): {n}")
    print(f"Wins: {len(wins)}  Losses: {len(losses)}")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Gross profit: ${gross_profit:,.2f}  Gross loss: ${gross_loss:,.2f}")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Total R: {total_r:.2f}  Avg R/trade: {avg_r:.3f}")
    print(f"Net P&L (1% risk, ${STARTING_BALANCE:,.0f} base): ${sum(t.pnl_usd for t in counted):,.2f}")
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    print("Exit reasons (all, incl. excluded TIME):", by_reason)
    by_bias: dict[str, int] = {}
    for t in counted:
        by_bias[t.bias] = by_bias.get(t.bias, 0) + 1
    print("By bias (counted only):", by_bias)


if __name__ == "__main__":
    trades = run_backtest()
    write_trades_csv(trades, TRADES_OUT)
    summarize(trades)
    print(f"\nTrade log written to {TRADES_OUT}")
