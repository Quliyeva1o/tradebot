"""The gold AMD bot's own backtest: strategy/gold_amd.find_entry() walked day by day over one broker's M1 history.

One code path with the live bot (find_entry), so this is what the bot would have done, not a look-alike. Each
trade enters at the bot's market price, exits at the first of: stop, `tp_r` x the risk, or 15:55 New York. A bar
that could hit both takes the stop. A short is bought back at the ask (bar low/high + the bar's spread).

Feeds the weekly report's baseline and the stop rule in deploy/kill_rules.json
(python -m scripts.amd_backtest prints the bootstrap envelope that rule was fixed from).

Usage:
    python -m scripts.amd_backtest [--broker cfi]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from backtest.live_replay.brokers import BROKERS, history_path  # noqa: E402
from backtest.live_replay.market import load_bars, trim_to_real_m1  # noqa: E402
from backtest.live_replay.specs import load_specs  # noqa: E402
from strategy.gold_amd import GoldAmdConfig, build_series, find_entry, session_times  # noqa: E402

REPO = Path(__file__).parent.parent
# FundingPips' gold history is on the wrong clock before 2025-03-09 (a Bucharest+1h stamp), so no replay earlier.
START = {"cfi": date(2024, 1, 1), "fundingpips": date(2025, 3, 10)}
LAUNCHER_CONFIG = GoldAmdConfig(tp_r=2.0, min_fvg=0.5, spread_floor=0.15)   # run_live_amd_gold_xauusd_*.bat


def backtest_trades(broker_name: str = "cfi", config: GoldAmdConfig = LAUNCHER_CONFIG, symbol: str = "XAUUSD",
                    history: Path | None = None) -> pd.DataFrame:
    """One row per trade: day (New York date), direction (+1/-1), risk (price points), r. Empty without history."""
    broker = BROKERS[broker_name]
    spec = load_specs(REPO / broker.specs_file)[symbol]
    path = history or REPO / history_path(broker, spec)
    if not path.exists():
        return pd.DataFrame(columns=["day", "direction", "risk", "r"])
    m1 = trim_to_real_m1(load_bars(path, symbol, 1))
    ts, o, h, l, c, sp = m1.ts, m1.open, m1.high, m1.low, m1.close, m1.spread
    series = build_series(ts, o, h, l, c, sp, config)
    day = max(START[broker_name], pd.Timestamp(ts[0], unit="s").date())
    last = pd.Timestamp(ts[-1], unit="s").date()
    rows = []
    while day <= last:
        if day.weekday() < 5:
            times = session_times(day, config)
            entry = find_entry(series, times, config).entry
            if entry is not None:
                risk = abs(entry.entry - entry.stop)
                target = entry.entry + entry.direction * config.tp_r * risk
                end = min(int(np.searchsorted(ts, times.flat)), len(ts))
                r = None
                for k in range(entry.index, end):
                    if entry.direction == 1:
                        if l[k] <= entry.stop:
                            r = -1.0
                        elif h[k] >= target:
                            r = config.tp_r
                    elif h[k] + sp[k] >= entry.stop:
                        r = -1.0
                    elif l[k] + sp[k] <= target:
                        r = config.tp_r
                    if r is not None:
                        break
                if r is None and end > entry.index:
                    px = c[end - 1] if entry.direction == 1 else c[end - 1] + sp[end - 1]
                    r = entry.direction * (px - entry.entry) / risk
                if r is not None:
                    rows.append((day, entry.direction, risk, r))
        day += timedelta(days=1)
    return pd.DataFrame(rows, columns=["day", "direction", "risk", "r"])


def envelope(rs: np.ndarray, *, windows: tuple[int, ...] = (20, 40), checkpoint: int = 20,
             draws: int = 100_000, seed: int = 20261009) -> tuple[dict[int, float], float]:
    """Bootstrap like scripts/fvg_window_envelope.envelope, at this bot's pace (~2 trades a month): p99 max
    drawdown inside the first N trades for each window, and the p05 net R at the checkpoint."""
    horizon = max((*windows, checkpoint))
    sample = np.random.default_rng(seed).choice(rs, size=(draws, horizon), replace=True)
    cumulative = np.concatenate([np.zeros((draws, 1)), sample.cumsum(axis=1)], axis=1)
    below_peak = np.maximum.accumulate(cumulative, axis=1) - cumulative
    return ({n: float(np.percentile(below_peak[:, :n + 1].max(axis=1), 99)) for n in windows},
            float(np.percentile(cumulative[:, checkpoint], 5)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--broker", default="cfi", choices=sorted(BROKERS))
    args = ap.parse_args()
    frame = backtest_trades(args.broker)
    if frame.empty:
        raise SystemExit(f"No {args.broker} gold history on this machine")
    rs = frame["r"].to_numpy()
    loss = -rs[rs <= 0].sum()
    stops, floor = envelope(rs)
    print(f"{args.broker}: {frame['day'].min()} -> {frame['day'].max()}  n={len(rs)}  WR {100 * (rs > 0).mean():.1f}%  "
          f"PF {rs[rs > 0].sum() / loss:.3f}  net {rs.sum():+.1f}R")
    print("  p99 max drawdown " + "  ".join(f"{n}: {v:.1f}R" for n, v in stops.items())
          + f"   p05 net@20: {floor:+.1f}R")


if __name__ == "__main__":
    main()
