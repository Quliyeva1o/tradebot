"""Runs scripts.order_flow_bias_backtest across every symbol/TF combo and
prints a ranking table (mirrors artifacts/master_symbol_tf_ranking.csv's
format from the earlier SR+Bias sweep this session).
"""

from __future__ import annotations

import time

from scripts.order_flow_bias_backtest import run_backtest, write_trades_csv

SYMBOLS = ["XAUUSD", "EURUSD", "NAS100"]
TFS = [5, 15, 30, 60]


def stats(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": None, "pf": None, "r": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = gp / gl if gl > 0 else float("inf")
    return {"n": n, "wr": round(wins / n * 100, 1), "pf": round(pf, 2), "r": round(sum(t.r_multiple for t in trades), 1)}


def main() -> None:
    rows = []
    for symbol in SYMBOLS:
        for tf in TFS:
            t0 = time.time()
            input_csv = f"data/history/{symbol}_M1.csv"
            trades, skip_counts = run_backtest(tf, input_csv)
            write_trades_csv(trades, f"artifacts/order_flow_bias_trades_{symbol}_{tf}m.csv")
            s = stats(trades)
            dt = time.time() - t0
            print(f"{symbol} {tf}m: {s} ({dt:.0f}s) skip_funnel_top3={sorted(skip_counts.items(), key=lambda kv: -kv[1])[:3]}")
            rows.append({"symbol": symbol, "tf": tf, **s})

    print("\n=== RANKING (by PF, then by trade count) ===")
    ranked = sorted(rows, key=lambda r: (-(r["pf"] or 0), -r["n"]))
    for r in ranked:
        print(r)


if __name__ == "__main__":
    main()
