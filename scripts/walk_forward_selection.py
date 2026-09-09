"""Walk-forward test of the SELECTION itself, not of a single fixed config.

The sweep picked its winners by looking at all 6.7 years at once, so their
headline numbers include an unknown amount of hindsight. This measures how much
by replaying the decision: at each fold, rank every candidate using ONLY the
train window, "trade" the winner through the next unseen validation window, and
compare what the train window promised against what the validation window paid.

The gap between those two is the overfitting cost, and it is the number that
matters before real money -- a config selected on the full history cannot be
evaluated on the full history.

Selection pool is the FULL Breakout timeframe grid (7 symbols x 9 OR/scan
pairs), losers included. Restricting the pool to configs already known to have
finished positive would leak the answer into every fold.

Each config's trade list is computed once and then sliced per fold, so the
folds cost no extra backtests.

Usage:
    python -m scripts.walk_forward_selection
    python -m scripts.walk_forward_selection --train-months 24 --val-months 6
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
from scripts.consistency_analysis import _cached_load_m1, agg, max_drawdown_r
from scripts.two_strategy_symbol_sweep import DATA_DIR, SYMBOLS, recent_spread

orb_mod.load_m1 = _cached_load_m1

GRID = [(5, 1), (5, 5), (15, 1), (15, 5), (30, 1), (30, 5), (30, 15), (60, 5), (60, 15)]
TP_R = 4.0
MIN_TRAIN_TRADES = 30      # az nümunəli xanalar train-de "qalib" cixmasin


def month_add(d: date, months: int) -> date:
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    return date(y, m + 1, 1)


def build_all() -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    total = len(SYMBOLS) * len(GRID)
    i = 0
    for sym in SYMBOLS:
        csv = DATA_DIR / f"{sym}_M1.csv"
        if not csv.exists():
            continue
        sp = recent_spread(csv)
        for or_m, scan_m in GRID:
            i += 1
            label = f"{sym} {or_m}m/M{scan_m}"
            tr = orb_mod.run_backtest(str(csv), "full", sp, TP_R, "long",
                                      or_minutes=or_m, scan_minutes=scan_m)
            out[label] = [(date.fromisoformat(str(t.day)[:10]), t.r_multiple) for t in tr]
            print(f"  [{i}/{total}] {label} n={len(tr)}", file=sys.stderr, flush=True)
    return out


def window(trades, start: date, end: date):
    return [r for d, r in trades if start <= d < end]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-months", type=int, default=24)
    ap.add_argument("--val-months", type=int, default=6)
    args = ap.parse_args()

    allt = build_all()
    first = min(d for tr in allt.values() for d, _ in tr)
    last = max(d for tr in allt.values() for d, _ in tr)

    folds = []
    tr_start = date(first.year, first.month, 1)
    while True:
        tr_end = month_add(tr_start, args.train_months)
        va_end = month_add(tr_end, args.val_months)
        if va_end > last:
            break
        folds.append((tr_start, tr_end, va_end))
        tr_start = month_add(tr_start, args.val_months)

    print("=" * 120)
    print(f"WALK-FORWARD SECIM TESTI  --  train {args.train_months} ay / validation "
          f"{args.val_months} ay, {len(folds)} fold")
    print(f"Secim hovuzu: {len(allt)} konfiqurasiya (butun grid, uduzanlar daxil)")
    print("=" * 120)
    print(f"{'FOLD':>4} {'TRAIN':>21} {'VALIDATION':>21}  {'SECILEN (train-e gore)':28} "
          f"{'train PF':>9} {'val PF':>8} {'val netR':>9}")
    print("-" * 120)

    val_pfs, val_nets, wins, top3_nets = [], [], 0, []
    for i, (a, b, c) in enumerate(folds, 1):
        ranked = []
        for label, tr in allt.items():
            rs = window(tr, a, b)
            if len(rs) < MIN_TRAIN_TRADES:
                continue
            n, wr, pf, netr = agg(rs)
            ranked.append((pf, label))
        if not ranked:
            continue
        ranked.sort(reverse=True)
        pick_pf, pick = ranked[0]

        vn, vwr, vpf, vnet = agg(window(allt[pick], b, c))
        val_pfs.append(vpf if vpf != float("inf") else 3.0)
        val_nets.append(vnet)
        wins += 1 if vnet > 0 else 0

        t3 = [lab for _, lab in ranked[:3]]
        t3net = sum(agg(window(allt[l], b, c))[3] for l in t3) / len(t3)
        top3_nets.append(t3net)

        print(f"{i:>4} {a}..{b}  {b}..{c}  {pick:28} {pick_pf:>9.3f} "
              f"{vpf:>8.3f} {vnet:>+9.1f}")

    n = len(val_nets)
    print("-" * 120)
    print(f"\nTOP-1 SECIM:  musbet validation fold: {wins}/{n} ({wins/n*100:.0f}%)   "
          f"orta val netR: {sum(val_nets)/n:+.1f}R   cemi: {sum(val_nets):+.1f}R")
    print(f"TOP-3 ORTALAMA (diversifikasiya): orta val netR {sum(top3_nets)/n:+.1f}R   "
          f"cemi {sum(top3_nets):+.1f}R")

    print("\n" + "=" * 120)
    print("SABITLIK -- adi namizedlerin fold-be-fold validation netR-i")
    print("=" * 120)
    named = ["XAUUSD 60m/M15", "XAUUSD 15m/M5", "XAUUSD 15m/M1", "GER40 30m/M1",
             "SPX500 60m/M5", "NDX100 5m/M1"]
    hdr = " ".join(f"F{i:<4}" for i in range(1, len(folds) + 1))
    print(f"{'KONFIQURASIYA':18s} {hdr} {'musbet':>8} {'cemi':>8}")
    print("-" * 120)
    for label in named:
        if label not in allt:
            continue
        cells, pos, tot = [], 0, 0.0
        for a, b, c in folds:
            _, _, _, netr = agg(window(allt[label], b, c))
            cells.append(f"{netr:>+5.0f}")
            pos += 1 if netr > 0 else 0
            tot += netr
        print(f"{label:18s} " + " ".join(cells) + f" {pos}/{len(folds):<6} {tot:>+8.1f}")


if __name__ == "__main__":
    main()
