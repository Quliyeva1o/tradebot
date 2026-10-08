"""How many R? Fixed-R targets on the same fills and stops: python -m ict_lab.rmult

Backtest part: A+/A/B+ trades of the 11 models over 7 CFI symbols 2021-2026 (the entry, stop and fill are the
engine's; only the take-profit multiple changes). The trade picked per window is the one the 2R book picks.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from ict_lab.core import Mirror, build_setups, simulate
from ict_lab.data import load_symbol
from ict_lab.strategies import CFGS

KS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
SYMS = ("XAUUSD", "US100", "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD")


def one(name: str) -> list[dict]:
    sym = load_symbol(name)
    mir = Mirror(sym)
    rows = []
    for day in sym.tdays:
        lv = sym.levels(day)
        if lv is None or day < 18628:
            continue
        for key, cfg in CFGS.items():
            groups: dict[str, list] = {}
            for st in build_setups(sym, mir, cfg, day, lv):
                groups.setdefault(st.win, []).append(st)
            for win, cands in groups.items():
                for st in cands:
                    base = simulate(st, sym, "r2")
                    if base is None or base == "skip":
                        continue
                    r = {"sym": name, "strat": key, "grade": st.grade, "day": day, "win": win}
                    for k in KS:
                        out = simulate(st, sym, "r2", r_mult=k)
                        r[f"R{k}"] = out[0] if isinstance(out, tuple) else np.nan
                    rows.append(r)
                    break
    return rows


def table(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for k in KS:
        x = df[f"R{k}"].dropna()
        out.append(dict(R=k, n=len(x), win=(x > 0).mean(), breakeven_win=1 / (1 + k), net=x.sum(), avg=x.mean(),
                        t=x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 else np.nan))
    return pd.DataFrame(out).round(3)


if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=len(SYMS)) as ex:
        parts = list(ex.map(one, SYMS))
    df = pd.DataFrame([r for p in parts for r in p])
    df.to_csv("ict_lab/out/rmult_trades.csv", index=False)
    pd.set_option("display.width", 200)
    for g in ("A+", "A", "B+"):
        print(f"\n=== BACKTEST grade {g}, all symbols pooled")
        print(table(df[df.grade == g]).to_string(index=False))
    print("\n=== A+ by symbol: net R at each multiple")
    for s in SYMS:
        t = table(df[(df.grade == 'A+') & (df.sym == s)])
        print(s, " ".join(f"{r.R}:{r.net:+.0f}" for r in t.itertuples()), "n", int(t.n.iloc[0]))
