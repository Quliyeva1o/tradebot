"""Robustness battery for a trade log: recency (out-of-sample proxy) split,
bootstrap resampling confidence intervals, and a realistic spread-cost
stress test. Run against the 8 strategy/TF combos requested for a
side-by-side reliability comparison.

Usage:
    python -m scripts.robustness_analysis
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import datetime

# Round-trip spread cost, in price units, per symbol -- pulled LIVE from
# this account's actual mt5.symbol_info() (bid/ask), not an estimate.
# Charged ONCE per trade (entry side), matching how every other cost-aware
# script in this repo already treats spread.
SPREAD_BY_SYMBOL = {
    "XAUUSD": 0.39,
    "EURUSD": 0.00014,
    "NAS100": 3.0,
}


@dataclass
class TradeRow:
    ts: datetime
    r: float
    entry: float | None
    stop: float | None


def load(path: str) -> list[TradeRow]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["entry_time"])
            entry = float(row["entry_price"]) if row.get("entry_price") not in (None, "") else None
            stop = float(row["stop"]) if row.get("stop") not in (None, "") else None
            rows.append(TradeRow(ts=ts, r=float(row["r_multiple"]), entry=entry, stop=stop))
    rows.sort(key=lambda t: t.ts)
    return rows


def pf(rs: list[float]) -> float:
    gw = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    return gw / gl if gl > 0 else float("inf")


def stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "wr": None, "pf": None, "r": 0.0}
    wins = sum(1 for r in rs if r > 0)
    return {"n": n, "wr": round(wins / n * 100, 1), "pf": round(pf(rs), 2), "r": round(sum(rs), 1)}


def recency_split(trades: list[TradeRow], split_frac: float = 0.8) -> dict:
    """Chronological 80/20 split -- NOT a formal walk-forward refit (none of
    these strategies' configs were parameter-fit against this data via a
    search/optimization loop, so there is no "in-sample fit" to leak; this
    checks the weaker but still meaningful question of whether the edge
    held up in the untouched, most-recent slice of history).
    """
    cut = int(len(trades) * split_frac)
    first = [t.r for t in trades[:cut]]
    last = [t.r for t in trades[cut:]]
    return {"first_80pct": stats(first), "last_20pct": stats(last)}


def bootstrap(trades: list[TradeRow], iterations: int = 5000, seed: int = 42) -> dict:
    rs = [t.r for t in trades]
    n = len(rs)
    if n < 10:
        return {"note": "too few trades for a meaningful bootstrap (n<10)"}
    rng = random.Random(seed)
    pfs = []
    for _ in range(iterations):
        sample = [rs[rng.randrange(n)] for _ in range(n)]
        pfs.append(pf(sample))
    pfs.sort()
    p5 = pfs[int(0.05 * iterations)]
    p50 = pfs[int(0.50 * iterations)]
    p95 = pfs[int(0.95 * iterations)]
    prob_gt1 = sum(1 for p in pfs if p > 1.0) / iterations
    return {"pf_p5": round(p5, 2), "pf_p50": round(p50, 2), "pf_p95": round(p95, 2), "prob_pf_gt_1": round(prob_gt1 * 100, 1)}


def cost_stress(trades: list[TradeRow], symbol: str) -> dict:
    spread = SPREAD_BY_SYMBOL[symbol]
    adjusted = []
    missing_price_data = 0
    for t in trades:
        if t.entry is None or t.stop is None:
            missing_price_data += 1
            adjusted.append(t.r)
            continue
        risk_dist = abs(t.entry - t.stop)
        cost_r = spread / risk_dist if risk_dist > 0 else 0.0
        adjusted.append(t.r - cost_r)
    result = stats(adjusted)
    result["missing_price_data"] = missing_price_data
    return result


COMBOS = [
    ("Order Flow", "XAUUSD", "5m", "artifacts/order_flow_bias_trades_XAUUSD_5m.csv", "XAUUSD"),
    ("Order Flow", "NAS100", "15m", "artifacts/order_flow_bias_trades_NAS100_15m.csv", "NAS100"),
    ("SR+Bias", "NAS100", "30m", "artifacts/sr_daily_bias_liquidity_tp_trades_NAS100_30m.csv", "NAS100"),
    ("First FVG", "NAS100", "M1", "artifacts/midnight_fvg_live_trades.csv", "NAS100"),
    ("Order Flow", "EURUSD", "5m", "artifacts/order_flow_bias_trades_EURUSD_5m.csv", "EURUSD"),
    ("Order Flow", "NAS100", "5m", "artifacts/order_flow_bias_trades_NAS100_5m.csv", "NAS100"),
    ("SR+Bias", "XAUUSD", "15m", "artifacts/sr_daily_bias_liquidity_tp_trades_XAUUSD_15m.csv", "XAUUSD"),
    ("Order Flow", "XAUUSD", "15m", "artifacts/order_flow_bias_trades_XAUUSD_15m.csv", "XAUUSD"),
]


def main() -> None:
    for strat, symbol, tf, path, cost_symbol in COMBOS:
        trades = load(path)
        print(f"\n{'=' * 70}\n{strat} -- {symbol} {tf}  (n={len(trades)})\n{'=' * 70}")
        print("Full history:", stats([t.r for t in trades]))
        print("Recency split (80/20):", recency_split(trades))
        print("Bootstrap (5000x):", bootstrap(trades))
        print("Cost-stressed (spread deducted):", cost_stress(trades, cost_symbol))


if __name__ == "__main__":
    main()
