"""Out-of-sample check for the RANGING-only regime filter found in
ADVANCED_VALIDATION_REPORT.md #3: both strategies' entire edge appeared to
come from the RANGING regime when measured over the FULL history at once.
That is a single aggregate measurement -- it says nothing about whether the
effect is a stable, exploitable property of the market or an artifact of
which specific losing trades happened to fall in TRENDING/MEAN_REVERTING
buckets over this one span of history.

This script re-uses the SAME 10 chronological walk-forward folds from
scripts/walk_forward_montecarlo.py (not a fresh single 80/20 split) and
asks the sharper question: does dropping TRENDING/MEAN_REVERTING trades
improve PF in EACH INDEPENDENT fold, or only in the aggregate? A real
regime effect should show up as a consistent per-fold improvement, not just
a favorable sum across all of them (which one or two large folds could
dominate).

Usage:
    python -m scripts.regime_filter_walk_forward
"""

from __future__ import annotations

import bisect
import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Bar, Timeframe
from research.regime_analysis import analyze_regime
from scripts.backtest_common import load_m1, resample

N_FOLDS = 10
WINDOW_BARS = 200


@dataclass
class TaggedTrade:
    ts: datetime
    r: float
    regime: str


def df_to_bars(df) -> list[Bar]:
    return [
        Bar(timestamp=ts.to_pydatetime(), open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
        for ts, row in df.iterrows()
    ]


def load_and_tag(trades_csv: str, bars: list[Bar], timeframe: Timeframe, symbol: str) -> list[TaggedTrade]:
    raw = []
    with open(trades_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw.append((datetime.fromisoformat(row["entry_time"]), float(row["r_multiple_net"])))
    raw.sort(key=lambda t: t[0])

    bar_ts = [b.timestamp for b in bars]
    out = []
    for entry_time, r in raw:
        idx = bisect.bisect_right(bar_ts, entry_time) - 1
        if idx < WINDOW_BARS:
            continue
        summary = analyze_regime(bars[: idx + 1], symbol=symbol, timeframe=timeframe, window_bars=WINDOW_BARS)
        out.append(TaggedTrade(ts=entry_time, r=r, regime=summary.regime.value))
    return out


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def fold_bounds(trades: list[TaggedTrade], n_folds: int) -> list[tuple[datetime, datetime]]:
    start, end = trades[0].ts, trades[-1].ts
    span = end - start
    return [(start + span * (k / n_folds), start + span * ((k + 1) / n_folds)) for k in range(n_folds)]


def report(label: str, trades: list[TaggedTrade]) -> None:
    bounds = fold_bounds(trades, N_FOLDS)
    print(f"\n{'=' * 92}\n{label}  (n={len(trades)} tagged trades)\n{'=' * 92}")
    print(f"  {'Fold':<5}{'Period':<28}{'All: n/PF':<16}{'RANGING-only: n/PF':<20}{'Delta PF':<10}{'Improved?'}")

    improved = 0
    valid_folds = 0
    all_pfs, ranging_pfs = [], []
    for k, (fold_start, fold_end) in enumerate(bounds):
        if k < N_FOLDS - 1:
            sub = [t for t in trades if fold_start <= t.ts < fold_end]
        else:
            sub = [t for t in trades if fold_start <= t.ts <= fold_end]
        if not sub:
            continue
        ranging = [t for t in sub if t.regime == "RANGING"]
        all_rs = [t.r for t in sub]
        rng_rs = [t.r for t in ranging]
        pf_all = pf_of(all_rs)
        pf_rng = pf_of(rng_rs) if rng_rs else float("nan")
        delta = pf_rng - pf_all if rng_rs else float("nan")
        is_improved = rng_rs and pf_rng > pf_all
        if rng_rs:
            valid_folds += 1
            all_pfs.append(pf_all)
            ranging_pfs.append(pf_rng)
            if is_improved:
                improved += 1
        period = f"{fold_start.date()}->{fold_end.date()}"
        print(f"  {k+1:<5}{period:<28}{f'{len(sub)}/{pf_all:.3f}':<16}"
              f"{f'{len(ranging)}/{pf_rng:.3f}' if rng_rs else 'n=0':<20}"
              f"{f'{delta:+.3f}' if rng_rs else '--':<10}{'YES' if is_improved else ('no' if rng_rs else '--')}")

    print(f"\n  Folds where RANGING-only filter improved PF: {improved}/{valid_folds}")
    if all_pfs:
        print(f"  Mean fold PF -- all trades: {sum(all_pfs)/len(all_pfs):.3f}  |  RANGING-only: {sum(ranging_pfs)/len(ranging_pfs):.3f}")


def main() -> None:
    m1 = load_m1("data/history/NAS100_M1.csv")

    m15 = df_to_bars(resample(m1, 15))
    fvg = load_and_tag("artifacts/first_fvg_15m_2R_spread_0930_all.csv", m15, Timeframe.M15, "NAS100")
    report("First FVG (NAS100 09:30/15m/2R) -- RANGING-only filter, per fold", fvg)

    m30 = df_to_bars(resample(m1, 30))
    sr = load_and_tag("artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv", m30, Timeframe.M30, "NAS100")
    report("SR+Bias (NAS100 30m liquidity-TP) -- RANGING-only filter, per fold", sr)


if __name__ == "__main__":
    main()
