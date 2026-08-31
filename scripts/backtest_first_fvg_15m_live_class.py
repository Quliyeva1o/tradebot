"""Full trade-outcome backtest of the LIVE FirstFvg15mStrategy class
(strategy/first_fvg_15m.py), bar-by-bar over NAS100 M15, mirroring
scripts/backtest_sr_daily_bias_live_class.py's / backtest_midnight_fvg_live_class.py's
pattern for the same kind of check on the other live classes.

Two purposes:
1. Regression check: with require_ranging_regime=False (the class's
   existing, already-validated default), this must reproduce
   scripts/first_fvg_15m_spread_backtest.py's 09:30/15m/2R trade count/PF
   closely (the two were already verified byte-for-byte identical earlier
   in this session; this script makes that check reproducible/scriptable
   rather than ad-hoc).
2. New: with require_ranging_regime=True, measures what the opt-in regime
   gate (ADVANCED_VALIDATION_REPORT.md #3/#3.1) actually does when driven
   through the REAL live class -- not just the offline trade-tagging
   analysis -- before anyone considers enabling it for real order routing.

Usage:
    python -m scripts.backtest_first_fvg_15m_live_class
    python -m scripts.backtest_first_fvg_15m_live_class --require-ranging-regime
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.first_fvg_15m import FirstFvg15mConfig, FirstFvg15mStrategy
from scripts.first_fvg_15m_spread_backtest import (
    SPREAD_POINTS,
    load_m1_with_spread,
    resample_tf,
)

NY = ZoneInfo("America/New_York")
INPUT_CSV = "data/history/NAS100_M1.csv"


@dataclass
class LiveTrade:
    entry_time: datetime
    direction: str
    entry_price: float
    stop: float
    target: float
    exit_reason: str
    r_multiple_net: float


def df_to_bars(df) -> list[Bar]:
    # resample_tf() (scripts.first_fvg_15m_spread_backtest) only carries
    # open/high/low/close/spread -- no volume column -- and the strategy
    # never reads Bar.volume, so 0.0 is a safe placeholder, not a real gap.
    return [
        Bar(timestamp=ts.to_pydatetime(), open=row.open, high=row.high, low=row.low, close=row.close, volume=0.0)
        for ts, row in df.iterrows()
    ]


def simulate_outcome(direction: SignalDirection, entry: float, sl: float, tp: float, bars: list[Bar], start_idx: int):
    for idx in range(start_idx, len(bars)):
        bar = bars[idx]
        if direction == SignalDirection.BUY:
            hit_sl, hit_tp = bar.low <= sl, bar.high >= tp
        else:
            hit_sl, hit_tp = bar.high >= sl, bar.low <= tp
        if hit_sl:
            return sl, "SL", idx, True
        if hit_tp:
            return tp, "TP", idx, True
    return None, None, None, False


def run(require_ranging_regime: bool) -> list[LiveTrade]:
    m1 = load_m1_with_spread(INPUT_CSV)
    m15_df = resample_tf(m1, 15)
    bars = df_to_bars(m15_df)
    print(f"Loaded {len(bars)} M15 bars: {bars[0].timestamp} -> {bars[-1].timestamp}")

    strategy = FirstFvg15mStrategy(config=FirstFvg15mConfig(require_ranging_regime=require_ranging_regime))
    market_state = MarketState(symbol="NAS100", timeframe=Timeframe.M15)

    trades: list[LiveTrade] = []
    open_until_idx: int | None = None

    for i, bar in enumerate(bars):
        market_state.append_bar(bar)
        setup = strategy.evaluate(market_state)
        if setup is None:
            continue
        if open_until_idx is not None and i < open_until_idx:
            continue

        entry = setup.entry_zone[0]
        sl = setup.stop_zone[0]
        tp = setup.target_zone[0]
        risk_dist = abs(entry - sl)
        if risk_dist <= 0:
            continue

        exit_price, exit_reason, exit_idx, resolved = simulate_outcome(setup.direction, entry, sl, tp, bars, i + 1)
        if not resolved:
            break

        move = (exit_price - entry) if setup.direction == SignalDirection.BUY else (entry - exit_price)
        r_gross = move / risk_dist
        cost_r = SPREAD_POINTS / risk_dist
        r_net = r_gross - cost_r

        trades.append(LiveTrade(
            entry_time=bar.timestamp, direction=setup.direction.name, entry_price=entry, stop=sl, target=tp,
            exit_reason=exit_reason, r_multiple_net=round(r_net, 4),
        ))
        open_until_idx = exit_idx + 1

    return trades


def summarize(trades: list[LiveTrade]) -> None:
    n = len(trades)
    print(f"\nLIVE CLASS total trades: {n}")
    if n == 0:
        return
    wins = sum(1 for t in trades if t.r_multiple_net > 0)
    gp = sum(t.r_multiple_net for t in trades if t.r_multiple_net > 0)
    gl = abs(sum(t.r_multiple_net for t in trades if t.r_multiple_net <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    print(f"Win rate: {wins/n*100:.1f}%  PF (net): {pf:.3f}  Total R (net): {sum(t.r_multiple_net for t in trades):.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ranging-regime", action="store_true")
    parser.add_argument("--output-csv", default=None)
    args = parser.parse_args()

    trades = run(args.require_ranging_regime)
    summarize(trades)

    out_path = args.output_csv or (
        "artifacts/first_fvg_15m_live_class_regime_trades.csv" if args.require_ranging_regime
        else "artifacts/first_fvg_15m_live_class_trades.csv"
    )
    Path("artifacts").mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_price", "stop", "target", "exit_reason", "r_multiple_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_price, t.stop, t.target, t.exit_reason, t.r_multiple_net])
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
