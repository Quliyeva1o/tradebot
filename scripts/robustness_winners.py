"""Bootstrap/Monte Carlo confidence intervals + recency split for the two
strategy configs this repo settled on after the 2026-08-28 spread work
(FIRST_FVG_15M_SPREAD_REPORT.md, SR_DAILY_BIAS_SPREAD_REPORT.md):

  - First FVG: NAS100, 09:30 session, 15m, fixed 2R
  - SR + Daily Bias: NAS100, 30m, liquidity-TP

Reuses scripts.robustness_analysis's stats()/bootstrap()/recency_split()
UNCHANGED -- only the trade loader differs, since these two strategies'
trade logs use different column names (r_multiple_net, not r_multiple) than
the CSVs robustness_analysis.py was originally written against.

Unlike robustness_analysis.py's own cost_stress() (which stress-tests a
SPREAD-FREE trade log by subtracting an assumed cost), the R-multiples
loaded here are ALREADY net of spread -- see the two spread reports above.
Running cost_stress on top would double-count the spread deduction, so it
is deliberately not used here.

Usage:
    python -m scripts.robustness_winners
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime

from scripts.robustness_analysis import bootstrap, recency_split, stats

TRADE_LOGS = {
    "First FVG (NAS100 09:30/15m/2R)": "artifacts/first_fvg_15m_spread_0930_all.csv",
    "SR+Bias (NAS100 30m liquidity-TP)": "artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv",
}


@dataclass
class TradeRow:
    ts: datetime
    r: float


def load_net(path: str) -> list[TradeRow]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["entry_time"])
            rows.append(TradeRow(ts=ts, r=float(row["r_multiple_net"])))
    rows.sort(key=lambda t: t.ts)
    return rows


def windowed(trades: list[TradeRow], years: float | None) -> list[TradeRow]:
    if years is None:
        return trades
    end = trades[-1].ts
    cutoff = end.replace(year=end.year - int(years)) if years == int(years) else end
    return [t for t in trades if t.ts >= cutoff]


def main() -> None:
    for label, path in TRADE_LOGS.items():
        trades = load_net(path)
        print(f"\n{'=' * 74}\n{label}  (n={len(trades)} total, {trades[0].ts.date()} -> {trades[-1].ts.date()})\n{'=' * 74}")

        for window_label, years in [("Full history", None), ("Last 5y", 5), ("Last 1y", 1)]:
            sub = windowed(trades, years)
            rs = [t.r for t in sub]
            print(f"\n-- {window_label} (n={len(sub)}) --")
            print("Stats:", stats(rs))
            if len(sub) >= 10:
                b = bootstrap(sub, iterations=5000, seed=42)
                print("Bootstrap (5000x resample of R-multiples):", b)
            else:
                print("Bootstrap: skipped (n<10)")

        print(f"\n-- Recency split (80/20 chronological, full history, n={len(trades)}) --")
        rs_full = [t.r for t in trades]
        # recency_split expects TradeRow-like objects with .r; reuse directly.
        print(recency_split(trades, split_frac=0.8))


if __name__ == "__main__":
    main()
