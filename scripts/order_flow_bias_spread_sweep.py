"""Order Flow + Daily Bias + Trendline strategy, backtested with spread,
across every symbol/timeframe combination -- finishes the "12-combo re-sweep"
SESSION_HANDOFF.md left unfinished (section 5, "Order Flow"), extended from
3 to 5 symbols (adding GBPUSD/USDJPY, matching the SR sweep's coverage) and
adding spread cost, which no Order Flow backtest in this repo has ever
applied.

Reuses scripts.order_flow_bias_backtest.run_backtest() UNCHANGED -- not
reimplemented -- since that module already carries the verified post-fix
HTF-bias lookahead guard (SESSION_HANDOFF.md #2.1: the entire apparent edge
was a lookahead leak before the fix; `htf_bias_to_index` in
scripts/backtest_common.py is what closes it). Correctness re-reviewed here
before running the sweep: bias is re-stamped to each 1H bar's CLOSE before
any execution bar reads it (no lookahead), order-flow delta/CVD features are
built per-execution-bar from ONLY the M1 sub-bars inside that same bar (no
future leak), pivot highs/lows are confirmed non-repainting, PDH/PDL use only
the prior FULLY CLOSED day, and entry fills at that bar's own close with
SL/TP checked starting the NEXT bar (same safe pattern as SR -- no same-bar-
as-entry stop-out gap, unlike the FVG family).

Spread model: identical convention to the SR and First FVG spread work --
cost_r = spread_price / risk_distance, subtracted once per trade (entry
side). NAS100 uses the fixed 3.0-point round-trip constant (its own spread
column reads 0.0 before 2024); XAUUSD/EURUSD/GBPUSD/USDJPY use their ACTUAL
historical per-bar spread (verified non-zero and realistic across the full
2020-2026 history in the SR spread work).

Usage:
    python -m scripts.order_flow_bias_spread_sweep
    python -m scripts.order_flow_bias_spread_sweep --symbols NAS100,XAUUSD --timeframes 15,30
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

import scripts.order_flow_bias_backtest as ofb

# run_backtest() reloads and re-parses the full M1 CSV (millions of rows,
# pure-Python csv.DictReader + datetime.strptime per row) EVERY call, even
# though the same file is reused across all 4 timeframes for one symbol --
# that redundant parse was the dominant cost in a smoke test (a single
# NAS100 60m run took 3+ minutes). Caching load_m1() per path here (by
# monkeypatching the module attribute run_backtest() looks up at call time,
# NOT the local `run_backtest`/`write_trades_csv` symbols already bound in
# this file) cuts that to one parse per symbol instead of one per combo --
# run_backtest()'s own logic is untouched.
ofb.load_m1 = lru_cache(maxsize=8)(ofb.load_m1)
run_backtest = ofb.run_backtest
write_trades_csv = ofb.write_trades_csv

NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")

SYMBOLS = ["NAS100", "XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
TIMEFRAMES = [5, 15, 30, 60]

FIXED_SPREAD_OVERRIDE = {"NAS100": 3.0}
RISK_PCT = 0.01
STARTING_BALANCE = 100_000.0


@dataclass
class NetTrade:
    entry_time: pd.Timestamp
    direction: str
    exit_reason: str
    spread_price: float
    r_multiple_gross: float
    r_multiple: float
    pnl_usd: float


@lru_cache(maxsize=8)
def _load_raw_spread(path: str) -> pd.Series:
    """Loads just the M1 spread column (own lightweight parse, cached per
    path so the 20-combo sweep parses each symbol's CSV for spread only
    once, not once per timeframe).
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append((ny_ts, float(row.get("spread") or 0.0)))
    df = pd.DataFrame(rows, columns=["ts", "spread"]).set_index("ts").sort_index()
    return df["spread"]


def load_spread_series(path: str, tf_minutes: int) -> pd.Series:
    """Raw M1 spread, resampled (mean) to the execution timeframe -- a
    lightweight side-channel so scripts.order_flow_bias_backtest.run_backtest()
    (which never sees spread) doesn't need to be touched.
    """
    raw = _load_raw_spread(path)
    return raw.resample(f"{tf_minutes}min", label="left", closed="left").mean()


def apply_spread(symbol: str, tf_minutes: int, trades: list, spread_series: pd.Series) -> list[NetTrade]:
    fixed_spread = FIXED_SPREAD_OVERRIDE.get(symbol)
    net_trades = []
    for t in trades:
        risk_dist = abs(t.entry_price - t.stop)
        if fixed_spread is not None:
            spread_price = fixed_spread
        else:
            spread_price = spread_series.get(t.entry_time, 0.0)
            if pd.isna(spread_price):
                spread_price = 0.0
        cost_r = (spread_price / risk_dist) if risk_dist > 0 else 0.0
        r_net = t.r_multiple - cost_r
        net_trades.append(NetTrade(
            entry_time=t.entry_time, direction=t.direction, exit_reason=t.exit_reason,
            spread_price=round(float(spread_price), 6),
            r_multiple_gross=round(t.r_multiple, 4), r_multiple=round(r_net, 4),
            pnl_usd=round(r_net * STARTING_BALANCE * RISK_PCT, 2),
        ))
    return net_trades


def summarize(trades: list[NetTrade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "net_pnl": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    return {
        "trades": n, "win_rate": round(wins / n * 100, 1),
        "profit_factor": (round(pf, 3) if gl > 0 else None),
        "net_pnl": round(sum(t.pnl_usd for t in trades), 2),
    }


def write_net_trades_csv(trades: list[NetTrade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_time", "direction", "exit_reason", "spread_price", "r_multiple_gross", "r_multiple_net", "pnl_usd_net"])
        for t in trades:
            w.writerow([t.entry_time, t.direction, t.exit_reason, t.spread_price, t.r_multiple_gross, t.r_multiple, t.pnl_usd])


def period_breakdown(trades: list[NetTrade], end_date) -> dict:
    end = pd.Timestamp(end_date)
    windows = {
        "5y": pd.DateOffset(years=5), "1y": pd.DateOffset(years=1),
        "3mo": pd.DateOffset(months=3), "1mo": pd.DateOffset(months=1),
    }
    window_starts = {label: (end - off).date().isoformat() for label, off in windows.items()}

    def day_of(t: NetTrade) -> str:
        return t.entry_time.date().isoformat()

    window_summaries = {
        label: summarize([t for t in trades if window_starts[label] <= day_of(t) <= end.date().isoformat()])
        for label in windows
    }

    y1_trades = [t for t in trades if window_starts["1y"] <= day_of(t) <= end.date().isoformat()]
    monthly: dict[str, list[NetTrade]] = {}
    for t in y1_trades:
        monthly.setdefault(day_of(t)[:7], []).append(t)
    monthly_summary = {ym: summarize(ts) for ym, ts in sorted(monthly.items())}

    m1_trades = [t for t in trades if window_starts["1mo"] <= day_of(t) <= end.date().isoformat()]
    daily_by_day: dict[str, list[NetTrade]] = {}
    for t in m1_trades:
        daily_by_day.setdefault(day_of(t), []).append(t)
    daily_summary = {d: summarize(ts) for d, ts in sorted(daily_by_day.items())}

    y5_start = pd.Timestamp(window_starts["5y"])
    half_year_summary = {}
    cursor = y5_start
    while cursor < end:
        nxt = cursor + pd.DateOffset(months=6)
        label = f"{cursor.date().isoformat()}..{min(nxt, end).date().isoformat()}"
        sub = [t for t in trades if cursor.date().isoformat() <= day_of(t) < nxt.date().isoformat()]
        half_year_summary[label] = summarize(sub)
        cursor = nxt

    return {
        "windows": window_summaries, "monthly_last_1y": monthly_summary,
        "daily_last_1mo": daily_summary, "half_year_last_5y": half_year_summary,
        "window_starts": window_starts, "end_date": end.date().isoformat(),
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(str(t) for t in TIMEFRAMES))
    parser.add_argument("--output-json", default="artifacts/order_flow_bias_spread_sweep.json",
                         help="Use a per-symbol path when running multiple symbols in parallel processes, to avoid them clobbering each other's output.")
    parser.add_argument("--min-confirmations", type=int, default=None,
                         help="Overrides scripts.order_flow_bias_backtest.OF_MIN_CONFIRMATIONS (default 3) "
                         "via monkeypatch, to test whether relaxing this hard gate produces enough trades "
                         "to draw a statistically meaningful conclusion (SESSION_HANDOFF.md's own suggested "
                         "next step -- see ORDER_FLOW_SPREAD_REPORT.md section on sample size).")
    args = parser.parse_args()

    if args.min_confirmations is not None:
        ofb.OF_MIN_CONFIRMATIONS = args.min_confirmations
        print(f"NOTE: OF_MIN_CONFIRMATIONS monkeypatched to {args.min_confirmations} (default is 3).")

    symbols = args.symbols.split(",")
    timeframes = [int(t) for t in args.timeframes.split(",")]

    all_results: dict[str, dict] = {}

    for symbol in symbols:
        input_csv = f"data/history/{symbol}_M1.csv"
        for tf in timeframes:
            key = f"{symbol}_{tf}m"
            gross_trades, skip_counts = run_backtest(tf, input_csv)
            spread_series = load_spread_series(input_csv, tf)
            trades = apply_spread(symbol, tf, gross_trades, spread_series)
            write_net_trades_csv(trades, f"artifacts/of_sweep_{key}_trades.csv")

            if not trades:
                print(f"{key}: 0 trades  skip_top3={sorted(skip_counts.items(), key=lambda kv: -kv[1])[:3]}")
                all_results[key] = {"symbol": symbol, "timeframe": tf, "trades": 0}
                continue

            end_date = max(t.entry_time for t in trades).date()
            breakdown = period_breakdown(trades, end_date)
            print(f"{key}: n_total={len(trades)}  5y={breakdown['windows']['5y']}  1y={breakdown['windows']['1y']}")
            all_results[key] = {
                "symbol": symbol, "timeframe": tf, "total_trades": len(trades), **breakdown,
            }

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull sweep JSON written to {args.output_json}")


if __name__ == "__main__":
    main()
