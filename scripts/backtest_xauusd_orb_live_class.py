"""Full trade-outcome backtest of the LIVE XauusdOrbLiquiditySweepStrategy
class (strategy/xauusd_orb_liquidity_sweep.py), bar-by-bar over XAUUSD M5,
mirroring scripts/backtest_first_fvg_15m_live_class.py's / backtest_sr_daily_bias_live_class.py's
pattern for the same kind of check on the other live classes.

Purpose: regression/fidelity check -- this must reproduce
scripts/xauusd_orb_liquidity_sweep_backtest.py's Setup-B-only trade count/PF
closely (n=101, PF 1.62 net of 0.39pt spread, over data/history/XAUUSD.ifx_M1.csv
resampled to M5). A real gap here would mean the live class's state machine
diverges from the validated batch script despite being written to mirror it
bar-by-bar -- exactly the kind of silent drift SrDailyBiasStrategy's own
"KNOWN FIDELITY GAP" note warns about.

Known, accepted fidelity gap (same one documented for FirstFvg15mStrategy's
live-class check): this harness's own exit simulation walks forward through
ALL subsequent bars for SL/TP with no EOD force-close, unlike the batch
script's `simulate_trade`/day loop (which force-closes at the day's last bar
if unresolved). A live broker doesn't force-close at day-end either, so this
harness's own behavior is arguably CLOSER to reality -- but it is a genuine,
if small (n difference typically ~1%), divergence from the exact number the
batch script reports. Not treated as a bug.

Usage:
    python -m scripts.backtest_xauusd_orb_live_class
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from scripts.backtest_common import load_m1, resample
from strategy.xauusd_orb_liquidity_sweep import XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy

NY = ZoneInfo("America/New_York")
INPUT_CSV = "data/history/XAUUSD.ifx_M1.csv"
SPREAD_POINTS = 0.39  # robustness_analysis.SPREAD_BY_SYMBOL["XAUUSD"]


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
    return [
        Bar(timestamp=ts.to_pydatetime(), open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
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


def run() -> list[LiveTrade]:
    m1 = load_m1(INPUT_CSV)
    m5_df = resample(m1, 5)
    m5_df.index = m5_df.index.tz_convert(NY)
    bars = df_to_bars(m5_df)
    print(f"Loaded {len(bars)} M5 bars: {bars[0].timestamp} -> {bars[-1].timestamp}")

    strategy = XauusdOrbLiquiditySweepStrategy(config=XauusdOrbLiquiditySweepConfig())
    market_state = MarketState(symbol="XAUUSD", timeframe=Timeframe.M5)

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
    trades = run()
    summarize(trades)

    out_path = "artifacts/xauusd_orb_live_class_trades.csv"
    Path("artifacts").mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "entry_price", "stop", "target", "exit_reason", "r_multiple_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.entry_price, t.stop, t.target, t.exit_reason, t.r_multiple_net])
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
