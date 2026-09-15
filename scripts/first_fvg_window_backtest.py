"""First FVG window backtest: the trades one live bot would take, priced like its paper broker.

Per NY day: strategy.first_fvg_window.find_plan() on the signal bars. A limit at the plan's
entry works on M1 bars from candle 3's close until New York midnight. Once it fills, the stop
and target work on M1 bars with no end-of-day close. Every fill is priced by
execution/level_fill.py, the rules PaperBroker(level_fills=True) applies, so a paper trade and
its backtest twin can be matched exactly (scripts/first_fvg_paper_parity.py does that).

One position at a time, like the bot: a day's setup is skipped while the previous trade is
still open at candle 3's close.

R is measured in the plan's risk (entry edge to stop), and spread is charged once per trade
in those units.

Usage:
    python -m scripts.first_fvg_window_backtest --m1-csv data/history/fundingpips/NDX100_M1.csv \\
        --signal-csv data/history/fundingpips/NDX100_M15.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Bar, OrderType, SignalDirection  # noqa: E402
from execution.level_fill import exit_fill, limit_fill  # noqa: E402
from strategy.first_fvg_window import FirstFvgWindowConfig, find_plan, order_expires_at, setup_id  # noqa: E402

NY = ZoneInfo("America/New_York")
TRADING_BREAK = timedelta(minutes=30)  # same threshold PaperBroker uses for a stale session open


@dataclass(frozen=True)
class FvgTrade:
    day: date
    setup_id: str
    direction: str
    entry_time: datetime
    entry: float
    stop: float
    target: float
    exit_time: datetime
    exit_price: float
    reason: str
    r_gross: float
    r_net: float


class ListBars:
    """A list of Bars, searchable by time."""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self._times = [b.timestamp for b in bars]

    def __len__(self) -> int:
        return len(self._bars)

    def __getitem__(self, i: int) -> Bar:
        return self._bars[i]

    def first_at_or_after(self, ts: datetime) -> int:
        return bisect_left(self._times, ts)


class FrameBars:
    """Bars backed by numpy columns, built on access -- years of M1 as Bar objects would not fit."""

    def __init__(self, frame) -> None:  # a load_m1() DataFrame
        index = frame.index.as_unit("ns")
        self._ns = index.asi8
        self._o, self._h, self._l, self._c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))

    def __len__(self) -> int:
        return len(self._ns)

    def __getitem__(self, i: int) -> Bar:
        ts = datetime.fromtimestamp(self._ns[i] // 1_000_000_000, UTC)
        return Bar(timestamp=ts, open=self._o[i], high=self._h[i], low=self._l[i], close=self._c[i], volume=0.0)

    def first_at_or_after(self, ts: datetime) -> int:
        return int(np.searchsorted(self._ns, int(ts.timestamp()) * 1_000_000_000, side="left"))


def run_backtest(
    signal_bars: list[Bar],
    m1_bars: list[Bar] | FrameBars,
    cfg: FirstFvgWindowConfig,
    symbol: str,
    spread: float,
) -> list[FvgTrade]:
    m1 = m1_bars if isinstance(m1_bars, FrameBars) else ListBars(m1_bars)
    by_day: dict[date, list[Bar]] = {}
    for bar in signal_bars:
        by_day.setdefault(bar.timestamp.astimezone(NY).date(), []).append(bar)

    trades: list[FvgTrade] = []
    busy_until: datetime | None = None
    for day, day_bars in by_day.items():
        plan = find_plan(day_bars, cfg)
        if plan is None or (busy_until is not None and busy_until > plan.confirm_close):
            continue

        long = plan.direction == SignalDirection.BUY
        order_type = OrderType.BUY_LIMIT if long else OrderType.SELL_LIMIT
        expires = order_expires_at(plan)
        entry_i = entry_price = None
        for i in range(m1.first_at_or_after(plan.confirm_close), len(m1)):
            bar = m1[i]
            if bar.timestamp >= expires:
                break
            price = limit_fill(order_type, plan.entry, bar)
            if price is not None:
                entry_i, entry_price = i, price
                break
        if entry_i is None:
            continue

        exit_hit = None
        for i in range(entry_i, len(m1)):
            bar = m1[i]
            after_break = i > 0 and bar.timestamp - m1[i - 1].timestamp > TRADING_BREAK
            hit = exit_fill(plan.direction, plan.stop, plan.target, bar,
                            entry_bar=i == entry_i, after_break=after_break)
            if hit is not None:
                exit_hit = (bar.timestamp, *hit)
                break
        if exit_hit is None:
            busy_until = datetime.max.replace(tzinfo=UTC)  # still open when the data ends
            continue

        exit_time, exit_price, reason = exit_hit
        sign = 1 if long else -1
        plan_risk = (plan.entry - plan.stop) * sign
        r_gross = (exit_price - entry_price) * sign / plan_risk
        trades.append(FvgTrade(
            day=day, setup_id=setup_id(symbol, plan), direction="LONG" if long else "SHORT",
            entry_time=m1[entry_i].timestamp, entry=entry_price, stop=plan.stop, target=plan.target,
            exit_time=exit_time, exit_price=exit_price, reason=reason,
            r_gross=r_gross, r_net=r_gross - spread / plan_risk,
        ))
        busy_until = exit_time
    return trades


def summarize(trades: list[FvgTrade]) -> str:
    if not trades:
        return "n=0"
    rs = [t.r_net for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    pf = sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")
    wr = sum(1 for r in rs if r > 0) / len(rs) * 100
    return f"n={len(rs)} WR={wr:.1f}% PF={pf:.3f} netR={sum(rs):+.1f}"


def main() -> None:
    from scripts.backtest_common import load_m1, resample
    from scripts.two_strategy_symbol_sweep import recent_spread

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--m1-csv", required=True)
    ap.add_argument("--signal-csv", help="Native signal-timeframe CSV; resampled from M1 when omitted")
    ap.add_argument("--symbol", default="NDX100")
    ap.add_argument("--bar-minutes", type=int, default=15)
    ap.add_argument("--tp-r", type=float, default=3.0)
    ap.add_argument("--session-start", default="10:00")
    ap.add_argument("--c1-bars-before", type=int, default=1)
    ap.add_argument("--third-candle-before", default="11:00")
    ap.add_argument("--spread", type=float, help="Points per trade; defaults to the M1 file's 2026 mean")
    ap.add_argument("--out-csv")
    args = ap.parse_args()

    cfg = FirstFvgWindowConfig(
        session_start=time.fromisoformat(args.session_start), c1_bars_before=args.c1_bars_before,
        third_candle_before=time.fromisoformat(args.third_candle_before), tp_r=args.tp_r,
        bar_minutes=args.bar_minutes,
    )
    spread = args.spread if args.spread is not None else recent_spread(Path(args.m1_csv))
    m1 = load_m1(args.m1_csv)
    signal = load_m1(args.signal_csv) if args.signal_csv else resample(m1, args.bar_minutes)
    signal_bars = [Bar(timestamp=ts.to_pydatetime().astimezone(UTC), open=r.open, high=r.high, low=r.low,
                       close=r.close, volume=0.0) for ts, r in zip(signal.index, signal.itertuples())]

    trades = run_backtest(signal_bars, FrameBars(m1), cfg, args.symbol, spread)
    one_year = trades[-1].day - timedelta(days=365) if trades else None
    print(f"{args.symbol} {cfg} spread={spread}")
    print("full history:", summarize(trades))
    print("last 365 days:", summarize([t for t in trades if one_year and t.day > one_year]))
    if args.out_csv:
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()) if trades else ["day"])
            w.writeheader()
            for t in trades:
                w.writerow(asdict(t))


if __name__ == "__main__":
    main()
