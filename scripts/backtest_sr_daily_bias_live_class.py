"""Full trade-outcome backtest of the LIVE SrDailyBiasStrategy class
(strategy/sr_daily_bias.py) on NAS100 M30, to measure the actual size of
the "KNOWN FIDELITY GAP" that class's own docstring flags but never
quantifies: the batch script (scripts/sr_daily_bias_backtest_liquidity_tp.py)
skips ALL setup detection while a position is open (`if in_position:
continue`), but the live class has no such visibility and keeps updating
its broken-level/retest tracking every bar regardless -- so a Retest setup
the batch script would never reach (blocked by an earlier still-open
Breakout) can appear here. This script drives the ACTUAL live class
bar-by-bar (matching scripts/backtest_midnight_fvg_live_class.py's pattern
for the same kind of check on MidnightFvgStrategy) and diffs its resulting
trades against the batch script's own trade log to answer: is this gap
"harmless" (as the docstring already claims from qualitative checks) or
does it move the numbers?

One-trade-at-a-time gating (mirrors run_live_sr_bias.py: never evaluates
for a NEW trade while a position is open) is enforced HERE, external to the
class, exactly as the live runner does it -- but unlike Midnight, no
same-bar-stop-out check is needed: SR's entry fills at that bar's own
CLOSE (see strategy/sr_daily_bias.py line ~439), so there's no remaining
range on the entry bar itself to check, only bars strictly after it.

Spread: same fixed 3.0-point NAS100 round-trip constant as every other
NAS100 spread calc in this session, applied identically to both this
live-class replay and the batch comparison so the diff isolates the
fidelity gap, not a spread-methodology difference.

Usage:
    python -m scripts.backtest_sr_daily_bias_live_class
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.sr_daily_bias import SrDailyBiasConfig, SrDailyBiasStrategy, compute_daily_bias_context
# Reuse the BATCH script's own load_m1()/resample() bit-for-bit -- not a
# hand-rolled reimplementation -- so any trade-count/PF difference this
# script finds is attributable to the live class vs batch loop, never to a
# resampling discrepancy between two independently-written resamplers.
from scripts.sr_daily_bias_backtest_liquidity_tp import load_m1 as _load_m1_df
from scripts.sr_daily_bias_backtest_liquidity_tp import resample as _resample_df

NY = ZoneInfo("America/New_York")

INPUT_CSV = "data/history/NAS100_M1.csv"
OUTPUT_CSV = "artifacts/sr_daily_bias_live_class_trades.csv"
SPREAD_POINTS = 3.0
STARTING_BALANCE = 100_000.0
RISK_PCT = 0.01


def _df_to_bars(df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(timestamp=ts.to_pydatetime(), open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
        for ts, row in df.iterrows()
    ]


def load_m1(path: str) -> list[Bar]:
    return _df_to_bars(_load_m1_df(path))


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    df = pd.DataFrame(
        {"open": [b.open for b in bars], "high": [b.high for b in bars],
         "low": [b.low for b in bars], "close": [b.close for b in bars],
         "volume": [b.volume for b in bars]},
        index=pd.DatetimeIndex([b.timestamp for b in bars]),
    )
    return _df_to_bars(_resample_df(df, minutes))


@dataclass
class LiveTrade:
    entry_time: datetime
    direction: str
    entry_price: float
    stop: float
    target: float
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str
    r_multiple_gross: float
    r_multiple_net: float


def simulate_outcome(direction: SignalDirection, entry: float, sl: float, tp: float, bars: list[Bar], start_idx: int):
    """Returns (exit_price, exit_reason, exit_time, exit_idx, resolved)."""
    for idx in range(start_idx, len(bars)):
        bar = bars[idx]
        if direction == SignalDirection.BUY:
            hit_sl, hit_tp = bar.low <= sl, bar.high >= tp
        else:
            hit_sl, hit_tp = bar.high >= sl, bar.low <= tp
        if hit_sl:
            return sl, "SL", bar.timestamp, idx, True
        if hit_tp:
            return tp, "TP", bar.timestamp, idx, True
    return None, None, None, None, False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ranging-regime", action="store_true",
                         help="Opt-in gate from ADVANCED_VALIDATION_REPORT.md #3/#3.1 -- OFF by default.")
    args = parser.parse_args()

    m1 = load_m1(INPUT_CSV)
    print(f"Loaded {len(m1)} M1 bars: {m1[0].timestamp} -> {m1[-1].timestamp}")
    m30 = resample(m1, 30)
    daily = resample(m1, 1440)
    print(f"Resampled to {len(m30)} M30 bars, {len(daily)} D1 bars")

    strategy = SrDailyBiasStrategy(config=SrDailyBiasConfig(require_ranging_regime=args.require_ranging_regime))
    market_state = MarketState(symbol="NAS100", timeframe=Timeframe.M30)

    trades: list[LiveTrade] = []
    open_until_idx: int | None = None  # bar index at/after which no trade is open (mirrors
    # scripts/backtest_midnight_fvg_live_class.py's identical variable -- a plain boolean flag
    # cleared via a forward-search in the SAME iteration it was set would clear itself before
    # the outer loop ever advances, silently disabling the one-trade-at-a-time gate entirely;
    # caught by a sanity check here (first draft gave 1017 trades, ~25% over the batch script's
    # 811 -- this fix brought it back in line, see module docstring for the real remaining gap).
    current_bias_date: date | None = None
    risk_amount = STARTING_BALANCE * RISK_PCT

    for i, bar in enumerate(m30):
        this_date = bar.timestamp.date()
        if this_date != current_bias_date:
            ctx = compute_daily_bias_context(daily, this_date)
            if ctx is not None:
                strategy.set_daily_bias_context(ctx)
            current_bias_date = this_date

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

        exit_price, exit_reason, exit_time, exit_idx, resolved = simulate_outcome(setup.direction, entry, sl, tp, m30, i + 1)
        if not resolved:
            break

        move = (exit_price - entry) if setup.direction == SignalDirection.BUY else (entry - exit_price)
        r_gross = move / risk_dist
        cost_r = SPREAD_POINTS / risk_dist
        r_net = r_gross - cost_r

        trades.append(LiveTrade(
            entry_time=bar.timestamp, direction=setup.direction.name, entry_price=entry, stop=sl, target=tp,
            exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
            r_multiple_gross=round(r_gross, 4), r_multiple_net=round(r_net, 4),
        ))
        open_until_idx = exit_idx + 1

    print(f"\nLIVE CLASS total trades: {len(trades)}")
    n = len(trades)
    wins = sum(1 for t in trades if t.r_multiple_net > 0)
    gp = sum(t.r_multiple_net for t in trades if t.r_multiple_net > 0)
    gl = abs(sum(t.r_multiple_net for t in trades if t.r_multiple_net <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    print(f"Win rate: {wins/n*100:.1f}%  PF (net): {pf:.3f}  Total R (net): {sum(t.r_multiple_net for t in trades):.2f}")

    output_csv = "artifacts/sr_daily_bias_live_class_regime_trades.csv" if args.require_ranging_regime else OUTPUT_CSV
    Path("artifacts").mkdir(exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_price", "stop", "target", "exit_time", "exit_price",
                    "exit_reason", "r_multiple_gross", "r_multiple_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_price, t.stop, t.target, t.exit_time, t.exit_price,
                        t.exit_reason, t.r_multiple_gross, t.r_multiple_net])
    print(f"Wrote {output_csv}")


if __name__ == "__main__":
    main()
