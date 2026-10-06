"""Summarise ict_lab/out/trades_all.csv by strategy x grade and write the numbers into ict.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from ict_lab.build_db import DB_PATH, build
from ict_lab.strategies import CFGS, DEFAULT_VARIANTS, VARIANTS

OUT = Path("ict_lab/out")
GRADES = ["A+", "A", "B+", "B"]
SPLIT = pd.Timestamp("2024-01-01")


def stats(g: pd.DataFrame) -> dict:
    r = g.sort_values("date")["R"].to_numpy()
    n = len(r)
    if n == 0:
        return dict(n=0, wins=0, win=np.nan, net=0.0, avg=np.nan, pf=np.nan, t=np.nan, dd=0.0, h1=np.nan, h2=np.nan)
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    cum = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max())
    sd = r.std(ddof=1) if n > 1 else np.nan
    early = g[g["date"] < SPLIT]["R"]
    late = g[g["date"] >= SPLIT]["R"]
    return dict(n=n, wins=int((r > 0).sum()), win=(r > 0).mean(), net=float(r.sum()), avg=float(r.mean()),
                pf=float(gain / loss) if loss > 0 else np.inf, t=float(r.mean() / sd * np.sqrt(n)) if sd else np.nan,
                dd=dd, h1=float(early.mean()) if len(early) else np.nan, h2=float(late.mean()) if len(late) else np.nan)


def load() -> pd.DataFrame:
    df = pd.read_csv(OUT / "trades_all.csv", parse_dates=["date"])
    return df


def table(df: pd.DataFrame, key: str, variant: str) -> pd.DataFrame:
    sub = df[(df.strat == key) & (df.variant == variant)]
    rows = []
    for g in GRADES + ["ALL"]:
        part = sub if g == "ALL" else sub[sub.grade == g]
        rows.append(dict(grade=g, **stats(part)))
    return pd.DataFrame(rows)


def save_db(df: pd.DataFrame) -> None:
    build()
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM results")
    for key in CFGS:
        for variant in VARIANTS.get(key, DEFAULT_VARIANTS):
            sub = df[(df.strat == key) & (df.variant == variant)]
            for sym in ["ALL"] + sorted(df.sym.unique()):
                for g in GRADES:
                    part = sub[(sub.grade == g) & ((sub.sym == sym) | (sym == "ALL"))]
                    s = stats(part)
                    con.execute(
                        "INSERT INTO results (strategy_key,grade,symbol,broker,period,variant,trades,wins,net_r,pf,"
                        "max_dd_r,spread_on,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)",
                        (key, g, sym, "CFI", "2021-01..2026-09", variant, s["n"], s["wins"], s["net"],
                         None if not np.isfinite(s["pf"]) else s["pf"], s["dd"],
                         f"t={s['t']:.2f} avgR={s['avg']:.3f} early={s['h1']:.3f} late={s['h2']:.3f}"))
    con.commit()
    con.close()


if __name__ == "__main__":
    d = load()
    pd.set_option("display.width", 200)
    print(d.groupby(["strat", "grade"]).size().unstack()[GRADES])
    save_db(d)
