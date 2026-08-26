"""Day-by-day trade breakdown for the last N days of a trade-log CSV
(entry_time, r_multiple columns) -- see scripts/summarize_trade_log.py for
the period-cut/monthly version this complements.

Usage:
    python -m scripts.daily_breakdown --input <csv> --label <name> --days 30
"""

from __future__ import annotations

import argparse
import csv
from datetime import timedelta, datetime


def load_trades(path: str) -> list[dict]:
    trades = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["entry_time"])
            trades.append({"entry_time": ts, "r": float(row["r_multiple"])})
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    trades = load_trades(args.input)
    now = trades[-1]["entry_time"]
    cutoff = now - timedelta(days=args.days)

    buckets: dict = {}
    for t in trades:
        if t["entry_time"] < cutoff:
            continue
        key = t["entry_time"].date()
        buckets.setdefault(key, []).append(t["r"])

    print(f"=== {args.label} === last {args.days}d (since {cutoff.date()})")
    for day in sorted(buckets):
        rs = buckets[day]
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        gw = sum(r for r in rs if r > 0)
        gl = sum(-r for r in rs if r < 0)
        pf = round(gw / gl, 2) if gl > 0 else float("inf")
        net = round(sum(rs), 1)
        print(f"{day} | trades={n} win_rate={round(wins/n*100,1)}% pf={pf} net_r={net}")


if __name__ == "__main__":
    main()
