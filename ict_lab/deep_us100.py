"""Deep check of the one cell that passed: US100, Order Block, grade A+ (python -m ict_lab.deep_us100).

Rebuilds the setups, reproduces the stored trades, then re-simulates them with execution friction the
base engine does not charge: extra slippage on entry and on the stop, and a limit that must trade
THROUGH its price by `pen` points before it fills.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ict_lab.core import MIN_RISK_SPREADS, Mirror, build_setups
from ict_lab.data import load_symbol
from ict_lab.strategies import CFGS


def sim(st, sym, variant, slip=0.0, pen=0.0, floor_spread=0.0):
    """core.simulate with `slip` points worse on the fill and on a stop exit, `pen` points of
    penetration needed to fill a limit, and a minimum spread `floor_spread` charged on top."""
    m1 = sym.m1
    d = st.d
    a, e, x = st.a0, min(st.exp, st.end), st.end
    o, h, l, c = (v[a:x].tolist() for v in (m1.o, m1.h, m1.l, m1.c))
    sp = [max(v, floor_spread) for v in m1.sp[a:x].tolist()]
    n = len(o)
    ne = max(0, e - a)
    fill = fi = None
    for i in range(ne):
        if d == 1:
            if l[i] + sp[i] <= st.E - pen:
                fill, fi = min(st.E, o[i] + sp[i]) + slip, i
                break
            if c[i] < st.inval:
                return None
        else:
            if h[i] >= st.E + pen:
                fill, fi = max(st.E, o[i]) - slip, i
                break
            if c[i] + sp[i] > st.inval:
                return None
    if fill is None:
        return None
    SL = st.sl
    risk = abs(fill - SL)
    if risk < MIN_RISK_SPREADS * sp[fi] or (fill - SL) * d <= 0:
        return None
    if variant == "r2":
        tp = fill + d * 2 * risk
    else:
        tp = st.tp.get(variant)
        if tp is None or abs(tp - fill) < 2 * risk or (tp - fill) * d <= 0:
            return "skip"
    for i in range(fi, n):
        if d == 1:
            if l[i] <= SL:
                return (-(fill - SL + slip) / risk, risk)
            if h[i] >= tp:
                return ((tp - fill) / risk, risk)
        else:
            if h[i] + sp[i] >= SL:
                return (-(SL - fill + slip) / risk, risk)
            if l[i] + sp[i] <= tp:
                return ((fill - tp) / risk, risk)
    px = c[n - 1] if d == 1 else c[n - 1] + sp[n - 1]
    return ((px - fill) * d / risk, risk)


def collect(name="US100", key="order_block", grade="A+"):
    sym = load_symbol(name)
    mir = Mirror(sym)
    cfg = CFGS[key]
    out = []
    for day in sym.tdays:
        lv = sym.levels(day)
        if lv is None or day < 18628:
            continue
        groups: dict[str, list] = {}
        for st in build_setups(sym, mir, cfg, day, lv):
            groups.setdefault(st.win, []).append(st)
        for win, cands in groups.items():
            for variant in ("doc", "r2"):
                for st in cands:
                    r = sim(st, sym, variant)   # baseline, to pick the same setup the engine picks
                    if r is None or r == "skip":
                        continue
                    if st.grade == grade:
                        out.append((day, win, variant, st))
                    break
    return sym, out


if __name__ == "__main__":
    import pickle
    sym, setups = collect()
    print(len(setups), "A+ setups (doc+r2)")
    pickle.dump([(d, w, v, st) for d, w, v, st in setups], open("ict_lab/out/us100_ob_aplus_setups.pkl", "wb"))
