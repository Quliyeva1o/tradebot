"""Definitive Breakout matrix: every symbol x OR width x scan bar x R, fold-tested.

Everything earlier in this session tested a slice -- the R grid at one timeframe,
then the timeframe grid at one R, then folds for a handful of hand-picked cells.
This runs the whole cross-product and fold-tests all of it, so the deployment
question can be answered from one table instead of three.

Every row carries its walk-forward record (rolling 24-month train / 6-month
validation), because full-history PF has repeatedly disagreed with it here:
GER40 30m/M1 shows PF 1.264 and only 5/9 green folds.

Every scan size in the grid is deployable: NasdaqOrbM1BreakoutStrategy builds
its opening range by wall-clock rather than by counting bars, so it reads
whatever bar size it is fed, and run_live_nasdaq_orb.py exposes that as
--scan-timeframe (M1/M5/M15). An earlier version of this file split the output
into "M1 rows you can deploy" and "M5/M15 rows that need the class extended";
the class already handles all three (measured at 100% backtest agreement by
scripts/backtest_orb_breakout_live_class.py) and NDX100 runs M5 live today.

A row with no fold record (`fold` = "tarixce az") did not fail the walk-forward
-- it does not have the 24+6 months of history the fold test needs at all. Do
not read it as a rejection.

Usage:
    python -m scripts.full_walk_forward_matrix
    python -m scripts.full_walk_forward_matrix --symbols XAUUSD,GER40
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
from scripts.consistency_analysis import _cached_load_m1, agg, consistency, max_drawdown_r
from scripts.two_strategy_symbol_sweep import DATA_DIR, SYMBOLS, recent_spread
from scripts.walk_forward_selection import month_add, window

orb_mod.load_m1 = _cached_load_m1

PAIRS = [(15, 1), (15, 5), (30, 1), (30, 5), (30, 15), (60, 1), (60, 5), (60, 15)]
R_VALUES = [3.0, 4.0]
TRAIN_M, VAL_M = 24, 6
# A "60% green folds" rate means nothing over two folds -- FTSE100 has exactly
# two and would otherwise top the shortlist at 2/2. Long-history symbols get 9.
MIN_FOLDS = 5


def folds_for(trades: list[tuple[date, float]]) -> tuple[int, int, float]:
    """Rolling 24m-train / 6m-validation fold record for one config.

    The clock starts at THIS config's own first trade, not at a fixed date.
    It used to start at a hardcoded 2020-01-01, which silently charged every
    short-history symbol for validation windows that predate its data: those
    windows hold zero trades, score netR 0.0, and 0.0 is not > 0, so each one
    counted as a losing fold. JP225 (data from 2024-04) had 4 of its 9 folds
    empty and could therefore never score above 5/9 -- it was rejected at
    "4/9 green" when its real record on the folds it could trade is 4/5.
    FTSE100 (data from 2023-07) lost 3 folds the same way.
    """
    if not trades:
        return 0, 0, 0.0
    last = max(d for d, _ in trades)
    first = min(d for d, _ in trades)
    nets, s = [], date(first.year, first.month, 1)
    while True:
        b = month_add(s, TRAIN_M)
        e = month_add(b, VAL_M)
        if e > last:
            break
        nets.append(agg(window(trades, b, e))[3])
        s = month_add(s, VAL_M)
    return sum(1 for v in nets if v > 0), len(nets), sum(nets)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    rows = []
    total = len(symbols) * len(PAIRS) * len(R_VALUES)
    i = 0
    for sym in symbols:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            print(f"{sym}: data yoxdur", file=sys.stderr)
            continue
        sp = recent_spread(csv)
        for or_m, scan_m in PAIRS:
            for r in R_VALUES:
                i += 1
                tr = orb_mod.run_backtest(str(csv), "full", sp, r, "long",
                                          or_minutes=or_m, scan_minutes=scan_m)
                t = [(date.fromisoformat(str(x.day)[:10]), x.r_multiple) for x in tr]
                if not t:
                    continue
                n, wr, pf, net = agg([v for _, v in t])
                pos, nf, fnet = folds_for(t)
                c = consistency(t)
                rows.append(dict(sym=sym, or_m=or_m, scan=scan_m, r=r, n=n, pf=pf, wr=wr,
                                 net=net, dd=max_drawdown_r(t), green=c["green_pct"],
                                 streak=c["max_red_streak"], pos=pos, nf=nf, fnet=fnet,
                                 # rate is None -- not 0.0 -- when the symbol
                                 # is too young to fold-test, so "untestable"
                                 # never sorts or reads as "failed".
                                 rate=(pos / nf if nf else None),
                                 deploy=True))
                print(f"  [{i}/{total}] {sym} {or_m}m/M{scan_m} {r:g}R  n={n} folds={pos}/{nf}",
                      file=sys.stderr, flush=True)

    # Only rows that are both profitable overall and green in most folds are
    # worth a deployment conversation; sort the rest below them. Fold-untestable
    # rows (rate None) sort with the worst tested ones -- they are not evidence
    # of anything -- but the table labels them so the reason stays visible.
    # 2.0 sorts below every real rate, whose keys run -1.0 (100% green) .. 0.0.
    rows.sort(key=lambda x: (-x["rate"] if x["rate"] is not None else 2.0, -x["pf"]))

    print("=" * 132)
    print("TAM MATRIS -- butun simvol x OR x skan x R, walk-forward ile (train 24 ay / val 6 ay)")
    print("=" * 132)
    print(f"{'SIMVOL':8s} {'OR/SKAN':10s} {'R':>3} {'n':>5} {'PF':>6} {'WR':>6} {'netR':>8} "
          f"{'maxDD':>7} {'yasil':>6} {'qirmizi':>8} {'fold':>7} {'foldR':>7} {'DEPLOY':>7}")
    print("-" * 132)
    for x in rows:
        tf = f"{x['or_m']}m/M{x['scan']}"
        fold = f"{x['pos']:>3}/{x['nf']:<3}" if x["rate"] is not None else "tarixce az"
        print(f"{x['sym']:8s} {tf:10s} "
              f"{x['r']:>3.0f} {x['n']:>5} {x['pf']:>6.3f} {x['wr']:>5.1f}% {x['net']:>+8.1f} "
              f"{x['dd']:>6.1f}R {x['green']:>5.0f}% {x['streak']:>6} ay {fold:>10} "
              f"{x['fnet']:>+7.0f}")

    print("\n" + "=" * 132)
    print(f"DEPLOY MEYARINI KECEN -- PF>1, son-1-il PF>1 ayrica yoxlanilir, "
          f">={MIN_FOLDS} folddan >=60% yasil")
    print("=" * 132)
    dep = [x for x in rows
           if x["pf"] > 1.0 and x["rate"] is not None
           and x["nf"] >= MIN_FOLDS and x["rate"] >= 0.6]
    if not dep:
        print("  (meyara uygun konfiqurasiya yoxdur)")
    for x in dep:
        print(f"  {x['sym']:8s} {x['or_m']:>3}m/M{x['scan']:<2} {x['r']:>2.0f}R   PF {x['pf']:>5.3f}  "
              f"netR {x['net']:>+7.1f}  maxDD {x['dd']:>5.1f}R  yasil {x['green']:>3.0f}%  "
              f"fold {x['pos']}/{x['nf']}")

    print("\n" + "=" * 132)
    print(f"FOLD TESTI ETIBARLI DEYIL (<{MIN_FOLDS} fold) -- REDD DEYIL, SUBUT YOXDUR")
    print("=" * 132)
    young = [x for x in rows
             if x["pf"] > 1.0 and (x["rate"] is None or x["nf"] < MIN_FOLDS)]
    if not young:
        print("  (yoxdur)")
    for x in young[:15]:
        print(f"  {x['sym']:8s} {x['or_m']:>3}m/M{x['scan']:<2} {x['r']:>2.0f}R   PF {x['pf']:>5.3f}  "
              f"netR {x['net']:>+7.1f}  maxDD {x['dd']:>5.1f}R  yasil {x['green']:>3.0f}%  n={x['n']}")


if __name__ == "__main__":
    main()
