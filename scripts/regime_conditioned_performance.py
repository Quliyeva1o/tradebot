"""Tags every trade in the two production trade logs with the market regime
(TRENDING / MEAN_REVERTING / RANGING) and volatility bucket (low/normal/high)
that prevailed in the 200 bars immediately BEFORE that trade's entry, using
research/regime_analysis.py unchanged, then reports PF/WR per regime bucket.

Purpose: SR_DAILY_BIAS_SPREAD_REPORT.md's half-year breakdown shows PF
swinging between 0.47 and 2.22 across periods -- this checks whether that
swing tracks a REGIME (actionable: could gate the strategy to only trade in
its favorable regime) or is just noise uncorrelated with regime (in which
case regime-gating would not help).

The 200-bar trailing window is analyze_regime()'s own default and is
deliberately NOT re-tuned here -- the point is to reuse the existing,
already-written classifier as-is, not to fit a new one.

Usage:
    python -m scripts.regime_conditioned_performance
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Bar, Timeframe
from research.regime_analysis import analyze_regime
from scripts.backtest_common import load_m1, resample

WINDOW_BARS = 200


def df_to_bars(df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(timestamp=ts.to_pydatetime(), open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
        for ts, row in df.iterrows()
    ]


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def load_trades(path: str, entry_col: str, r_col: str) -> list[tuple[datetime, float]]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append((datetime.fromisoformat(row[entry_col]), float(row[r_col])))
    out.sort(key=lambda t: t[0])
    return out


def tag_and_report(label: str, trades: list[tuple[datetime, float]], bars: list[Bar], timeframe: Timeframe, symbol: str) -> None:
    bar_ts = [b.timestamp for b in bars]
    by_regime: dict[str, list[float]] = defaultdict(list)
    by_vol: dict[str, list[float]] = defaultdict(list)
    unmatched = 0

    import bisect
    for entry_time, r in trades:
        idx = bisect.bisect_right(bar_ts, entry_time) - 1
        if idx < WINDOW_BARS:
            unmatched += 1
            continue
        summary = analyze_regime(bars[: idx + 1], symbol=symbol, timeframe=timeframe, window_bars=WINDOW_BARS)
        by_regime[summary.regime.value].append(r)
        by_vol[summary.volatility.bucket].append(r)

    print(f"\n{'=' * 78}\n{label}  (n={len(trades)}, {unmatched} skipped -- not enough leading bars)\n{'=' * 78}")
    print("\n-- By trend regime (lag-1 autocorrelation of the trailing 200 bars) --")
    for regime in ("TRENDING", "MEAN_REVERTING", "RANGING"):
        rs = by_regime.get(regime, [])
        if not rs:
            print(f"  {regime:15s}: n=0")
            continue
        wins = sum(1 for x in rs if x > 0)
        print(f"  {regime:15s}: n={len(rs):4d}  WR={wins/len(rs)*100:5.1f}%  PF={pf_of(rs):.3f}  net_R={sum(rs):+.2f}")

    print("\n-- By volatility bucket (ATR percentile vs trailing 100 readings) --")
    for bucket in ("low", "normal", "high"):
        rs = by_vol.get(bucket, [])
        if not rs:
            print(f"  {bucket:8s}: n=0")
            continue
        wins = sum(1 for x in rs if x > 0)
        print(f"  {bucket:8s}: n={len(rs):4d}  WR={wins/len(rs)*100:5.1f}%  PF={pf_of(rs):.3f}  net_R={sum(rs):+.2f}")


def main() -> None:
    m1 = load_m1("data/history/NAS100_M1.csv")

    m15 = df_to_bars(resample(m1, 15))
    fvg_trades = load_trades("artifacts/first_fvg_15m_2R_spread_0930_all.csv", "entry_time", "r_multiple_net")
    tag_and_report("First FVG (NAS100 09:30/15m/2R)", fvg_trades, m15, Timeframe.M15, "NAS100")

    m30 = df_to_bars(resample(m1, 30))
    sr_trades = load_trades("artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv", "entry_time", "r_multiple_net")
    tag_and_report("SR+Bias (NAS100 30m liquidity-TP)", sr_trades, m30, Timeframe.M30, "NAS100")


if __name__ == "__main__":
    main()
