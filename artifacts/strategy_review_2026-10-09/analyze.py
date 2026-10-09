"""Reads trades.csv (strat, broker, t, r) and writes one markdown section per strategy x broker."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCR = Path(__file__).parent
df = pd.read_csv(SCR / "trades.csv")
df["t"] = pd.to_datetime(df["t"], utc=True)
# FundingPips gold history is on the wrong clock before 2025-03-09; drop it
bad = (df.broker == "fundingpips") & df.strat.str.contains("XAUUSD|AMD|gold") & (df.t < pd.Timestamp("2025-03-10", tz="UTC"))
df = df[~bad].sort_values("t").reset_index(drop=True)
END = pd.Timestamp("2026-10-09", tz="UTC")
SEASON = {12: "Qış", 1: "Qış", 2: "Qış", 3: "Yaz", 4: "Yaz", 5: "Yaz", 6: "Yay", 7: "Yay", 8: "Yay",
          9: "Payız", 10: "Payız", 11: "Payız"}
WD = ["B.e.", "Ç.a.", "Çər", "C.a.", "Cümə"]


def stat(x):
    if len(x) == 0:
        return dict(n=0, wr=np.nan, pf=np.nan, net=0.0, avg=np.nan, dd=0.0)
    w = x.r[x.r > 0].sum(); l = -x.r[x.r <= 0].sum()
    eq = x.r.cumsum()
    return dict(n=len(x), wr=100 * (x.r > 0).mean(), pf=(w / l if l > 0 else np.inf), net=x.r.sum(), avg=x.r.mean(),
                dd=float((eq.cummax().clip(lower=0) - eq).max()))


def fmt(s):
    if s["n"] == 0:
        return "—"
    pf = "∞" if np.isinf(s["pf"]) else f"{s['pf']:.2f}"
    return f"{s['n']} | {s['wr']:.0f}% | {pf} | {s['net']:+.1f} | {s['avg']:+.2f} | {s['dd']:.1f}"


def table(groups, title, minn=0):
    out = [f"\n**{title}**\n", "| Dövr | n | WR | PF | Net R | Orta R | DD |", "|---|---|---|---|---|---|---|"]
    for k, g in groups:
        if len(g) >= minn:
            out.append(f"| {k} | {fmt(stat(g))} |")
    return "\n".join(out)


def boot(r, draws=20000, seed=1):
    if len(r) < 10:
        return np.nan, (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    m = rng.choice(r, size=(draws, len(r)), replace=True).mean(axis=1)
    return float((m <= 0).mean()), (float(np.percentile(m, 5)), float(np.percentile(m, 95)))


def verdict(g):
    """Plain rules, written down before looking: n, sign in both halves, last 12m, bootstrap."""
    notes = []
    s = stat(g)
    if s["n"] < 30:
        return "YETERSİZ MƏLUMAT", [f"n={s['n']} < 30"]
    p, ci = boot(g.r.values)
    half = len(g) // 2
    a, b = stat(g.iloc[:half]), stat(g.iloc[half:])
    last = stat(g[g.t >= END - pd.Timedelta(days=365)])
    score = 0
    if s["pf"] >= 1.15: score += 1
    else: notes.append(f"ümumi PF {s['pf']:.2f} < 1.15")
    if p < 0.10: score += 1
    else: notes.append(f"bootstrap p={p:.2f} (təsadüfdən ayırmaq çətin)")
    if a["net"] > 0 and b["net"] > 0: score += 1
    else: notes.append(f"yarımlar: {a['net']:+.1f}R / {b['net']:+.1f}R — biri mənfi")
    if last["n"] >= 10 and last["net"] > 0 and last["pf"] >= 1.0: score += 1
    else: notes.append(f"son 12 ay {last['net']:+.1f}R (n={last['n']})")
    yrs = g.groupby(g.t.dt.year).r.sum()
    pos = (yrs > 0).mean()
    if pos >= 0.6: score += 1
    else: notes.append(f"müsbət illər {100*pos:.0f}%")
    top = g.r.nlargest(max(1, int(len(g) * 0.05))).sum()
    if s["net"] > 0 and top / s["net"] > 0.8:
        notes.append(f"qazancın {100*top/s['net']:.0f}%-i ən yaxşı 5% trade-dən")
        score -= 1
    lab = {5: "GÜVƏNMƏK OLAR", 4: "ŞƏRTİ GÜVƏN", 3: "ŞƏRTİ GÜVƏN"}.get(score, "GÜVƏNMƏ" if score <= 1 else "ZƏİF")
    if score == 2: lab = "ZƏİF"
    return lab, notes


md = []
summary = []
for (strat, broker), g in df.groupby(["strat", "broker"], sort=False):
    g = g.sort_values("t").reset_index(drop=True)
    s = stat(g)
    p, ci = boot(g.r.values)
    lab, notes = verdict(g)
    last12 = stat(g[g.t >= END - pd.Timedelta(days=365)])
    last6 = stat(g[g.t >= END - pd.Timedelta(days=183)])
    summary.append(dict(strat=strat, broker=broker, first=g.t.min().date(), n=s["n"], wr=s["wr"], pf=s["pf"], net=s["net"],
                        dd=s["dd"], p=p, pf12=last12["pf"], net12=last12["net"], n12=last12["n"], pf6=last6["pf"],
                        net6=last6["net"], verdict=lab))
    md.append(f"\n\n## {strat} — {broker}\n")
    md.append(f"Dövr: {g.t.min().date()} → {g.t.max().date()} · **{lab}**")
    md.append(f"\nÜmumi: n={s['n']}, WR {s['wr']:.1f}%, PF {s['pf']:.2f}, net {s['net']:+.1f}R, orta {s['avg']:+.3f}R, maks. DD {s['dd']:.1f}R. "
              f"Bootstrap: orta R ≤ 0 ehtimalı p={p:.3f}, 90% aralıq [{ci[0]:+.2f}, {ci[1]:+.2f}] R/trade.")
    if notes:
        md.append("\nZəif nöqtələr: " + "; ".join(notes) + ".")
    g["y"] = g.t.dt.year
    md.append(table(g.groupby("y"), "İl-il"))
    g["h"] = g.t.dt.year.astype(str) + " " + np.where(g.t.dt.month <= 6, "H1", "H2")
    md.append(table(g.groupby("h"), "Yarımil-yarımil"))
    g["s"] = [f"{t.year - (1 if t.month == 12 else 0) + (1 if t.month == 12 else 0)} {SEASON[t.month]}" for t in g.t]
    g["sy"] = [(t.year + 1 if t.month == 12 else t.year) for t in g.t]
    g["sl"] = g.t.dt.month.map(SEASON)
    md.append(table(g.groupby("sl"), "Fəsil üzrə (bütün illər birgə)"))
    rec = g[g.t >= END - pd.Timedelta(days=730)]
    if len(rec):
        rec = rec.assign(k=rec.sy.astype(str) + " " + rec.sl)
        order = sorted(rec.k.unique(), key=lambda k: (int(k.split()[0]), ["Qış", "Yaz", "Yay", "Payız"].index(k.split()[1])))
        md.append(table([(k, rec[rec.k == k]) for k in order], "Fəsil-fəsil (son 2 il)"))
    md.append(table(g.groupby(g.t.dt.month), "Ay-ayın təqvimi (yanvar=1 … dekabr=12, bütün illər)"))
    g["ym"] = g.t.dt.strftime("%Y-%m")
    last_months = sorted(g.ym.unique())[-18:]
    md.append(table([(m, g[g.ym == m]) for m in last_months], "Ay-ay (son 18 ay)"))
    g["wd"] = g.t.dt.weekday
    md.append(table([(WD[k], g[g.wd == k]) for k in range(5) if (g.wd == k).any()], "Həftənin günü"))
    # day by day: distribution, best/worst, streaks
    daily = g.groupby(g.t.dt.date).r.sum()
    srt = daily.sort_values()
    streak, cur = 0, 0
    for v in g.r:
        cur = cur + 1 if v <= 0 else 0
        streak = max(streak, cur)
    md.append(f"\n**Gün-gündən:** {len(daily)} ticarət günü, müsbət gün {100*(daily>0).mean():.0f}%, orta gün {daily.mean():+.2f}R. "
              f"Ən yaxşı 3 gün: {', '.join(f'{d} {v:+.1f}R' for d, v in srt.tail(3)[::-1].items())}. "
              f"Ən pis 3 gün: {', '.join(f'{d} {v:+.1f}R' for d, v in srt.head(3).items())}. "
              f"Ən uzun ardıcıl məğlubiyyət: {streak} trade.")
    # last 25 trading days
    md.append("Son 15 ticarət günü: " + ", ".join(f"{d.strftime('%m-%d')} {v:+.1f}" for d, v in daily.tail(15).items()))
    # rolling 12m PF snapshots
    snaps = []
    for q in range(8):
        e = END - pd.Timedelta(days=91 * q); b = e - pd.Timedelta(days=365)
        x = g[(g.t >= b) & (g.t < e)]
        if len(x) >= 8:
            sx = stat(x); snaps.append(f"{e.date()}: PF {sx['pf']:.2f} ({sx['net']:+.1f}R, n={sx['n']})")
    if snaps:
        md.append("\nSürüşən 12 aylıq pəncərələr (rüblük addım): " + " · ".join(snaps))

sm = pd.DataFrame(summary)
sm.to_csv(SCR / "summary.csv", index=False)
(SCR / "sections.md").write_text("\n".join(md), encoding="utf-8")
pd.set_option("display.width", 250)
print(sm.round(2).to_string())
