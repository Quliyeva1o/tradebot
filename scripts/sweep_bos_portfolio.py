"""One fixed sweep+BOS configuration across a basket, and what stacking it actually buys.

A rule worth 1-3R a year on one symbol is not tradeable on its own. The question this
answers is whether the same rule, unchanged, is worth running on fifteen instruments at
once -- which it is only if the per-symbol edges are real AND their returns are close to
independent, so the combined drawdown grows more slowly than the combined return.

THE CONFIGURATION IS FIXED AND CHOSEN IN ADVANCE: equilibrium retracement, stop behind
the swept wick, target the next marked liquidity level. It is the cell that was
pre-registered and tested out of sample on EURUSD, and it is NOT re-selected here. Every
symbol below is fresh data for it. Picking the best cell per symbol would turn fifteen
independent tests back into one big fishing expedition, which is the failure mode this
whole exercise exists to avoid.

Each (symbol, side) is its own sleeve risking 1R per trade. Sleeves are summed daily, so
the portfolio curve is in R and assumes equal risk everywhere -- no volatility targeting,
no conviction weighting. That is deliberately the dumbest possible allocation: if the
result only works with clever weights, it is the weights doing the work.

Usage:
    python -m scripts.sweep_bos_portfolio --dir data/history/cfi_portfolio
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.liquidity_sweep_bos_backtest import Session, SweepTrade, run_backtest  # noqa: E402
from scripts.two_strategy_symbol_sweep import recent_spread  # noqa: E402
from strategy.liquidity_sweep_bos import LiquiditySweepBosConfig  # noqa: E402

# The pre-registered cell. Do not tune this file's way out of a bad result.
PORTFOLIO_CONFIG = LiquiditySweepBosConfig()

TRADING_DAYS = 252


def profit_factor(values: list[float]) -> float:
    loss = -sum(v for v in values if v <= 0)
    return sum(v for v in values if v > 0) / loss if loss > 0 else float("inf")


def summarize(values: list[float]) -> str:
    if not values:
        return f"{'n=0':<44}"
    wins = sum(1 for v in values if v > 0)
    return (f"n={len(values):<5} WR={wins / len(values) * 100:5.1f}%  "
            f"PF={profit_factor(values):6.3f}  netR={sum(values):+8.1f}")


def daily_r(trades: list[SweepTrade]) -> dict[date, float]:
    """R booked per calendar day, keyed by the day the trade was ENTERED.

    Entry day rather than exit day: a sleeve's risk is committed when the order goes in,
    and every trade here closes the same session anyway.
    """
    out: dict[date, float] = defaultdict(float)
    for t in trades:
        out[t.day] += t.r_net
    return out


def max_drawdown(curve: np.ndarray) -> float:
    """Deepest peak-to-trough fall of a cumulative-R curve, as a positive number."""
    if len(curve) == 0:
        return 0.0
    return float(np.max(np.maximum.accumulate(curve) - curve))


def sharpe(daily: np.ndarray) -> float:
    """Annualised mean/stdev of the daily R series, zero days included.

    Zero days belong in the denominator: a sleeve that trades twice a month really does
    sit flat the rest of the time, and leaving those days out would flatter the ratio.
    """
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return 0.0
    return float(daily.mean() / daily.std(ddof=1) * math.sqrt(TRADING_DAYS))


def run_basket(
    paths: dict[str, Path], since: str | None
) -> tuple[dict[str, dict[str, list[SweepTrade]]], set[date]]:
    """Each symbol's LONG and SHORT trades, and the calendar the basket was open on.

    The calendar is every NY date any symbol had a session on, NOT just the dates that
    produced a trade. A sleeve that fires twice a month is flat the rest of the time, and
    those flat days have to be in the series or the risk figures come out flattered.
    Frames are loaded one at a time and dropped.
    """
    results: dict[str, dict[str, list[SweepTrade]]] = {}
    calendar: set[date] = set()
    for symbol, path in paths.items():
        spread = recent_spread(path)
        session = Session.from_csv(str(path), since)
        calendar |= set(session.day_windows(PORTFOLIO_CONFIG))
        sides = {}
        for side, long in (("LONG", True), ("SHORT", False)):
            trades, _ = run_backtest(session, PORTFOLIO_CONFIG, symbol, spread, long=long)
            sides[side] = trades
        results[symbol] = sides
        print(f"  {symbol:<13} spread={spread:<12.8g} "
              f"LONG {len(sides['LONG']):>4}  SHORT {len(sides['SHORT']):>4}", file=sys.stderr)
        del session
    return results, calendar


def align(
    series: dict[str, dict[date, float]], calendar: set[date] | None = None
) -> tuple[list[date], np.ndarray]:
    """(days, matrix) of every symbol's daily R, zeros filled.

    `calendar` is the set of days the basket could have traded. Without it the index
    collapses to days something DID trade, which drops every flat day and inflates both
    the correlations and the Sharpe.
    """
    days = sorted(calendar if calendar is not None else {d for s in series.values() for d in s})
    matrix = np.array([[s.get(d, 0.0) for d in days] for s in series.values()])
    return days, matrix


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="Directory of {SYMBOL}_M1.csv files")
    ap.add_argument("--since", help="Skip CSV rows before this broker-local date")
    ap.add_argument("--out-csv", help="Write the portfolio's daily R curve")
    args = ap.parse_args()

    paths = {p.name[: -len("_M1.csv")]: p for p in sorted(Path(args.dir).glob("*_M1.csv"))}
    if not paths:
        raise SystemExit(f"no *_M1.csv under {args.dir}")
    print(f"basket of {len(paths)}: {', '.join(paths)}\n", file=sys.stderr)

    results, calendar = run_basket(paths, args.since)

    print(f"\nOne fixed configuration -- {PORTFOLIO_CONFIG.retrace_mode.value} retracement, "
          f"stop {PORTFOLIO_CONFIG.stop_mode.value}, target {PORTFOLIO_CONFIG.target_mode.value}")
    print("Chosen in advance and unchanged per symbol.\n")

    print("per symbol (net R, spread charged):")
    combined: dict[str, dict[date, float]] = {}
    per_symbol_total: list[tuple[str, float, int]] = []
    for symbol, sides in results.items():
        longs = [t.r_net for t in sides["LONG"]]
        shorts = [t.r_net for t in sides["SHORT"]]
        both = longs + shorts
        print(f"  {symbol:<13} LONG  {summarize(longs)}")
        print(f"  {'':<13} SHORT {summarize(shorts)}")
        print(f"  {'':<13} BOTH  {summarize(both)}")
        merged = daily_r(sides["LONG"] + sides["SHORT"])
        combined[symbol] = merged
        per_symbol_total.append((symbol, sum(both), len(both)))

    days, matrix = align(combined, calendar)
    if len(days) < 2:
        raise SystemExit("not enough trading days to aggregate")
    span_years = (days[-1] - days[0]).days / 365.25

    print(f"\nwindow {days[0]} -> {days[-1]}  ({span_years:.1f}y)")
    print("\nper-symbol totals, worst first:")
    for symbol, total, n in sorted(per_symbol_total, key=lambda kv: kv[1]):
        print(f"  {symbol:<13} {total:+8.1f}R over {n:>4} trades   {total / span_years:+6.2f}R/yr")

    names = list(combined)
    corr = np.corrcoef(matrix) if len(names) > 1 else np.array([[1.0]])
    off = corr[~np.eye(len(names), dtype=bool)]
    print(f"\ndaily-R correlation across the basket: mean {off.mean():+.3f}  "
          f"min {off.min():+.3f}  max {off.max():+.3f}")
    pairs = sorted(((corr[i, j], names[i], names[j])
                    for i in range(len(names)) for j in range(i + 1, len(names))),
                   reverse=True)
    print("  most correlated pairs:")
    for value, a, b in pairs[:5]:
        print(f"    {a:<13} {b:<13} {value:+.3f}")

    portfolio = matrix.sum(axis=0)
    curve = np.cumsum(portfolio)
    active = (matrix != 0).sum(axis=0)
    print(f"\nportfolio, 1R per trade per sleeve ({len(names) * 2} sleeves):")
    print(f"  total {curve[-1]:+.1f}R over {int((matrix != 0).sum())} trades "
          f"-> {curve[-1] / span_years:+.2f}R/yr")
    peak_to_trough = max_drawdown(curve)
    ratio = f"   return/drawdown {curve[-1] / peak_to_trough:.2f}" if peak_to_trough > 0 else ""
    print(f"  max drawdown {peak_to_trough:.1f}R{ratio}")
    print(f"  annualised Sharpe of daily R: {sharpe(portfolio):.2f}")
    print(f"  days with any trade: {int((active > 0).sum())} of {len(days)}   "
          f"busiest day: {int(active.max())} sleeves")

    best_single = max(per_symbol_total, key=lambda kv: kv[1])
    single_curve = np.cumsum(np.array([combined[best_single[0]].get(d, 0.0) for d in days]))
    print(f"\n  for scale, the best single symbol ({best_single[0]}): "
          f"{best_single[1]:+.1f}R, max drawdown {max_drawdown(single_curve):.1f}R")

    by_year: dict[int, float] = defaultdict(float)
    for d, r in zip(days, portfolio):
        by_year[d.year] += r
    print("\n  portfolio by year:")
    for year in sorted(by_year):
        print(f"    {year}  {by_year[year]:+7.1f}R")

    if args.out_csv:
        with open(args.out_csv, "w", encoding="utf-8", newline="") as f:
            f.write("day,r_net,cumulative_r,sleeves_active\n")
            for d, r, c, a in zip(days, portfolio, curve, active):
                f.write(f"{d},{r:.6f},{c:.6f},{a}\n")
        print(f"\nwrote {args.out_csv}")


if __name__ == "__main__":
    main()
