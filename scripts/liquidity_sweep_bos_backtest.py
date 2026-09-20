"""Liquidity-sweep + BOS backtest: the trades one live bot would take.

Per NY day: strategy.liquidity_sweep_bos.DayState is fed the session's M1 bars together
with the confirmed 5M/1M swing and FVG values for each bar, and returns at most one entry.
Entry is a market fill at the 1M BOS bar's close, so the exit search starts on the NEXT
bar -- the entry bar is already over when the order goes in.

Everything the day reads is stamped by when it became knowable:
  * a swing is a non-repainting pivot, written at its confirmation bar (the same rule as
    scripts.backtest_common.compute_pivots, which `_pivots` reproduces vectorised because
    that function's Python loop over 6M M1 bars is far too slow here -- they are asserted
    equal in the tests);
  * a 5M BOS or FVG is attached to the M1 bar whose close coincides with the 5M bar's;
  * the 4H/1H levels are built on the NY local calendar and stamped one minute after each
    candle's last bar, so the 09:30 markup can only read a candle that had already closed
    (see `Session._htf_levels` for why a plain resample is wrong here).

R is the entry-to-stop distance, and spread is charged once per trade in those units.

Usage:
    python -m scripts.liquidity_sweep_bos_backtest --m1-csv data/history/fundingpips/NDX100_M1.csv \
        --symbol NDX100 --sweep
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import SignalDirection  # noqa: E402
from execution.level_fill import exit_fill  # noqa: E402
from scripts.backtest_common import load_m1, resample  # noqa: E402
from scripts.first_fvg_window_backtest import FrameBars  # noqa: E402
from scripts.two_strategy_symbol_sweep import recent_spread  # noqa: E402
from strategy.liquidity_sweep_bos import (  # noqa: E402
    DayState,
    Levels,
    LiquiditySweepBosConfig,
    RetraceMode,
    StopMode,
    SweepEntry,
    TargetMode,
    setup_id,
)

NY = ZoneInfo("America/New_York")
TRADING_BREAK = timedelta(minutes=30)  # same threshold PaperBroker uses for a stale session open
NS = 1_000_000_000


@dataclass(frozen=True)
class SweepTrade:
    day: date
    setup_id: str
    direction: str
    level_name: str
    level_price: float
    sweep_extreme: float
    leg_high: float
    target_name: str
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
    no_levels: int = 0
    no_setup: int = 0
    busy: int = 0
    still_open: int = 0

    def plus(self, **kw: int) -> Counts:
        return replace(self, **{k: getattr(self, k) + v for k, v in kw.items()})


def _ffill(values: np.ndarray) -> np.ndarray:
    """Carries each non-NaN value forward, leaving NaN before the first one."""
    idx = np.where(~np.isnan(values), np.arange(len(values)), 0)
    np.maximum.accumulate(idx, out=idx)
    return values[idx]


def _pivots(highs: np.ndarray, lows: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """`backtest_common.compute_pivots`, vectorised: the value is written at `i + k`.

    A pivot high at bar i means highs[i] is the maximum of the 2k+1 bars centred on it,
    and it is first knowable k bars later -- which is where the value is written, so
    reading position j never uses information from after j.
    """
    n = len(highs)
    ph, pl = np.full(n, np.nan), np.full(n, np.nan)
    if n < 2 * k + 1:
        return ph, pl
    window = np.lib.stride_tricks.sliding_window_view
    centre = slice(k, n - k)
    ph[2 * k:] = np.where(highs[centre] == window(highs, 2 * k + 1).max(axis=1),
                          highs[centre], np.nan)
    pl[2 * k:] = np.where(lows[centre] == window(lows, 2 * k + 1).min(axis=1),
                          lows[centre], np.nan)
    return ph, pl


class Context:
    """Everything a day needs, precomputed once for a given pivot strength."""

    def __init__(self, session: Session, strength: int) -> None:
        m1, m5 = session.frame, session.m5
        ph, pl = _pivots(m1["high"].to_numpy(float), m1["low"].to_numpy(float), strength)
        self.m1_swing_high = _ffill(ph)
        self.m1_swing_low = _ffill(pl)

        m5_high = m5["high"].to_numpy(float)
        m5_low = m5["low"].to_numpy(float)
        m5_close = m5["close"].to_numpy(float)
        m5_ph, m5_pl = _pivots(m5_high, m5_low, strength)
        last_ph, last_pl = _ffill(m5_ph), _ffill(m5_pl)

        n5 = len(m5_close)
        bos_long = np.where(m5_close > last_ph, m5_high, np.nan)
        bos_short = np.where(m5_close < last_pl, m5_low, np.nan)

        # A bullish 5M gap: this bar's low sits above the high two bars back. Price
        # re-enters it from above at that low, so the low is the edge that counts.
        fvg_long = np.full(n5, np.nan)
        fvg_short = np.full(n5, np.nan)
        if n5 > 2:
            fvg_long[2:] = np.where(m5_low[2:] > m5_high[:-2], m5_low[2:], np.nan)
            fvg_short[2:] = np.where(m5_high[2:] < m5_low[:-2], m5_high[2:], np.nan)

        # Scatter the 5M values onto the M1 bar whose close coincides with the 5M bar's.
        m1_n = len(session.minutes)
        slots = np.searchsorted(session.ns, m5.index.as_unit("ns").asi8 + 4 * 60 * NS)
        valid = (slots < m1_n) & (session.ns[np.minimum(slots, m1_n - 1)]
                                  == m5.index.as_unit("ns").asi8 + 4 * 60 * NS)
        rows, cols = slots[valid], np.flatnonzero(valid)
        self.bos5_long = np.full(m1_n, np.nan)
        self.bos5_short = np.full(m1_n, np.nan)
        self.bos5_long[rows] = bos_long[cols]
        self.bos5_short[rows] = bos_short[cols]
        scattered_long = np.full(m1_n, np.nan)
        scattered_short = np.full(m1_n, np.nan)
        scattered_long[rows] = fvg_long[cols]
        scattered_short[rows] = fvg_short[cols]
        self.fvg_long = _ffill(scattered_long)
        self.fvg_short = _ffill(scattered_short)


class Session:
    """One symbol's M1 history, parsed once and reusable across configurations."""

    @classmethod
    def from_csv(cls, path: str, since: str | None = None) -> Session:
        return cls(load_m1(path, since))

    def __init__(self, frame) -> None:  # a load_m1() DataFrame
        self.frame = frame
        self.m1 = FrameBars(frame)
        index = frame.index
        self.ns = index.as_unit("ns").asi8
        self.minutes = (index.hour * 60 + index.minute).to_numpy()
        self.day_key = index.normalize().asi8
        self.m5 = resample(frame, 5)
        self._contexts: dict[int, Context] = {}
        self._levels: dict[date, Levels] | None = None

    def context(self, strength: int) -> Context:
        if strength not in self._contexts:
            self._contexts[strength] = Context(self, strength)
        return self._contexts[strength]

    def _htf_levels(self, hours: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(known-at ns, high, low) of each closed `hours`-hour candle, on the NY grid.

        Built from the local calendar rather than `resample`. A fixed-frequency resample
        steps in ABSOLUTE time from an origin, so on a tz-aware index a 4-hour grid slips
        by an hour at every DST change and stops matching the 00:00/04:00/08:00 blocks a
        chart draws -- measured on this data it produces both 00:00/04:00/08:00 and
        03:00/07:00/11:00 boundaries within one series, and which one you get depends on
        where the file happens to start. A 1-hour grid is immune because the shift is a
        whole hour, which is why this only ever showed up in the 4H levels.

        Each candle is stamped one minute after its last bar: the moment it has closed
        and its high and low are knowable, and never a moment earlier.
        """
        index = self.frame.index
        # `day_key` is the ns of local midnight, so days sit 8.64e13 apart and adding the
        # block number (0..5) cannot collide with the next day.
        block = self.day_key + (index.hour.to_numpy() // hours)
        grouped = pd.DataFrame({
            "block": block,
            "high": self.frame["high"].to_numpy(float),
            "low": self.frame["low"].to_numpy(float),
            "ns": self.ns,
        }).groupby("block", sort=True).agg(high=("high", "max"), low=("low", "min"),
                                           last_ns=("ns", "max"))
        return (grouped["last_ns"].to_numpy() + 60 * NS,
                grouped["high"].to_numpy(float), grouped["low"].to_numpy(float))

    def levels_by_day(self) -> dict[date, Levels]:
        """Each NY date's marked liquidity, as of that day's 09:30."""
        if self._levels is not None:
            return self._levels

        frame, minutes, day_key = self.frame, self.minutes, self.day_key
        highs, lows = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
        dates = frame.index.date

        session = (minutes >= 570) & (minutes < 960)  # 09:30-16:00
        prev_high: dict[date, float] = {}
        prev_low: dict[date, float] = {}
        for d, h, low in zip(dates[session], highs[session], lows[session]):
            prev_high[d] = max(prev_high.get(d, -np.inf), h)
            prev_low[d] = min(prev_low.get(d, np.inf), low)
        ordered = sorted(prev_high)
        previous = {d: ordered[i - 1] for i, d in enumerate(ordered) if i > 0}

        # 18:00 belongs to the NEXT date's overnight; anything before 09:30 to its own.
        overnight = (minutes >= 1080) | (minutes < 570)
        on_high: dict[date, float] = {}
        on_low: dict[date, float] = {}
        one_day = timedelta(days=1)
        for d, m, h, low in zip(dates[overnight], minutes[overnight],
                                highs[overnight], lows[overnight]):
            key = d + one_day if m >= 1080 else d
            on_high[key] = max(on_high.get(key, -np.inf), h)
            on_low[key] = min(on_low.get(key, np.inf), low)

        h4_ns, h4_high, h4_low = self._htf_levels(4)
        h1_ns, h1_high, h1_low = self._htf_levels(1)
        # One 09:30 bar per date, found by mask rather than by walking all six million.
        opens = {dates[i]: self.ns[i] for i in np.flatnonzero(minutes == 570)}

        out: dict[date, Levels] = {}
        for d in ordered:
            prev = previous.get(d)
            open_ns = opens.get(d)
            if prev is None or open_ns is None or d not in on_high:
                continue
            h4 = int(np.searchsorted(h4_ns, open_ns, side="right")) - 1
            h1 = int(np.searchsorted(h1_ns, open_ns, side="right")) - 1
            if h4 < 0 or h1 < 0 or not np.isfinite(h4_high[h4]) or not np.isfinite(h1_high[h1]):
                continue
            out[d] = Levels(
                h4_high=float(h4_high[h4]), h4_low=float(h4_low[h4]),
                h1_high=float(h1_high[h1]), h1_low=float(h1_low[h1]),
                psh=prev_high[prev], psl=prev_low[prev],
                onh=on_high[d], onl=on_low[d],
            )
        self._levels = out
        return out

    def day_windows(self, cfg: LiquiditySweepBosConfig) -> dict[date, np.ndarray]:
        """Each NY date's bar positions inside the session window."""
        first = cfg.session_start.hour * 60 + cfg.session_start.minute
        last = cfg.session_end.hour * 60 + cfg.session_end.minute
        pos = np.flatnonzero((self.minutes >= first) & (self.minutes < last))
        if len(pos) == 0:
            return {}
        keys = self.day_key[pos]
        groups = np.split(pos, np.flatnonzero(keys[1:] != keys[:-1]) + 1)
        dates = self.frame.index.date
        return {dates[g[0]]: g for g in groups}


def run_backtest(
    session: Session,
    cfg: LiquiditySweepBosConfig,
    symbol: str,
    spread: float,
    *,
    long: bool = True,
    allow_overlap: bool = False,
) -> tuple[list[SweepTrade], Counts]:
    ctx = session.context(cfg.pivot_strength)
    m1, minutes, day_key = session.m1, session.minutes, session.day_key
    levels_by_day = session.levels_by_day()
    bos5 = ctx.bos5_long if long else ctx.bos5_short
    fvg = ctx.fvg_long if long else ctx.fvg_short
    swing = ctx.m1_swing_high if long else ctx.m1_swing_low
    close_minute = None if cfg.hold_past_session else (
        cfg.session_end.hour * 60 + cfg.session_end.minute
    )

    trades: list[SweepTrade] = []
    counts = Counts()
    busy_until: datetime | None = None

    for day, window in session.day_windows(cfg).items():
        counts = counts.plus(days=1)
        levels = levels_by_day.get(day)
        if levels is None:
            counts = counts.plus(no_levels=1)
            continue

        state = DayState(levels, cfg, long=long)
        found: tuple[int, SweepEntry] | None = None
        for i in window:
            entry = state.on_bar(
                m1[i],
                bos5_leg_high=None if np.isnan(bos5[i]) else float(bos5[i]),
                m1_swing=None if np.isnan(swing[i]) else float(swing[i]),
                fvg_edge=None if np.isnan(fvg[i]) else float(fvg[i]),
            )
            if entry is not None:
                found = (i, entry)
                break
        if found is None:
            counts = counts.plus(no_setup=1)
            continue

        entry_i, entry = found
        if not allow_overlap and busy_until is not None and busy_until > entry.time:
            counts = counts.plus(busy=1)
            continue

        exit_hit = _find_exit(m1, minutes, day_key, entry_i, entry, close_minute)
        if exit_hit is None:
            counts = counts.plus(still_open=1)
            busy_until = datetime.max.replace(tzinfo=UTC)
            continue

        exit_i, exit_time, exit_price, reason = exit_hit
        sign = 1 if long else -1
        r_gross = (exit_price - entry.price) * sign / entry.risk
        trades.append(SweepTrade(
            day=day, setup_id=setup_id(symbol, entry), direction="LONG" if long else "SHORT",
            level_name=entry.level_name, level_price=entry.level_price,
            sweep_extreme=entry.sweep_extreme, leg_high=entry.leg_high,
            target_name=entry.target_name, entry_time=entry.time, entry=entry.price,
            stop=entry.stop, target=entry.target, exit_time=exit_time, exit_price=exit_price,
            reason=reason, bars_held=exit_i - entry_i,
            r_gross=r_gross, r_net=r_gross - spread / entry.risk,
        ))
        busy_until = exit_time
    return trades, counts


def _find_exit(
    m1: FrameBars,
    minutes: np.ndarray,
    day_key: np.ndarray,
    entry_i: int,
    entry: SweepEntry,
    close_minute: int | None,
) -> tuple[int, datetime, float, str] | None:
    """(index, time, price, reason) of the bar that closes the trade, or None.

    The search starts one bar AFTER the entry: the fill was that bar's close, so nothing
    else could have happened on it. The session close is taken at the open of the first
    bar at or after it, which is the price on the clock.
    """
    entry_day = day_key[entry_i]
    for j in range(entry_i + 1, len(m1)):
        bar = m1[j]
        if close_minute is not None and (
            day_key[j] > entry_day or minutes[j] >= close_minute
        ):
            return j, bar.timestamp, bar.open, "TIME"
        after_break = bar.timestamp - m1[j - 1].timestamp > TRADING_BREAK
        hit = exit_fill(entry.direction, entry.stop, entry.target, bar,
                        entry_bar=False, after_break=after_break)
        if hit is not None:
            return j, bar.timestamp, *hit
    return None


def summarize(trades: list[SweepTrade], field: str = "r_net") -> str:
    if not trades:
        return f"{'n=0':<52}"
    rs = [getattr(t, field) for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    pf = sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")
    wr = sum(1 for r in rs if r > 0) / len(rs) * 100
    return (f"n={len(rs):<5} WR={wr:5.1f}%  PF={pf:6.3f}  netR={sum(rs):+8.1f}"
            f"  avgR={sum(rs) / len(rs):+.3f}")


def _profit_factor(trades: list[SweepTrade], field: str) -> float:
    rs = [getattr(t, field) for t in trades]
    loss = -sum(r for r in rs if r <= 0)
    return sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")


def _recent(trades: list[SweepTrade], days: int = 365) -> list[SweepTrade]:
    if not trades:
        return []
    cutoff = trades[-1].day - timedelta(days=days)
    return [t for t in trades if t.day > cutoff]


def _report(label: str, trades: list[SweepTrade], counts: Counts, *, recent: bool = False) -> None:
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.reason] += 1
    tail = "  ".join(f"{k}={reasons[k]}" for k in ("TP", "SL", "TIME") if reasons[k])
    if not trades:
        print(f"  {label:<38}n=0    (setups: {counts.days - counts.no_setup - counts.no_levels})")
        return
    cost = sum(t.r_gross - t.r_net for t in trades) / len(trades)
    print(f"  {label:<38}{summarize(trades)}  PFgross={_profit_factor(trades, 'r_gross'):6.3f}"
          f"  spread={cost:.3f}R   {tail}")
    if recent:
        window = _recent(trades)
        gross = f"{_profit_factor(window, 'r_gross'):6.3f}" if window else "     -"
        print(f"  {'   last 365d':<38}{summarize(window)}  PFgross={gross}")


def _by_year(trades: list[SweepTrade]) -> None:
    """Calendar-year rows. A year is a small sample for this rule -- 20 to 80 trades --
    so the count is printed beside every figure rather than left to be assumed."""
    grouped: dict[int, list[SweepTrade]] = defaultdict(list)
    for t in trades:
        grouped[t.day.year].append(t)
    print(f"    {'year':<6}{'n':>5}{'WR':>8}{'PF net':>9}{'PF gross':>10}{'netR':>9}{'avgR':>9}")
    for year in sorted(grouped):
        rows = grouped[year]
        rs = [t.r_net for t in rows]
        loss = -sum(r for r in rs if r <= 0)
        pf = sum(r for r in rs if r > 0) / loss if loss > 0 else float("inf")
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        print(f"    {year:<6}{len(rows):>5}{wr:>7.1f}%{pf:>9.3f}"
              f"{_profit_factor(rows, 'r_gross'):>10.3f}{sum(rs):>+9.1f}{sum(rs) / len(rs):>+9.3f}")


RETRACE_VARIANTS = [("EQ  ", RetraceMode.EQUILIBRIUM), ("FVG ", RetraceMode.FVG),
                    ("both", RetraceMode.EITHER)]
EXIT_VARIANTS = [
    ("SL sweep / TP liq", {"stop_mode": StopMode.SWEEP_EXTREME,
                           "target_mode": TargetMode.NEXT_LIQUIDITY}),
    ("SL sweep / TP 2R ", {"stop_mode": StopMode.SWEEP_EXTREME,
                           "target_mode": TargetMode.FIXED_R}),
    ("SL pullbk/ TP liq", {"stop_mode": StopMode.PULLBACK_EXTREME,
                           "target_mode": TargetMode.NEXT_LIQUIDITY}),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--m1-csv", required=True)
    ap.add_argument("--symbol", default="NDX100")
    ap.add_argument("--sweep", action="store_true", help="Run the retracement x exit grid")
    ap.add_argument("--pivot-strength", type=int, default=2)
    ap.add_argument("--retrace", choices=[m.value for m in RetraceMode],
                    default=RetraceMode.EQUILIBRIUM.value)
    ap.add_argument("--stop", choices=[m.value for m in StopMode],
                    default=StopMode.SWEEP_EXTREME.value)
    ap.add_argument("--target", choices=[m.value for m in TargetMode],
                    default=TargetMode.NEXT_LIQUIDITY.value)
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--pivot-sweep", help="Comma-separated pivot strengths to compare")
    ap.add_argument("--short", action="store_true", help="Run the mirrored SHORT setup")
    ap.add_argument("--both-sides", action="store_true", help="Report LONG and SHORT separately")
    ap.add_argument("--hold-past-session", action="store_true")
    ap.add_argument("--last-year", action="store_true")
    ap.add_argument("--by-year", action="store_true", help="Add a calendar-year breakdown")
    ap.add_argument("--allow-overlap", action="store_true")
    ap.add_argument("--since", help="Skip CSV rows before this broker-local date (YYYY-MM-DD)")
    ap.add_argument("--spread", type=float)
    ap.add_argument("--out-csv")
    args = ap.parse_args()

    base = LiquiditySweepBosConfig(
        pivot_strength=args.pivot_strength, hold_past_session=args.hold_past_session,
        retrace_mode=RetraceMode(args.retrace), stop_mode=StopMode(args.stop),
        target_mode=TargetMode(args.target), target_r=args.target_r,
    )
    spread = args.spread if args.spread is not None else recent_spread(Path(args.m1_csv))
    session = Session.from_csv(args.m1_csv, args.since)
    sides = [True, False] if args.both_sides else [not args.short]

    def run(cfg: LiquiditySweepBosConfig, long: bool):
        return run_backtest(session, cfg, args.symbol, spread, long=long,
                            allow_overlap=args.allow_overlap)

    print(f"\n{args.symbol}  sweep+BOS, pivot strength {base.pivot_strength}, "
          f"session {base.session_start:%H:%M}-{base.session_end:%H:%M}, spread={spread}")
    print(f"  levels marked at 09:30: 4H/1H last closed, PSH/PSL previous NY session, "
          f"ONH/ONL 18:00-09:30")

    if args.pivot_sweep:
        print("\n  pivot strength (EQ retracement, SL sweep / TP liquidity):")
        for strength in (int(x) for x in args.pivot_sweep.split(",")):
            for long in sides:
                cfg = replace(base, pivot_strength=strength)
                trades, counts = run(cfg, long)
                side = "LONG " if long else "SHORT"
                _report(f"k={strength}  {side}", trades, counts, recent=args.last_year)
        return

    if not args.sweep:
        for long in sides:
            trades, counts = run(base, long)
            print(f"  {counts}")
            _report("LONG" if long else "SHORT", trades, counts, recent=args.last_year)
            if args.by_year and trades:
                _by_year(trades)
            if args.out_csv and trades:
                _write(args.out_csv, trades)
        return

    print("\n  retracement x exit grid:")
    for r_label, mode in RETRACE_VARIANTS:
        for e_label, exit_kw in EXIT_VARIANTS:
            for long in sides:
                cfg = replace(base, retrace_mode=mode, **exit_kw)
                trades, counts = run(cfg, long)
                side = "" if len(sides) == 1 else ("  LONG " if long else "  SHORT")
                _report(f"{r_label}  {e_label}{side}", trades, counts, recent=args.last_year)


def _write(path: str, trades: list[SweepTrade]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        w.writeheader()
        for t in trades:
            w.writerow(asdict(t))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
