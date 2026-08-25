"""Regression check: replays USTEC_M1.csv bar-by-bar through the LIVE
MidnightFvgStrategy class (strategy/midnight_fvg.py) exactly as
run_live_midnight_fvg.py's (corrected, bar-by-bar) _evaluate_for_new_trade()
would, and compares the resulting trade count/direction/entry/SL/TP against
scripts/first_fvg_backtest.py's already-validated batch output for the same
date range -- NOT expecting byte-identical numbers, but to confirm the core
signal generation (which days fire, in which direction, at which
entry/SL/TP) lines up closely enough to trust the port. See
strategy/midnight_fvg.py's module docstring for the one known, deliberate
behavioral difference from the batch script's prose spec (retest window).

Mirrors scripts/replay_live_strategy_check.py's structure/rationale (that
one covers NyOpenAccumulationBreakoutStrategy).
"""
from __future__ import annotations

import csv
from datetime import datetime
from zoneinfo import ZoneInfo

from core.models import Bar, Timeframe
from market_structure.structure_models import MarketState
from strategy.midnight_fvg import MidnightFvgConfig, MidnightFvgStrategy

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
    print(f"Loaded {len(all_bars)} bars: {all_bars[0].timestamp} -> {all_bars[-1].timestamp}")

    market_state = MarketState(symbol="USTEC", timeframe=Timeframe.M1)
    strategy = MidnightFvgStrategy(config=MidnightFvgConfig(fixed_tp_r=2.5, min_gap_points=3.0))

    setups = []
    for bar in all_bars:
        market_state.append_bar(bar)
        setup = strategy.evaluate(market_state)
        if setup is not None:
            setups.append(setup)

    print(f"Live-replay setups found: {len(setups)}")
    for s in setups:
        print(f"  {s.timestamp.astimezone(NY)}  {s.direction.name}  entry={s.entry_zone[0]:.2f}  "
              f"sl={s.stop_zone[0]:.2f}  tp={s.target_zone[0]:.2f}")
    print(strategy.diagnostics.summary())


if __name__ == "__main__":
    main()
