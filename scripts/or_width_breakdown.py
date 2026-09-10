"""Breaks the Opening-Range width decision down every way it can be sliced.

The live XAUUSD Breakout bot moved from a 15-minute opening range to 60 on
2026-09-09 because full history said 60m earned the same with less than half
the drawdown. The last YEAR disagrees: 15m nets more R over the same period at
a similar profit factor, simply by trading more often. Both statements are true
of the same data, which is exactly why one summary line is not enough to decide
on.

So this prints the whole thing -- full history, last year, last quarter, then
year by year, season by season and calendar month by calendar month, for every
OR width x R combination -- and puts `n` beside every single number, because a
profit factor over eleven trades is not a profit factor.

READ THE SAMPLE SIZE FIRST. Slicing 6.7 years of one symbol into seasons and
months means dozens of cells, and at that point some of them look impressive
by arithmetic alone. A cell under ~30 trades is marked; treat it as colour, not
evidence. The honest comparisons here are the full-history and last-year rows,
and even the last-year row is ~100-190 trades depending on the config.

Usage:
    python -m scripts.or_width_breakdown
    python -m scripts.or_width_breakdown --symbol XAUUSD --or-widths 15,60 --r 2,3,4
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

sys.stdout.reconfigure(encoding="utf-8")

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
from scripts.consistency_analysis import _cached_load_m1, agg, max_drawdown_r
from scripts.two_strategy_symbol_sweep import DATA_DIR, recent_spread

orb_mod.load_m1 = _cached_load_m1

THIN = 30          # below this many trades a cell is decoration, not evidence
SEASONS = {12: "Qis", 1: "Qis", 2: "Qis", 3: "Yaz", 4: "Yaz", 5: "Yaz",
           6: "Yay", 7: "Yay", 8: "Yay", 9: "Payiz", 10: "Payiz", 11: "Payiz"}
MONTH_AZ = ["Yan", "Fev", "Mar", "Apr", "May", "Iyn", "Iyl", "Avq", "Sen", "Okt", "Noy", "Dek"]


def cell(rows: list[float]) -> str:
    """n / PF / netR for one bucket, flagged when the sample is too thin."""
    if not rows:
        return f"{'-':>18s}"
    n, wr, pf, net = agg(rows)
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    mark = "*" if n < THIN else " "
    return f"{n:>4}{mark}{pf_s:>6}{net:>+7.1f}R"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--or-widths", default="15,60")
    ap.add_argument("--r", default="2,3,4")
    ap.add_argument("--scan", type=int, default=1)
    args = ap.parse_args()

    widths = [int(x) for x in args.or_widths.split(",")]
    rs = [float(x) for x in args.r.split(",")]

    csv = DATA_DIR / f"{args.symbol}_M1.csv"
    sp = recent_spread(csv)
    today = date.today()

    configs = {}
    for orm in widths:
        for r in rs:
            tr = orb_mod.run_backtest(str(csv), "full", sp, r, "long",
                                      or_minutes=orm, scan_minutes=args.scan)
            configs[(orm, r)] = [(date.fromisoformat(str(x.day)[:10]), x.r_multiple) for x in tr]
            print(f"  [{orm}m {r:g}R] n={len(tr)}", file=sys.stderr, flush=True)

    names = list(configs)
    hdr = "  ".join(f"{f'{o}m/{r:g}R':>18s}" for o, r in names)
    W = len(hdr) + 14

    def section(title: str) -> None:
        print("\n" + "=" * W)
        print(title)
        print("=" * W)
        print(f"{'':12s}  " + hdr)
        print(f"{'':12s}  " + "  ".join(f"{'n   PF   netR':>18s}" for _ in names))
        print("-" * W)

    def line(label: str, pick) -> None:
        print(f"{label:12s}  " + "  ".join(cell(pick(configs[k])) for k in names))

    print(f"\n{args.symbol} Breakout -- OR eni ve R uzre tam bolgu  "
          f"(skan M{args.scan}, spread {sp:.3f})")
    print(f"* = {THIN} islemden az, statistik cekisi yoxdur")

    # ---------------- headline windows ----------------
    section("1) ESAS PENCERELER")
    line("Tam tarixce", lambda t: [v for _, v in t])
    for label, days in (("Son 1 il", 365), ("Son 6 ay", 182), ("Son 3 ay", 91), ("Son 1 ay", 30)):
        since = today - timedelta(days=days)
        line(label, lambda t, s=since: [v for d, v in t if d >= s])

    print("-" * W)
    print(f"{'maxDD (tam)':12s}  " + "  ".join(
        f"{max_drawdown_r(configs[k]):>17.1f}R" for k in names))
    print(f"{'maxDD (1 il)':12s}  " + "  ".join(
        f"{max_drawdown_r([(d, v) for d, v in configs[k] if d >= today - timedelta(days=365)]):>17.1f}R"
        for k in names))

    # ---------------- year by year ----------------
    section("2) ILBEIL")
    years = sorted({d.year for t in configs.values() for d, _ in t})
    for y in years:
        line(str(y), lambda t, y=y: [v for d, v in t if d.year == y])

    # ---------------- season ----------------
    section("3) FESILBEFESIL (butun iller birlikde)")
    for s in ("Qis", "Yaz", "Yay", "Payiz"):
        line(s, lambda t, s=s: [v for d, v in t if SEASONS[d.month] == s])

    # ---------------- calendar month ----------------
    section("4) AYBAAY (butun iller birlikde)")
    for m in range(1, 13):
        line(MONTH_AZ[m - 1], lambda t, m=m: [v for d, v in t if d.month == m])

    # ---------------- rolling 12-month, to see the trend ----------------
    section("5) HER TEQVIM ILI UCUN SON 12 AY (trend gorunsun deye)")
    for y in years[:-1]:
        end = date(y + 1, 1, 1)
        start = date(y, 1, 1)
        line(f"{y}", lambda t, a=start, b=end: [v for d, v in t if a <= d < b])

    print("\n" + "=" * W)
    print("OXUNUS QAYDASI")
    print("=" * W)
    print("- Ustunde * olan xanalar 30 islemden azdir; fesil/ay bolgusunun coxu beledir.")
    print("- Ay ve fesil bolgusu 6.7 ili ~12-48 xanaya boler; bezileri sirf tesadufen")
    print("  gozel gorunecek. Bunlar konfiqurasiya secmek ucun deyil, kontekst ucundur.")
    print("- Qerar vermek ucun yalniz 'Tam tarixce' ve 'Son 1 il' setirleri, bir de")
    print("  maxDD sətirləri islenmelidir -- qalanlarinda numune yoxdur.")


if __name__ == "__main__":
    main()
