"""Does moving the stop to breakeven help? python -m ict_lab.be_test

A+ trades only, same fills and stops as the 2R book. Rules:
  be@x      stop to entry once price has gone +x R in favour
  t@N       stop to entry after N minutes in the trade IF the trade is in profit then
The move applies from the next bar. A stopped-at-breakeven trade scores 0R.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from ict_lab.core import Mirror, build_setups, simulate
from ict_lab.data import load_symbol
from ict_lab.strategies import CFGS

SYMS = ("XAUUSD", "US100", "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD")
CONFIGS = {  # name: (tp_r, be_at_r, be_after)
    "base 2R": (2.0, None, None),
    "be@0.5": (2.0, 0.5, None), "be@0.75": (2.0, 0.75, None), "be@1.0": (2.0, 1.0, None), "be@1.5": (2.0, 1.5, None),
    "t@30": (2.0, None, 30), "t@60": (2.0, None, 60), "t@120": (2.0, None, 120), "t@240": (2.0, None, 240),
    "be@1.0+t@120": (2.0, 1.0, 120),
    "base 3R": (3.0, None, None), "3R be@1.0": (3.0, 1.0, None), "3R be@1.5": (3.0, 1.5, None),
    "3R t@120": (3.0, None, 120), "3R be@1.0+t@120": (3.0, 1.0, 120),
}


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
                    if simulate(st, sym, "r2") in (None, "skip"):
                        continue
                    if st.grade == "A+":
                        r = {"sym": name, "strat": key, "day": day, "win": win}
                        for cn, (tp, be, ta) in CONFIGS.items():
                            out = simulate(st, sym, "r2", r_mult=tp, be_at_r=be, be_after=ta)
                            r[cn], r[cn + "|why"] = (out[0], out[3]) if isinstance(out, tuple) else (np.nan, "")
                        rows.append(r)
                    break
    return rows


def table(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for cn in CONFIGS:
        x = df[cn].dropna()
        be = (df[cn + "|why"] == "be").mean()
        out.append(dict(rule=cn, n=len(x), win=(x > 0).mean(), be_exit=be, net=x.sum(), avg=x.mean(),
                        t=x.mean() / x.std(ddof=1) * np.sqrt(len(x))))
    return pd.DataFrame(out).round(3)


if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=len(SYMS)) as ex:
        parts = list(ex.map(one, SYMS))
    df = pd.DataFrame([r for p in parts for r in p])
    df["date"] = pd.to_datetime(df.day, unit="D")
    df.to_csv("ict_lab/out/be_trades.csv", index=False)
    pd.set_option("display.width", 200)
    print("=== A+, all symbols, 2021-2026"); print(table(df).to_string(index=False))
    print("\n=== A+, last 12 months (2025-10 ->)"); print(table(df[df.date >= "2025-10-01"]).to_string(index=False))
