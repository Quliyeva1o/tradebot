"""Aggregates a trade-log CSV (entry_time, r_multiple columns) into the
period cuts requested for the 3-strategy comparison: last 1y/3y/3mo/1mo
aggregates, last-2y and last-1y month-by-month breakdowns, and a
full-history half-yearly stability table.

Usage:
    python -m scripts.summarize_trade_log --input <csv> --label <name>
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path


def load_trades(path: str) -> list[dict]:
    trades = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["entry_time"])
            trades.append({"entry_time": ts, "r": float(row["r_multiple"])})
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "pf": None, "net_r": 0.0}
    wins = sum(1 for t in trades if t["r"] > 0)
    gross_win = sum(t["r"] for t in trades if t["r"] > 0)
    gross_loss = sum(-t["r"] for t in trades if t["r"] < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return {
        "trades": n,
        "win_rate": round(wins / n * 100, 1),
        "pf": round(pf, 2),
        "net_r": round(sum(t["r"] for t in trades), 1),
    }


def filter_since(trades: list[dict], since: datetime) -> list[dict]:
    return [t for t in trades if t["entry_time"] >= since]


def monthly_breakdown(trades: list[dict], months_back: int, now: datetime) -> list[dict]:
    cutoff = (now.replace(day=1) - timedelta(days=months_back * 31)).replace(day=1)
    buckets: dict[tuple[int, int], list[dict]] = {}
    for t in trades:
        if t["entry_time"] < cutoff:
            continue
        key = (t["entry_time"].year, t["entry_time"].month)
        buckets.setdefault(key, []).append(t)
    rows = []
    for (y, m), ts in sorted(buckets.items()):
        s = stats(ts)
        s["period"] = f"{y}-{m:02d}"
        rows.append(s)
    return rows


def half_yearly_breakdown(trades: list[dict]) -> list[dict]:
    buckets: dict[tuple[int, int], list[dict]] = {}
    for t in trades:
        half = 1 if t["entry_time"].month <= 6 else 2
        key = (t["entry_time"].year, half)
        buckets.setdefault(key, []).append(t)
    rows = []
    for (y, h), ts in sorted(buckets.items()):
        s = stats(ts)
        s["period"] = f"{y} H{h}"
        rows.append(s)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    trades = load_trades(args.input)
    now = trades[-1]["entry_time"] if trades else datetime.now(trades[0]["entry_time"].tzinfo)
    print(f"=== {args.label} === ({len(trades)} trades, {trades[0]['entry_time'].date()} -> {trades[-1]['entry_time'].date()})")

    print("\n-- Aggregate cuts --")
    for label, delta_days in [("last_1mo", 30), ("last_3mo", 91), ("last_1y", 365), ("last_3y", 365 * 3)]:
        since = now - timedelta(days=delta_days)
        s = stats(filter_since(trades, since))
        print(f"{label}: {s}")

    print("\n-- Last 12 months, month-by-month --")
    for row in monthly_breakdown(trades, 12, now):
        print(row)

    print("\n-- Last 24 months, month-by-month --")
    for row in monthly_breakdown(trades, 24, now):
        print(row)

    print("\n-- Half-yearly stability (full history) --")
    for row in half_yearly_breakdown(trades):
        print(row)


if __name__ == "__main__":
    main()
