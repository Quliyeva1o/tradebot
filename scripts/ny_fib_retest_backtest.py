"""NY 09:30-10:00 Fibonacci retest backtest: the trades one live bot would take.

Per NY day: strategy.ny_fib_retest.find_plan() on the morning's M1 bars. A limit at the
range's 0.5 midline works from the bias print until the day's cancel time; once it fills,
the 0.236 stop and the 1.618 target work on M1 bars with NO time exit -- the trade is
held until one of them is hit, overnight and over weekends included.

One position at a time, like the bot: a day's setup is skipped while the previous trade
is still open at the new bias print. --allow-overlap takes every setup instead, which
measures the rule rather than the deployable bot.

Fills are priced by execution/level_fill.py, the rules PaperBroker(level_fills=True)
applies, so a paper trade and its backtest twin can be compared exactly. R is the plan's
risk (0.5 to 0.236, i.e. 0.264 of the range), and spread is charged once per trade in
those units -- a fixed-point spread costs MORE R on a narrow-range day, so the net and
gross columns can tell different stories.

Usage:
    python -m scripts.ny_fib_retest_backtest --m1-csv data/history/fundingpips/NDX100_M1.csv \
        --symbol NDX100 --out-csv NDX100_ny_fib_retest_trades.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Bar, OrderType, SignalDirection  # noqa: E402
from execution.level_fill import exit_fill, limit_fill  # noqa: E402
from scripts.backtest_common import load_m1  # noqa: E402
from scripts.first_fvg_window_backtest import FrameBars  # noqa: E402
from scripts.two_strategy_symbol_sweep import recent_spread  # noqa: E402
from strategy.ny_fib_retest import (  # noqa: E402
    FibPlan,
    NyFibRetestConfig,
    find_plan,
    order_expires_at,
    setup_id,
)

NY = ZoneInfo("America/New_York")
TRADING_BREAK = timedelta(minutes=30)  # same threshold PaperBroker uses for a stale session open


@dataclass(frozen=True)
class FibTrade:
    day: date
    setup_id: str
    direction: str
    range_high: float
    range_low: float
    range_points: float
    bias_price: float
    entry_time: datetime
    entry: float
    stop: float
    target: float
    exit_time: datetime
    exit_price: float
    reason: str
    bars_held: int
    r_gross: float
    r_net: float


def morning_bars_by_day(frame, cfg: NyFibRetestConfig) -> dict[date, list[Bar]]:
    """Each NY date's M1 bars from the range start up to the bias print, as Bars.

    Only this slice is materialised: the whole M1 history as Bar objects would not fit,
    and find_plan reads nothing later than the bias bar.
    """
    minutes = frame.index.hour * 60 + frame.index.minute
    first = cfg.range_start.hour * 60 + cfg.range_start.minute
    last = cfg.bias_at.hour * 60 + cfg.bias_at.minute  # exclusive: the bias bar is last-1
    slice_ = frame[(minutes >= first) & (minutes < last)]

    by_day: dict[date, list[Bar]] = defaultdict(list)
    for ts, row in zip(slice_.index, slice_.itertuples()):
        by_day[ts.date()].append(
            Bar(timestamp=ts.to_pydatetime().astimezone(UTC), open=row.open, high=row.high,
                low=row.low, close=row.close, volume=0.0)
        )
    return by_day


def run_backtest(
    frame,
    cfg: NyFibRetestConfig,
    symbol: str,
    spread: float,
    *,
    allow_overlap: bool = False,
) -> tuple[list[FibTrade], dict[str, int]]:
    m1 = FrameBars(frame)
    by_day = morning_bars_by_day(frame, cfg)

    trades: list[FibTrade] = []
    counts = {"days": len(by_day), "no_plan": 0, "busy": 0, "no_fill": 0, "still_open": 0}
    busy_until: datetime | None = None

    for day in sorted(by_day):
        plan: FibPlan | None = find_plan(by_day[day], cfg)
        if plan is None:
            counts["no_plan"] += 1
            continue
        if not allow_overlap and busy_until is not None and busy_until > plan.bias_time:
            counts["busy"] += 1
            continue

        long = plan.direction == SignalDirection.BUY
        order_type = OrderType.BUY_LIMIT if long else OrderType.SELL_LIMIT
        expires = order_expires_at(plan, cfg)

        entry_i = entry_price = None
        for i in range(m1.first_at_or_after(plan.bias_time), len(m1)):
            bar = m1[i]
            if bar.timestamp >= expires:
                break
            price = limit_fill(order_type, plan.entry, bar)
            if price is not None:
                entry_i, entry_price = i, price
                break
        if entry_i is None:
            counts["no_fill"] += 1
            continue

        exit_hit = None
        for i in range(entry_i, len(m1)):
            bar = m1[i]
            after_break = i > 0 and bar.timestamp - m1[i - 1].timestamp > TRADING_BREAK
            hit = exit_fill(plan.direction, plan.stop, plan.target, bar,
                            entry_bar=i == entry_i, after_break=after_break)
            if hit is not None:
                exit_hit = (i, bar.timestamp, *hit)
                break
        if exit_hit is None:
            counts["still_open"] += 1
            busy_until = datetime.max.replace(tzinfo=UTC)  # still open when the data ends
            continue

        exit_i, exit_time, exit_price, reason = exit_hit
        sign = 1 if long else -1
        r_gross = (exit_price - entry_price) * sign / plan.risk
        trades.append(FibTrade(
            day=day, setup_id=setup_id(symbol, plan), direction="LONG" if long else "SHORT",
            range_high=plan.range_high, range_low=plan.range_low,
            range_points=plan.range_high - plan.range_low, bias_price=plan.bias_price,
            entry_time=m1[entry_i].timestamp, entry=entry_price, stop=plan.stop, target=plan.target,
            exit_time=exit_time, exit_price=exit_price, reason=reason, bars_held=exit_i - entry_i,
            r_gross=r_gross, r_net=r_gross - spread / plan.risk,
        ))
        busy_until = exit_time
    return trades, counts


def summarize(trades: list[FibTrade], field: str = "r_net") -> str:
    if not trades:
        return "n=0"
    rs = [getattr(t, field) for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    pf = sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")
    wr = sum(1 for r in rs if r > 0) / len(rs) * 100
    return f"n={len(rs):<5} WR={wr:5.1f}%  PF={pf:6.3f}  netR={sum(rs):+8.1f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--m1-csv", required=True)
    ap.add_argument("--symbol", default="NDX100")
    ap.add_argument("--range-start", default="09:30")
    ap.add_argument("--range-end", default="10:00")
    ap.add_argument("--bias-at", default="10:01")
    ap.add_argument("--entry-fib", type=float, default=0.5)
    ap.add_argument("--stop-fib", type=float, default=0.236)
    ap.add_argument("--target-fib", type=float, default=1.618)
    ap.add_argument("--order-expires", default="16:00")
    ap.add_argument("--allow-overlap", action="store_true")
    ap.add_argument("--since", help="Skip CSV rows before this broker-local date")
    ap.add_argument("--spread", type=float, help="Points per trade; defaults to the M1 file's 2026 mean")
    ap.add_argument("--out-csv")
    args = ap.parse_args()

    cfg = NyFibRetestConfig(
        range_start=time.fromisoformat(args.range_start),
        range_end=time.fromisoformat(args.range_end),
        bias_at=time.fromisoformat(args.bias_at),
        entry_fib=args.entry_fib, stop_fib=args.stop_fib, target_fib=args.target_fib,
        order_expires=time.fromisoformat(args.order_expires),
    )
    spread = args.spread if args.spread is not None else recent_spread(Path(args.m1_csv))
    frame = load_m1(args.m1_csv, args.since)
    trades, counts = run_backtest(frame, cfg, args.symbol, spread, allow_overlap=args.allow_overlap)

    overlap_note = ", overlapping setups allowed" if args.allow_overlap else ""
    print(f"\n{args.symbol}  {cfg.range_start:%H:%M}-{cfg.range_end:%H:%M} range, bias at "
          f"{cfg.bias_at:%H:%M}, entry {cfg.entry_fib} / stop {cfg.stop_fib} / target "
          f"{cfg.target_fib} ({cfg.reward_r:.2f}R), spread={spread}{overlap_note}")
    print(f"days={counts['days']}  no_plan={counts['no_plan']}  skipped_busy={counts['busy']}  "
          f"never_filled={counts['no_fill']}  open_at_end={counts['still_open']}  trades={len(trades)}")

    one_year = trades[-1].day - timedelta(days=365) if trades else None
    print(f"  full history   gross: {summarize(trades, 'r_gross')}")
    print(f"  full history   net  : {summarize(trades, 'r_net')}")
    recent = [t for t in trades if one_year and t.day > one_year]
    print(f"  last 365 days  net  : {summarize(recent, 'r_net')}")

    by_year: dict[int, list[FibTrade]] = defaultdict(list)
    for t in trades:
        by_year[t.day.year].append(t)
    print("\n  per year (net):")
    for year in sorted(by_year):
        print(f"    {year}  {summarize(by_year[year], 'r_net')}")

    if trades:
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        print(f"\n    LONG   {summarize(longs, 'r_net')}")
        print(f"    SHORT  {summarize(shorts, 'r_net')}")
        held = sorted(t.bars_held for t in trades)
        print(f"\n  M1 bars held: median={held[len(held) // 2]}  "
              f"p90={held[int(len(held) * 0.9)]}  max={held[-1]}")

    if args.out_csv and trades:
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
            w.writeheader()
            for t in trades:
                w.writerow(asdict(t))
        print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
