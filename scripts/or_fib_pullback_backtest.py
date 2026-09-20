"""Opening-range Fibonacci pullback backtest: the trades one live bot would take.

Per NY day: strategy.or_fib_pullback.DayState is fed the day's M1 bars from 09:30 to the
entry deadline and returns at most one entry. The stop and target then work on M1 bars,
priced by execution/level_fill.py -- the rules PaperBroker(level_fills=True) applies --
until one is hit or the day's cancel time closes the trade at the prevailing price.

The ATR the momentum filter compares the range against is the M15 ATR re-stamped by
`htf_bias_known_from`, so a 09:30 decision can only ever read an M15 bar that closed at
or before 09:30. Without that guard the filter would be reading the opening range's own
bar -- see the warning on that function.

One position at a time: a day's setup is skipped while the previous trade is still open.
That only ever bites with --no-time-exit, since a midday close ends every trade the same
day it started.

R is the entry-to-stop distance, and spread is charged once per trade in those units.

Usage:
    python -m scripts.or_fib_pullback_backtest --m1-csv data/history/fundingpips/NDX100_M1.csv \
        --symbol NDX100 --sweep
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import SignalDirection  # noqa: E402
from execution.level_fill import exit_fill  # noqa: E402
from scripts.backtest_common import compute_atr, htf_bias_known_from, load_m1, resample  # noqa: E402
from scripts.first_fvg_window_backtest import FrameBars  # noqa: E402
from scripts.two_strategy_symbol_sweep import recent_spread  # noqa: E402
from strategy.or_fib_pullback import (  # noqa: E402
    DayState,
    FibEntry,
    OrFibPullbackConfig,
    atr_ok,
    opening_range,
    setup_id,
)

NY = ZoneInfo("America/New_York")
TRADING_BREAK = timedelta(minutes=30)  # same threshold PaperBroker uses for a stale session open


@dataclass(frozen=True)
class PullbackTrade:
    day: date
    setup_id: str
    direction: str
    range_high: float
    range_low: float
    range_points: float
    atr: float
    break_time: datetime
    morning_extreme: float
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


@dataclass(frozen=True)
class Counts:
    days: int = 0
    no_range: int = 0
    atr_filtered: int = 0
    no_entry: int = 0
    busy: int = 0
    still_open: int = 0

    def plus(self, **kw: int) -> Counts:
        return replace(self, **{k: getattr(self, k) + v for k, v in kw.items()})


class Session:
    """One symbol's M1 history, parsed once and reusable across configurations."""

    @classmethod
    def from_csv(cls, path: str, since: str | None = None) -> Session:
        return cls(load_m1(path, since))

    def __init__(self, frame) -> None:  # a load_m1() DataFrame
        self.frame = frame
        self.m1 = FrameBars(self.frame)
        index = self.frame.index
        self.minutes = (index.hour * 60 + index.minute).to_numpy()
        self.day_key = index.normalize().asi8
        self._atr_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def atr_known_at(self, period: int) -> tuple[np.ndarray, np.ndarray]:
        """(timestamps_ns, values) of the M15 ATR, each stamped when it became knowable."""
        if period not in self._atr_cache:
            m15 = resample(self.frame, 15)
            known = htf_bias_known_from(compute_atr(m15, period), 15)
            self._atr_cache[period] = (known.index.as_unit("ns").asi8, known.to_numpy(float))
        return self._atr_cache[period]

    def day_windows(self, cfg: OrFibPullbackConfig) -> list[np.ndarray]:
        """Each NY date's bar positions from the range start up to the entry deadline."""
        first = cfg.range_start.hour * 60 + cfg.range_start.minute
        last = cfg.entry_deadline.hour * 60 + cfg.entry_deadline.minute
        pos = np.flatnonzero((self.minutes >= first) & (self.minutes < last))
        if len(pos) == 0:
            return []
        keys = self.day_key[pos]
        return np.split(pos, np.flatnonzero(keys[1:] != keys[:-1]) + 1)


def run_backtest(
    session: Session,
    cfg: OrFibPullbackConfig,
    symbol: str,
    spread: float,
    *,
    allow_overlap: bool = False,
) -> tuple[list[PullbackTrade], Counts]:
    m1, minutes, day_key = session.m1, session.minutes, session.day_key
    atr_ns, atr_values = session.atr_known_at(cfg.atr_period)
    close_minute = (
        cfg.close_at.hour * 60 + cfg.close_at.minute if cfg.close_at is not None else None
    )

    trades: list[PullbackTrade] = []
    counts = Counts()
    busy_until: datetime | None = None

    for window in session.day_windows(cfg):
        counts = counts.plus(days=1)
        day_bars = [m1[i] for i in window]
        or_range = opening_range(day_bars, cfg)
        if or_range is None:
            counts = counts.plus(no_range=1)
            continue

        open_ns = int(day_bars[0].timestamp.timestamp()) * 1_000_000_000
        slot = int(np.searchsorted(atr_ns, open_ns, side="right")) - 1
        atr = float(atr_values[slot]) if slot >= 0 and np.isfinite(atr_values[slot]) else None
        if not atr_ok(or_range, atr, cfg):
            counts = counts.plus(atr_filtered=1)
            continue

        state = DayState(or_range, cfg)
        found: tuple[int, FibEntry] | None = None
        for i, bar in zip(window, day_bars):
            entry = state.on_bar(bar)
            if entry is not None:
                found = (i, entry)
                break
        if found is None:
            counts = counts.plus(no_entry=1)
            continue

        entry_i, entry = found
        if not allow_overlap and busy_until is not None and busy_until > entry.time:
            counts = counts.plus(busy=1)
            continue

        exit_hit = _find_exit(m1, minutes, day_key, entry_i, entry, close_minute)
        if exit_hit is None:
            counts = counts.plus(still_open=1)
            busy_until = datetime.max.replace(tzinfo=UTC)  # still open when the data ends
            continue

        exit_i, exit_time, exit_price, reason = exit_hit
        sign = 1 if entry.direction == SignalDirection.BUY else -1
        r_gross = (exit_price - entry.price) * sign / entry.risk
        trades.append(PullbackTrade(
            day=day_bars[0].timestamp.astimezone(NY).date(), setup_id=setup_id(symbol, entry),
            direction="LONG" if sign == 1 else "SHORT",
            range_high=or_range.high, range_low=or_range.low, range_points=or_range.span,
            atr=atr if atr is not None else float("nan"),
            break_time=entry.break_time, morning_extreme=entry.morning_extreme,
            entry_time=entry.time, entry=entry.price, stop=entry.stop, target=entry.target,
            exit_time=exit_time, exit_price=exit_price, reason=reason, bars_held=exit_i - entry_i,
            r_gross=r_gross, r_net=r_gross - spread / entry.risk,
        ))
        busy_until = exit_time
    return trades, counts


def _find_exit(
    m1: FrameBars,
    minutes: np.ndarray,
    day_key: np.ndarray,
    entry_i: int,
    entry: FibEntry,
    close_minute: int | None,
) -> tuple[int, datetime, float, str] | None:
    """(index, time, price, reason) of the bar that closes the trade, or None.

    The midday close is taken at the OPEN of the first bar at or after it, which is the
    price on the clock; the stop and target are only allowed to fill on bars BEFORE it.

    ON THE ENTRY BAR THE TARGET CANNOT FILL. That bar is the one that came back to the
    pocket, so its own extreme is the move it retraced FROM -- and the target, being the
    morning extreme, sits right about there. Letting it fill would book a take-profit at
    a price that printed before the limit did. The stop still fills on the entry bar:
    that is the conservative side, and a bar that runs from the pocket to beyond the
    0.786 level really did trade through it after the entry.
    """
    long = entry.direction == SignalDirection.BUY
    unreachable = float("inf") if long else float("-inf")
    entry_day = day_key[entry_i]
    for j in range(entry_i, len(m1)):
        bar = m1[j]
        if close_minute is not None and j > entry_i and (
            day_key[j] > entry_day or minutes[j] >= close_minute
        ):
            return j, bar.timestamp, bar.open, "TIME"
        after_break = j > 0 and bar.timestamp - m1[j - 1].timestamp > TRADING_BREAK
        target = entry.target if j > entry_i else unreachable
        hit = exit_fill(entry.direction, entry.stop, target, bar,
                        entry_bar=j == entry_i, after_break=after_break)
        if hit is not None:
            return j, bar.timestamp, *hit
    return None


def summarize(trades: list[PullbackTrade], field: str = "r_net") -> str:
    if not trades:
        return f"{'n=0':<44}"
    rs = [getattr(t, field) for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    pf = sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")
    wr = sum(1 for r in rs if r > 0) / len(rs) * 100
    return f"n={len(rs):<5} WR={wr:5.1f}%  PF={pf:6.3f}  netR={sum(rs):+8.1f}  avgR={sum(rs) / len(rs):+.3f}"


SLTP_VARIANTS: list[tuple[str, dict]] = [
    ("SL 0.786 / TP morning", {"stop_at_swing_extreme": False, "target_extension_fib": None}),
    ("SL swing  / TP morning", {"stop_at_swing_extreme": True, "target_extension_fib": None}),
    ("SL 0.786 / TP 1.272ext", {"stop_at_swing_extreme": False, "target_extension_fib": 1.272}),
    ("SL swing  / TP 1.272ext", {"stop_at_swing_extreme": True, "target_extension_fib": 1.272}),
]
STOP_VARIANTS: list[tuple[str, dict]] = [
    ("SL 0.786", {"stop_at_swing_extreme": False}),
    ("SL swing", {"stop_at_swing_extreme": True}),
]
R_LADDER: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)
ENTRY_VARIANTS: list[tuple[str, dict]] = [
    ("limit    ", {"require_rejection": False}),
    ("rejection", {"require_rejection": True}),
]


def _profit_factor(trades: list[PullbackTrade], field: str) -> float:
    rs = [getattr(t, field) for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    return sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")


def _recent(trades: list[PullbackTrade], days: int = 365) -> list[PullbackTrade]:
    """The tail of `trades` inside the last `days` of the sample.

    Measured from the LAST TRADE, not from today: a symbol whose history ends early
    would otherwise silently report an empty window instead of its own final year.
    """
    if not trades:
        return []
    cutoff = trades[-1].day - timedelta(days=days)
    return [t for t in trades if t.day > cutoff]


def _report(
    label: str,
    trades: list[PullbackTrade],
    counts: Counts,
    r_mult: float | None = None,
    *,
    recent: bool = False,
) -> None:
    """One configuration's row. Gross sits beside net because the two answer different
    questions: gross says whether the rule has an edge at all, net says whether the
    edge survives a spread charged against a stop only 0.19 of the range wide.

    With a fixed-R target the row also carries BE, the win rate the configuration would
    need to break even: a winner books `r_mult` - cost and a loser -1 - cost, so
    EV = 0 at p = (1 + cost) / (r_mult + 1). Reading WR against BE says in one glance
    whether a nearer target bought enough accuracy to pay for itself.
    """
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.reason] += 1
    tail = "  ".join(f"{k}={reasons[k]}" for k in ("TP", "SL", "TIME") if reasons[k])
    if not trades:
        print(f"  {label:<34}n=0")
        return
    cost = sum(t.r_gross - t.r_net for t in trades) / len(trades)
    be = f"  BE={(1 + cost) / (r_mult + 1) * 100:5.1f}%" if r_mult is not None else ""
    print(f"  {label:<34}{summarize(trades)}{be}  PFgross={_profit_factor(trades, 'r_gross'):6.3f}"
          f"  spread={cost:.3f}R   {tail}")
    if recent:
        window = _recent(trades)
        gross = f"{_profit_factor(window, 'r_gross'):6.3f}" if window else "     -"
        pad = "" if r_mult is None else " " * 11
        print(f"  {'   last 365d':<34}{summarize(window)}{pad}  PFgross={gross}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--m1-csv", required=True)
    ap.add_argument("--symbol", default="NDX100")
    ap.add_argument("--sweep", action="store_true", help="Run the entry x SL/TP grid")
    ap.add_argument("--range-end", default="09:45")
    ap.add_argument("--entry-fib", type=float, default=0.618)
    ap.add_argument("--zone-fib", type=float, default=0.786)
    ap.add_argument("--stop-buffer-frac", type=float, default=0.02)
    ap.add_argument("--atr-mult", type=float, default=1.0)
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--entry-deadline", default="12:00")
    ap.add_argument("--no-time-exit", action="store_true")
    ap.add_argument("--require-rejection", action="store_true")
    ap.add_argument("--stop-at-swing-extreme", action="store_true")
    ap.add_argument("--target-extension-fib", type=float)
    ap.add_argument("--target-r", type=float, help="Fixed R-multiple target instead of a fib one")
    ap.add_argument("--r-sweep", action="store_true", help="Run the entry x stop x fixed-R grid")
    ap.add_argument("--r-ladder", help="Comma-separated R targets for --r-sweep")
    ap.add_argument("--last-year", action="store_true", help="Add a last-365-day row")
    ap.add_argument("--allow-overlap", action="store_true")
    ap.add_argument("--since", help="Skip CSV rows before this broker-local date")
    ap.add_argument("--spread", type=float, help="Points per trade; defaults to the M1 file's 2026 mean")
    ap.add_argument("--out-csv")
    args = ap.parse_args()

    base = OrFibPullbackConfig(
        range_end=time.fromisoformat(args.range_end),
        entry_fib=args.entry_fib, zone_fib=args.zone_fib,
        stop_buffer_frac=args.stop_buffer_frac,
        stop_at_swing_extreme=args.stop_at_swing_extreme,
        target_extension_fib=args.target_extension_fib, target_r=args.target_r,
        require_rejection=args.require_rejection,
        atr_period=args.atr_period, atr_mult=args.atr_mult,
        entry_deadline=time.fromisoformat(args.entry_deadline),
        close_at=None if args.no_time_exit else time.fromisoformat(args.entry_deadline),
    )
    spread = args.spread if args.spread is not None else recent_spread(Path(args.m1_csv))
    session = Session.from_csv(args.m1_csv, args.since)

    def run(cfg: OrFibPullbackConfig) -> tuple[list[PullbackTrade], Counts]:
        return run_backtest(session, cfg, args.symbol, spread, allow_overlap=args.allow_overlap)

    print(f"\n{args.symbol}  OR {base.range_start:%H:%M}-{base.range_end:%H:%M}, entry "
          f"{base.entry_fib} / zone {base.zone_fib}, ATR({base.atr_period}) x {base.atr_mult}, "
          f"deadline {base.entry_deadline:%H:%M}, spread={spread}")

    if args.r_sweep:
        ladder = tuple(float(x) for x in args.r_ladder.split(",")) if args.r_ladder else R_LADDER
        print("\n  entry x stop x fixed-R target grid:")
        for entry_label, entry_kw in ENTRY_VARIANTS:
            for stop_label, stop_kw in STOP_VARIANTS:
                for r_mult in ladder:
                    cfg = replace(base, **entry_kw, **stop_kw,
                                  target_extension_fib=None, target_r=r_mult)
                    trades, counts = run_backtest(
                        session, cfg, args.symbol, spread, allow_overlap=args.allow_overlap
                    )
                    _report(f"{entry_label}  {stop_label}  TP {r_mult}R", trades,
                            counts, r_mult, recent=args.last_year)
        return

    if not args.sweep:
        trades, counts = run(base)
        print(f"  {counts}")
        _report("all trades", trades, counts, recent=args.last_year)
        _by_year(trades)
        if args.out_csv:
            _write(args.out_csv, trades)
        return

    print("\n  entry x SL/TP grid (net R, spread charged):")
    for entry_label, entry_kw in ENTRY_VARIANTS:
        for sltp_label, sltp_kw in SLTP_VARIANTS:
            cfg = replace(base, **entry_kw, **sltp_kw)
            trades, counts = run(cfg)
            _report(f"{entry_label}  {sltp_label}", trades, counts, recent=args.last_year)

    print("\n  filter sensitivity (limit entry, SL 0.786 / TP morning):")
    for atr_mult in (0.0, 1.0):
        for hold in (False, True):
            cfg = replace(base, atr_mult=atr_mult, close_at=None if hold else base.entry_deadline)
            trades, counts = run(cfg)
            atr_note = "no ATR filter" if atr_mult == 0 else f"ATR x {atr_mult}"
            hold_note = "hold to SL/TP" if hold else "close 12:00"
            _report(f"{atr_note}, {hold_note}", trades, counts, recent=args.last_year)
            if atr_mult == 1.0 and not hold:
                print(f"    {counts}")

    best = replace(base, atr_mult=0.0)
    trades, _ = run(best)
    print("\n  per year (no ATR filter, limit entry, SL 0.786 / TP morning, close 12:00):")
    _by_year(trades)
    if args.out_csv:
        _write(args.out_csv, trades)


def _by_year(trades: list[PullbackTrade]) -> None:
    by_year: dict[int, list[PullbackTrade]] = defaultdict(list)
    for t in trades:
        by_year[t.day.year].append(t)
    for year in sorted(by_year):
        print(f"    {year}  {summarize(by_year[year])}")
    if trades:
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        print(f"    LONG   {summarize(longs)}")
        print(f"    SHORT  {summarize(shorts)}")


def _write(path: str, trades: list[PullbackTrade]) -> None:
    if not trades:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        w.writeheader()
        for t in trades:
            w.writerow(asdict(t))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
