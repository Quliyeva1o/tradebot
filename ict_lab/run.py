"""Run the ICT models over every symbol and save the trades: python -m ict_lab.run [strategy ...]"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from ict_lab.core import Mirror, build_setups, simulate
import os

from ict_lab.data import SYMBOLS, load_symbol
from ict_lab.strategies import CFGS, DEFAULT_VARIANTS, VARIANTS

OUT = Path("ict_lab/out")


def run_symbol(args):
    name, keys = args
    t0 = time.time()
    sym = load_symbol(name)
    mir = Mirror(sym)
    rows = []
    cand_count = {k: 0 for k in keys}
    for day in sym.tdays:
        lv = sym.levels(day)
        if lv is None or day * 1440 < 18628 * 1440:   # 2021-01-01
            continue
        for key in keys:
            cfg = CFGS[key]
            sets = build_setups(sym, mir, cfg, day, lv)
            cand_count[key] += len(sets)
            groups: dict[str, list] = {}
            for st in sets:
                groups.setdefault(st.win, []).append(st)
            for win, cands in groups.items():
                for variant in VARIANTS.get(key, DEFAULT_VARIANTS):
                    for st in cands:
                        r = simulate(st, sym, variant)
                        if r is None or r == "skip":
                            continue
                        R, fi, xi, why = r
                        rows.append(dict(strat=key, sym=name, day=day, win=win, variant=variant, d=st.d,
                                         kind=st.kind, R=R, why=why, grade=st.grade, score=st.score,
                                         hold=xi - fi, **st.feats))
                        break
    print(f"{name}: {len(rows)} trades, {sum(cand_count.values())} setups, {time.time() - t0:.0f}s", flush=True)
    return rows


def main(keys: list[str]) -> pd.DataFrame:
    OUT.mkdir(exist_ok=True)
    names = os.environ.get("ICT_SYMS", "").split(",") if os.environ.get("ICT_SYMS") else list(SYMBOLS)
    tag = os.environ.get("ICT_TAG", "")
    with ProcessPoolExecutor(max_workers=len(names)) as ex:
        parts = list(ex.map(run_symbol, [(s, keys) for s in names]))
    df = pd.DataFrame([r for p in parts for r in p])
    df["date"] = pd.to_datetime(df["day"], unit="D")
    df.to_csv(OUT / ("trades_" + ("all" if len(keys) == len(CFGS) else "_".join(keys)) + tag + ".csv"), index=False)
    return df


if __name__ == "__main__":
    main(sys.argv[1:] or list(CFGS))
