"""Regression check: replays USTEC_M1.csv bar-by-bar through the LIVE
strategy class (strategy/ny_open_accumulation_breakout.py) exactly as a live
loop would (one evaluate() call per closed bar, MarketState growing one bar
at a time), and compares the resulting trade count/direction/entry against
scripts/accumulation_breakout_backtest.py's already-validated batch output --
NOT to get identical numbers (the live class intentionally doesn't reproduce
every minor batch-processing convenience of the backtest, e.g. its exact
same-bar-instant-stop pre-check), but to confirm the core signal generation
(which days fire, in which direction, at roughly which entry price) lines up
closely enough to trust the port.
"""
from __future__ import annotations

import csv
from datetime import datetime
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.ny_open_accumulation_breakout import (
    DailyContext,
    NyOpenAccumulationBreakoutConfig,
    NyOpenAccumulationBreakoutStrategy,
    compute_daily_context,
)

BROKER_TZ = ZoneInfo("Europe/Bucharest")
UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


def load_bars(path: str) -> list[Bar]:
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            true_utc = broker_local.astimezone(UTC)
            bars.append(Bar(
                timestamp=true_utc, open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=0.0,
            ))
    bars.sort(key=lambda b: b.timestamp)
    return bars


def main() -> None:
    all_bars = load_bars("data/history/USTEC_M1.csv")

    # Only replay the last ~2.5 months live (this is a porting/regression
    # sanity check, not a full re-backtest) -- but compute_daily_context
    # still needs ~25 prior trading days of history for its own lookback, so
    # keep that much extra data before the replay-start point.
    from datetime import timedelta
    replay_start = all_bars[-1].timestamp - timedelta(days=75)
    context_lookback_start = all_bars[-1].timestamp - timedelta(days=120)
    context_bars_pool = [b for b in all_bars if b.timestamp >= context_lookback_start]
    replay_bars = [b for b in all_bars if b.timestamp >= replay_start]

    market_state = MarketState(symbol="USTEC", timeframe=Timeframe.M1)
    strategy = NyOpenAccumulationBreakoutStrategy(
        config=NyOpenAccumulationBreakoutConfig(max_rr_cap=3.0)
    )

    setups = []
    current_context_date = None
    for bar in replay_bars:
        market_state.append_bar(bar)
        local_date = bar.timestamp.astimezone(NY).date()
        if local_date != current_context_date:
            bounded_history = [b for b in context_bars_pool if b.timestamp <= bar.timestamp]
            ctx = compute_daily_context(bars=bounded_history, for_date=local_date)
            if ctx is not None:
                strategy.set_daily_context(ctx)
            current_context_date = local_date

        setup = strategy.evaluate(market_state)
        if setup is not None:
            setups.append(setup)

    print(f"Live-replay setups found: {len(setups)}")
    for s in setups[:15]:
        print(f"  {s.timestamp.astimezone(NY)}  {s.direction.name}  entry={s.entry_zone[0]:.2f}  "
              f"sl={s.stop_zone[0]:.2f}  tp={s.target_zone[0]:.2f}")
    print(strategy.diagnostics.summary())


if __name__ == "__main__":
    main()
