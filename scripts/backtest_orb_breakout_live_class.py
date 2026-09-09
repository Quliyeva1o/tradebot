"""Fidelity check: does NasdaqOrbM1BreakoutStrategy agree with its backtest on
timeframes other than M1?

The class docstring claims it "assumes it is fed M1 bars specifically ... feeding
it any other timeframe computes a wrong-sized range". Reading the code that
looks stale: the opening range is accumulated by wall-clock (`local_time <
scan_start`), not by counting bars, so M5 bars over 09:30-10:30 produce the same
high/low as M1 bars over the same window, and the breakout test reads
`latest_bar.close` -- an M5 close if M5 bars are what it is fed.

Stale or not, that is a claim about a class placing real orders, so this
measures it instead of arguing from the source: drive the live class bar by bar
over resampled data and compare the setups it proposes against the backtest's
trades for the same config.

Entry prices are expected to differ slightly and are not compared. The backtest
fills at the next bar's open; the live class can only know the breakout bar's
close when the signal fires, and the real fill lands on the following poll --
a documented, deliberate gap (see the class's own module docstring).

THE ONE-POSITION GATE IS PART OF THE COMPARISON. The backtest holds at most one
position at a time and keeps it open ACROSS days until SL or TP is hit (`if
in_position: ... continue` skips every entry check meanwhile); at 4R that can
be many days. run_live_nasdaq_orb.run_once enforces the same thing -- it returns
after _manage_open_trade whenever this strategy already owns a position on the
symbol. An earlier version of this script drove the strategy class with no such
gate and so counted a setup on every breakout day, including the days the
backtest was still holding. It reported "100% agreement" on the strength of
recall alone: NDX100 30m/M5 scored 489/489 matched while the ungated class also
proposed on 717 further days. Both directions are now reported, and the
headline number is the symmetric one (Jaccard), so an inflated live-side count
can no longer read as perfect agreement.

Usage:
    python -m scripts.backtest_orb_breakout_live_class --symbol XAUUSD --or-minutes 60 --scan-minutes 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
from core.models import Bar, Timeframe
from scripts.backtest_common import NY, resample
from scripts.consistency_analysis import _cached_load_m1
from scripts.two_strategy_symbol_sweep import DATA_DIR, recent_spread
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy

orb_mod.load_m1 = _cached_load_m1


class _State:
    """Minimal MarketState stand-in: the strategy reads the latest bar, plus
    symbol/timeframe when it builds a setup_id."""

    def __init__(self, symbol: str, timeframe: Timeframe) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self._bar: Bar | None = None

    def set(self, bar: Bar) -> None:
        self._bar = bar

    def get_latest_bar(self) -> Bar | None:
        return self._bar


def run_live_class(df, or_minutes: int, tp_r: float, symbol: str,
                   timeframe: Timeframe) -> list[tuple[date, str, float, float]]:
    """Drives the live class bar by bar under the runner's one-position gate.

    While a setup's virtual position is open, the bar is only checked against
    that position's SL/TP -- the strategy is not consulted, exactly as
    run_once() skips _evaluate_for_new_trade whenever it already owns a
    position. The position is priced off the setup itself (entry = the
    breakout close the class reports, SL = its stop) rather than the
    backtest's next-bar open, which is the honest live-side price.
    """
    strategy = NasdaqOrbM1BreakoutStrategy(
        config=NasdaqOrbM1BreakoutConfig(or_minutes=or_minutes, tp_r=tp_r, direction="long")
    )
    state = _State(symbol, timeframe)
    out = []
    open_sl = open_tp = None
    for ts, row in df.iterrows():
        high, low = float(row.high), float(row.low)
        if open_sl is not None:
            # LONG-only, matching the swept/deployed direction.
            if low <= open_sl or high >= open_tp:
                open_sl = open_tp = None
            continue

        state.set(Bar(timestamp=ts, open=float(row.open), high=high, low=low,
                      close=float(row.close), volume=float(row.volume), spread=0.0))
        setup = strategy.evaluate(state)
        if setup is not None:
            entry, stop = setup.entry_zone[0], setup.stop_zone[0]
            out.append((ts.astimezone(NY).date(), setup.direction.name, entry, stop))
            open_sl, open_tp = stop, entry + tp_r * abs(entry - stop)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--or-minutes", type=int, default=60)
    ap.add_argument("--scan-minutes", type=int, default=5)
    ap.add_argument("--tp-r", type=float, default=4.0)
    args = ap.parse_args()

    csv = DATA_DIR / f"{args.symbol}_M1.csv"
    sp = recent_spread(csv)

    bt = orb_mod.run_backtest(str(csv), "full", sp, args.tp_r, "long",
                              or_minutes=args.or_minutes, scan_minutes=args.scan_minutes)
    bt_days = {date.fromisoformat(str(t.day)[:10]) for t in bt}

    m1 = _cached_load_m1(str(csv))
    scan_anchor = 9 * 60 + 30 + args.or_minutes
    df = (m1 if args.scan_minutes == 1
          else resample(m1, args.scan_minutes, offset_minutes=scan_anchor % args.scan_minutes))
    df.index = df.index.tz_convert(NY)

    tf = {1: Timeframe.M1, 5: Timeframe.M5, 15: Timeframe.M15}[args.scan_minutes]
    live = run_live_class(df, args.or_minutes, args.tp_r, args.symbol, tf)
    live_days = {d for d, *_ in live}

    only_bt = bt_days - live_days
    only_live = live_days - bt_days
    both = bt_days & live_days

    print(f"{args.symbol}  OR {args.or_minutes}m / skan M{args.scan_minutes} / {args.tp_r:g}R")
    print("-" * 64)
    print(f"  backtest trade gunu : {len(bt_days)}")
    print(f"  canli sinif setup   : {len(live_days)}")
    print(f"  hər ikisinde        : {len(both)}")
    print(f"  yalniz backtest-de  : {len(only_bt)}")
    print(f"  yalniz canli sinifde: {len(only_live)}")
    # Jaccard, not recall: |both| / |union|. Recall alone reads 100% whenever
    # the live side is a superset, which is exactly the failure this script
    # used to hide -- see the module docstring.
    union = max(len(bt_days | live_days), 1)
    print(f"  uygunluq (Jaccard)  : {len(both) / union * 100:.1f}%")
    print(f"  ...bundan tutum     : {len(both) / max(len(bt_days), 1) * 100:.1f}% "
          f"(backtest gunlerinin nece faizini tutur)")
    for label, days in (("yalniz backtest", only_bt), ("yalniz canli", only_live)):
        if days:
            sample = sorted(days)[:8]
            print(f"  {label} ({len(days)}): {', '.join(str(d) for d in sample)}"
                  + (" ..." if len(days) > 8 else ""))


if __name__ == "__main__":
    main()
