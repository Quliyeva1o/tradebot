"""Definitive Breakout matrix: every symbol x OR width x scan bar x R, fold-tested.

Everything earlier in this session tested a slice -- the R grid at one timeframe,
then the timeframe grid at one R, then folds for a handful of hand-picked cells.
This runs the whole cross-product and fold-tests all of it, so the deployment
question can be answered from one table instead of three.

Every row carries its walk-forward record (rolling 24-month train / 6-month
validation), because full-history PF has repeatedly disagreed with it here:
GER40 30m/M1 shows PF 1.264 and only 5/9 green folds.

DEPLOY column marks what can go live today. NasdaqOrbM1BreakoutStrategy accepts
M1 bars only -- it builds the opening range by accumulating M1 bars and scans M1
closes -- so scan=M1 rows are a .bat flag away while M5/M15 rows would need the
strategy class extended first.

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


def folds_for(trades: list[tuple[date, float]]) -> tuple[int, int, float]:
    if not trades:
        return 0, 0, 0.0
    last = max(d for d, _ in trades)
    nets, s = [], date(2020, 1, 1)
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
                                 rate=(pos / nf if nf else 0.0),
                                 deploy=(scan_m == 1)))
                print(f"  [{i}/{total}] {sym} {or_m}m/M{scan_m} {r:g}R  n={n} folds={pos}/{nf}",
                      file=sys.stderr, flush=True)

    # Only rows that are both profitable overall and green in most folds are
    # worth a deployment conversation; sort the rest below them.
    rows.sort(key=lambda x: (-x["rate"], -x["pf"]))

    print("=" * 132)
    print("TAM MATRIS -- butun simvol x OR x skan x R, walk-forward ile (train 24 ay / val 6 ay)")
    print("=" * 132)
    print(f"{'SIMVOL':8s} {'OR/SKAN':10s} {'R':>3} {'n':>5} {'PF':>6} {'WR':>6} {'netR':>8} "
          f"{'maxDD':>7} {'yasil':>6} {'qirmizi':>8} {'fold':>7} {'foldR':>7} {'DEPLOY':>7}")
    print("-" * 132)
    for x in rows:
        mark = "M1 var" if x["deploy"] else "sinif"
        tf = f"{x['or_m']}m/M{x['scan']}"
        print(f"{x['sym']:8s} {tf:10s} "
              f"{x['r']:>3.0f} {x['n']:>5} {x['pf']:>6.3f} {x['wr']:>5.1f}% {x['net']:>+8.1f} "
              f"{x['dd']:>6.1f}R {x['green']:>5.0f}% {x['streak']:>6} ay {x['pos']:>3}/{x['nf']:<3} "
              f"{x['fnet']:>+7.0f} {mark:>7}")

    print("\n" + "=" * 132)
    print("BUGUN DEPLOY EDILE BILEN (skan=M1) -- fold rekorduna gore")
    print("=" * 132)
    dep = [x for x in rows if x["deploy"] and x["pf"] > 1.0 and x["rate"] >= 0.6]
    if not dep:
        print("  (meyara uygun konfiqurasiya yoxdur)")
    for x in dep:
        print(f"  {x['sym']:8s} {x['or_m']:>3}m/M1 {x['r']:>2.0f}R   PF {x['pf']:>5.3f}  "
              f"netR {x['net']:>+7.1f}  maxDD {x['dd']:>5.1f}R  yasil {x['green']:>3.0f}%  "
              f"fold {x['pos']}/{x['nf']}")

    print("\n" + "=" * 132)
    print("SINIF GENISLENDIRILSE ACILACAQ (skan=M5/M15, fold >=60%) -- potensial qazanc")
    print("=" * 132)
    ext = [x for x in rows if not x["deploy"] and x["pf"] > 1.0 and x["rate"] >= 0.6]
    for x in ext[:15]:
        print(f"  {x['sym']:8s} {x['or_m']:>3}m/M{x['scan']:<2} {x['r']:>2.0f}R   PF {x['pf']:>5.3f}  "
              f"netR {x['net']:>+7.1f}  maxDD {x['dd']:>5.1f}R  yasil {x['green']:>3.0f}%  "
              f"fold {x['pos']}/{x['nf']}")


if __name__ == "__main__":
    main()
