"""Recency breakdown for the XAUUSD ORB M15 next-open trade set: performance
sliced by calendar year (last 4), by quarter (last 4 years), and by month
(last 6 months), each shown at three risk-per-trade levels (2%, 0.5%,
0.25%) so the effect of TIME PERIOD and RISK% can be seen separately rather
than only as one aggregate 6.7-year number.

CAVEAT, stated up front: this strategy fires ~20 trades/year (n=137 over
6.7y). Quarterly buckets average ~5 trades, monthly buckets ~1.7 -- WR/PF
at that resolution are DESCRIPTIVE (what actually happened), not
statistically meaningful on their own; a single win or loss swings PF from
0 to infinity in a 1-2 trade bucket. Read the yearly table for the closest
thing to a reliable signal, and the finer tables as "what actually
happened recently," not as independent evidence.

$ columns assume a flat $100,000 starting balance for EACH bucket
(non-compounding across buckets) so periods are directly comparable --
matches walk_forward_montecarlo.py's own per-fold reporting convention
(PF/WR/net_R per fold, not one continuously compounded curve).

Usage:
    python -m scripts.xauusd_orb_recency_breakdown
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(str(Path(__file__).parent.parent.resolve()))

SPREAD_POINTS = 0.39  # robustness_analysis.SPREAD_BY_SYMBOL["XAUUSD"]
CSV_PATH = "artifacts/xauusd_orb_M15_nextopen_reversal_only_trades.csv"
RISK_LEVELS = [0.02, 0.005, 0.0025]
BASELINE = 100_000.0


def load_trades() -> list[tuple[datetime, float]]:
    out = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entry, stop = float(row["entry_price"]), float(row["stop"])
            risk_points = abs(entry - stop)
            cost_r = SPREAD_POINTS / risk_points if risk_points > 0 else 0.0
            r_net = float(row["r_multiple"]) - cost_r
            out.append((datetime.fromisoformat(row["entry_time"]), r_net))
    out.sort(key=lambda t: t[0])
    return out


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def print_bucket_table(title: str, buckets: list[tuple[str, list[float]]]) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    header = f"{'Dövr':<10}{'n':>4}{'WR':>8}{'PF':>8}{'netR':>9}"
    for rp in RISK_LEVELS:
        header += f"{rp*100:>13.2f}% risk"
    print(header)
    all_rs: list[float] = []
    for label, rs in buckets:
        all_rs.extend(rs)
        n = len(rs)
        if n == 0:
            print(f"{label:<10}{0:>4}{'--':>8}{'--':>8}{'--':>9}" + "".join(f"{'--':>18}" for _ in RISK_LEVELS))
            continue
        wr = sum(1 for r in rs if r > 0) / n * 100
        pf = pf_of(rs)
        net_r = sum(rs)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        line = f"{label:<10}{n:>4}{wr:>7.1f}%{pf_str:>8}{net_r:>+9.2f}"
        for rp in RISK_LEVELS:
            line += f"{net_r * rp * BASELINE:>+17,.0f}"
        print(line)
    if len(buckets) > 1:
        n = len(all_rs)
        wr = sum(1 for r in all_rs if r > 0) / n * 100 if n else 0.0
        pf = pf_of(all_rs)
        net_r = sum(all_rs)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        line = f"{'CƏM':<10}{n:>4}{wr:>7.1f}%{pf_str:>8}{net_r:>+9.2f}"
        for rp in RISK_LEVELS:
            line += f"{net_r * rp * BASELINE:>+17,.0f}"
        print("-" * 100)
        print(line)


def main() -> None:
    trades = load_trades()
    print(f"n={len(trades)}  {trades[0][0].date()} -> {trades[-1][0].date()}  "
          f"($ columns: flat $100k per bucket, non-compounding, at 2.00% / 0.50% / 0.25% risk)")

    by_year: dict[int, list[float]] = defaultdict(list)
    for ts, r in trades:
        by_year[ts.year].append(r)
    years = sorted(by_year)[-4:]
    labels = [f"{y}{' (qismən)' if ts_max_year_partial(trades, y) else ''}" for y in years]
    print_bucket_table("1) Son 4 il, İLBƏİL", list(zip(labels, [by_year[y] for y in years])))

    by_q: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ts, r in trades:
        if ts.year in years:
            by_q[(ts.year, (ts.month - 1) // 3 + 1)].append(r)
    quarters = sorted(by_q)
    print_bucket_table("2) Son 4 il, RÜBBƏ-RÜB (fəsilbə-fəsil)",
                        [(f"{y}-Q{q}", by_q[(y, q)]) for y, q in quarters])

    by_m: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ts, r in trades:
        by_m[(ts.year, ts.month)].append(r)
    last_key = max(by_m)
    last_idx = last_key[0] * 12 + last_key[1]
    last6_keys = sorted(k for k in by_m if last_idx - (k[0] * 12 + k[1]) < 6)
    print_bucket_table("3) Son 6 ay, AYBAAY",
                        [(f"{y}-{m:02d}", by_m[(y, m)]) for y, m in last6_keys])


def ts_max_year_partial(trades: list[tuple[datetime, float]], year: int) -> bool:
    return year == trades[-1][0].year and trades[-1][0].month < 12


if __name__ == "__main__":
    main()
