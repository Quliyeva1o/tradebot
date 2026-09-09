"""Risk-and-consistency ranking of EVERY configuration that survived the sweep.

Built for the question "which of these could carry real money", which profit
factor alone cannot answer: PF says how much the winners outweigh the losers,
not whether the equity curve gets there smoothly. A config that is green in
80% of months with a 12R worst drawdown is a different instrument from one
with the same PF that spends nine months underwater.

Ranked on RISK first, return second:
  * share of months closed green        <- the user's own stated goal
  * longest run of red months           <- how long you sit underwater
  * max drawdown in R, peak-to-trough   <- worst case the account must absorb
  * worst single month
then PF / win rate / netR as the return side.

Also reports rolling windows (full/1y/3m/1m), every half-year bucket,
Aug-Nov seasonality across all years, and -- with --detail -- month-by-month
plus the last month day-by-day.

Trade count is shown but never ranked on: 55 trades that are green 80% of
months beat 1200 trades that are green 55% of them for this purpose.

Configs are read straight out of the sweep result files so nothing that tested
positive is silently dropped.

Usage:
    python -m scripts.consistency_analysis
    python -m scripts.consistency_analysis --detail "XAUUSD 60m"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date, time as dtime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
import scripts.silver_bullet_fvg_backtest as fvg_mod
import scripts.xauusd_orb_liquidity_sweep_backtest as sweep_mod
from scripts.backtest_common import NY, load_m1 as _raw_load_m1, resample
from scripts.two_strategy_symbol_sweep import DATA_DIR, recent_spread

# Each backtest module calls load_m1(path) itself, once per run; without this
# a 48-config sweep would re-parse the same 2.3M-row CSVs dozens of times.
_CSV_CACHE: dict[str, object] = {}


def _cached_load_m1(path: str):
    if path not in _CSV_CACHE:
        _CSV_CACHE[path] = _raw_load_m1(path)
    return _CSV_CACHE[path].copy()


orb_mod.load_m1 = _cached_load_m1
sweep_mod.load_m1 = _cached_load_m1
fvg_mod.load_m1 = _cached_load_m1

SWEEP_FILES = ["sweep_all.txt", "sweep_tf.txt", "sweep_sb.txt",
               "sweep_bo_tf2.txt", "sweep_bo_tf3.txt"]
TRIPLE = re.compile(r"n=\s*(\d+)\s+PF=\s*([\d.]+|inf)\s+R=\s*([+-][\d.]+)")
SYMBOLS = ("XAUUSD", "GER40", "NDX100", "DJI30", "SPX500", "JP225", "FTSE100")


def discover_configs() -> list[tuple[str, str, str, dict]]:
    """Rebuilds (label, kind, symbol, params) for every positive sweep row.

    Positive = PF > 1.0 on BOTH full history and the last year, the same bar
    the sweep report used.
    """
    tmp = Path(os.environ.get("TEMP", "."))
    out, seen = [], set()
    for fname in SWEEP_FILES:
        p = tmp / fname
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if not parts or parts[0] not in SYMBOLS:
                continue
            m = TRIPLE.findall(line)
            if len(m) != 3:
                continue
            try:
                pf_full, pf_1y = float(m[0][1]), float(m[1][1])
            except ValueError:
                continue
            if not (pf_full > 1.0 and pf_1y > 1.0):
                continue

            sym = parts[0]
            cfg = None
            if parts[1] == "Breakout":                       # "XAUUSD Breakout 3R"
                cfg = ("breakout", f"{sym} Breakout 15m/M1 {parts[2]}",
                       dict(or_minutes=15, scan_minutes=1, tp_r=float(parts[2].rstrip("Rr"))))
            elif parts[1] == "LiqSweep":                     # "XAUUSD LiqSweep 3R"
                cfg = ("liqsweep", f"{sym} LiqSweep 5m/10:00 {parts[2]}",
                       dict(bar_minutes=5, end="10:00", tp_r=float(parts[2].rstrip("Rr"))))
            elif ":" in parts[2] and "-" in parts[2]:        # "NDX100 15m 10:00-11:00" (FVG)
                cfg = ("fvg", f"{sym} FirstFVG {parts[1]} {parts[2]}",
                       dict(tf=int(parts[1].rstrip("m")), tp_r=2.0, win=tuple(parts[2].split("-"))))
            elif ":" in parts[2]:                            # "GER40 15m 11:00" (LiqSweep TF grid)
                cfg = ("liqsweep", f"{sym} LiqSweep {parts[1]}/{parts[2]} 2R",
                       dict(bar_minutes=int(parts[1].rstrip("m")), end=parts[2], tp_r=2.0))
            elif parts[1].endswith("m") and parts[2].endswith("m"):   # "XAUUSD 60m 15m" (Breakout TF grid)
                cfg = ("breakout", f"{sym} Breakout {parts[1]}/M{parts[2].rstrip('m')} 4R",
                       dict(or_minutes=int(parts[1].rstrip("m")),
                            scan_minutes=int(parts[2].rstrip("m")), tp_r=4.0))
            if cfg is None:
                continue
            kind, label, params = cfg
            if label in seen:
                continue
            seen.add(label)
            out.append((label, kind, sym, params))
    return sorted(out)


def trades_for(kind: str, symbol: str, p: dict) -> list[tuple[date, float]]:
    csv = DATA_DIR / f"{symbol}_M1.csv"
    sp = recent_spread(csv)
    if kind == "breakout":
        tr = orb_mod.run_backtest(str(csv), "full", sp, p["tp_r"], "long",
                                  or_minutes=p["or_minutes"], scan_minutes=p["scan_minutes"])
        return [(date.fromisoformat(str(t.day)[:10]), t.r_multiple) for t in tr]
    if kind == "liqsweep":
        h, m = map(int, p["end"].split(":"))
        tr, _ = sweep_mod.run_backtest(str(csv), tp_r=p["tp_r"], spread_points=sp,
                                       enable_breakout=False, bar_minutes=p["bar_minutes"],
                                       entry_window_end=dtime(h, m), entry_fill_mode="next_open")
        return [(date.fromisoformat(str(t.day)[:10]), t.r_multiple) for t in tr]
    bars = resample(_cached_load_m1(str(csv)), p["tf"])
    bars.index = bars.index.tz_convert(NY)
    sh, sm = map(int, p["win"][0].split(":"))
    eh, em = map(int, p["win"][1].split(":"))
    rows = fvg_mod.run_window(bars, dtime(sh, sm), dtime(eh, em), p["tp_r"], sp)
    return [(date.fromisoformat(str(r["day"])[:10]), r["r"]) for r in rows]


def agg(rs: list[float]):
    if not rs:
        return 0, 0.0, 0.0, 0.0
    wins = [r for r in rs if r > 0]
    loss = abs(sum(r for r in rs if r <= 0))
    pf = (sum(wins) / loss) if loss > 0 else float("inf")
    return len(rs), len(wins) / len(rs) * 100, pf, sum(rs)


def max_drawdown_r(trades: list[tuple[date, float]]) -> float:
    """Peak-to-trough of the cumulative R curve, in R."""
    peak = cum = dd = 0.0
    for _, r in trades:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def monthly(trades):
    out = defaultdict(list)
    for d, r in trades:
        out[(d.year, d.month)].append(r)
    return dict(sorted(out.items()))


def consistency(trades) -> dict:
    months = monthly(trades)
    if not months:
        return dict(months=0, green_pct=0.0, worst=0.0, best=0.0, max_red_streak=0)
    nets = [sum(rs) for rs in months.values()]
    streak = worst_streak = 0
    for v in nets:
        streak = streak + 1 if v <= 0 else 0
        worst_streak = max(worst_streak, streak)
    return dict(months=len(nets), green_pct=sum(1 for v in nets if v > 0) / len(nets) * 100,
                worst=min(nets), best=max(nets), max_red_streak=worst_streak)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default=None)
    args = ap.parse_args()

    configs = discover_configs()
    print(f"Tapilmis musbet konfiqurasiya: {len(configs)}", file=sys.stderr, flush=True)

    today = date.today()
    wins = [("1 il", today - timedelta(days=365)), ("3 ay", today - timedelta(days=91)),
            ("1 ay", today - timedelta(days=30))]

    rows = []
    for i, (label, kind, sym, p) in enumerate(configs, 1):
        tr = trades_for(kind, sym, p)
        n, wr, pf, netr = agg([r for _, r in tr])
        row = dict(label=label, trades=tr, n=n, wr=wr, pf=pf, netr=netr,
                   dd=max_drawdown_r(tr), cons=consistency(tr))
        for wname, since in wins:
            row[wname] = agg([r for d, r in tr if d >= since])
        rows.append(row)
        print(f"  [{i}/{len(configs)}] {label} (n={n})", file=sys.stderr, flush=True)

    # Risk-first: green months desc, then shorter red streaks, then shallower DD.
    rows.sort(key=lambda r: (-r["cons"]["green_pct"], r["cons"]["max_red_streak"], r["dd"]))

    print("=" * 126)
    print("RISK SIRALAMASI -- 1) yasil ay %  2) en uzun qirmizi seriya  3) max drawdown")
    print("=" * 126)
    print(f"{'KONFIQURASIYA':30s} {'yasil':>7} {'ay':>4} {'qirmizi':>8} {'maxDD':>8} "
          f"{'en pis ay':>10} {'PF':>6} {'WR':>6} {'netR':>8} {'n':>5}")
    print("-" * 126)
    for r in rows:
        c = r["cons"]
        print(f"{r['label']:30s} {c['green_pct']:>6.1f}% {c['months']:>4} {c['max_red_streak']:>6} ay "
              f"{r['dd']:>7.1f}R {c['worst']:>9.1f}R {r['pf']:>6.3f} {r['wr']:>5.1f}% "
              f"{r['netr']:>+8.1f} {r['n']:>5}")

    print("\n" + "=" * 126)
    print("ROLLING PENCERELER (PF / netR)")
    print("=" * 126)
    print(f"{'KONFIQURASIYA':30s} {'TAM':>16} {'SON 1 IL':>16} {'SON 3 AY':>16} {'SON 1 AY':>16}")
    print("-" * 126)
    for r in rows:
        cells = []
        for k in ("1 il", "3 ay", "1 ay"):
            n, wr, pf, nr = r[k]
            cells.append(f"{pf:>6.3f}/{nr:>+7.1f}" if n else f"{'n=0':>14}")
        print(f"{r['label']:30s} {r['pf']:>6.3f}/{r['netr']:>+7.1f}  " + "  ".join(cells))

    halves = sorted({(d.year, 1 if d.month <= 6 else 2) for r in rows for d, _ in r["trades"]})
    print("\n" + "=" * 126)
    print("YARIMILLIK netR -- edge zamanla saxlanirmi?")
    print("=" * 126)
    print(f"{'KONFIQURASIYA':30s} " + " ".join(f"{y%100:02d}H{h}" for y, h in halves))
    print("-" * 126)
    for r in rows:
        b = defaultdict(float)
        for d, v in r["trades"]:
            b[(d.year, 1 if d.month <= 6 else 2)] += v
        print(f"{r['label']:30s} " + " ".join(
            f"{b[h]:>+4.0f}" if h in b else f"{'-':>4}" for h in halves))

    print("\n" + "=" * 126)
    print("SEZONALLIQ -- butun illerin Avq/Sen/Okt/Noy aylari (netR / PF)")
    print("=" * 126)
    print(f"{'KONFIQURASIYA':30s} {'AVQUST':>15} {'SENTYABR':>15} {'OKTYABR':>15} {'NOYABR':>15}")
    print("-" * 126)
    for r in rows:
        cells = []
        for mo in (8, 9, 10, 11):
            n, wr, pf, nr = agg([v for d, v in r["trades"] if d.month == mo])
            cells.append(f"{nr:>+6.1f}/{pf:>4.2f}" if n else f"{'-':>11}")
        print(f"{r['label']:30s} " + " ".join(f"{c:>15}" for c in cells))

    if args.detail:
        t = next((r for r in rows if args.detail.lower() in r["label"].lower()), None)
        if not t:
            print(f"\n'{args.detail}' tapilmadi")
            return
        print("\n" + "=" * 126)
        print(f"DETAL: {t['label']}   (maxDD {t['dd']:.1f}R, yasil ay {t['cons']['green_pct']:.1f}%)")
        print("=" * 126)
        print("\nAY-BE-AY (son 24 ay):")
        for (y, mo), rs in list(monthly(t["trades"]).items())[-24:]:
            n, wr, pf, nr = agg(rs)
            print(f"  {y}-{mo:02d}  n={n:>3} WR={wr:>5.1f}% PF={pf:>6.3f} netR={nr:>+7.1f}  "
                  + ("+" if nr > 0 else "-") * min(int(abs(nr)), 40))
        print("\nSON 1 AY, GUN-BE-GUN:")
        recent = [(d, v) for d, v in t["trades"] if d >= today - timedelta(days=30)]
        for d, v in recent or []:
            print(f"  {d}  {v:>+7.2f}R  {'TP' if v > 0 else 'SL'}")
        if not recent:
            print("  (trade yoxdur)")


if __name__ == "__main__":
    main()
