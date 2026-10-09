import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\tradebot")
from backtest.live_replay.market import load_m1, trim_to_real_m1
from strategy.gold_amd import GoldAmdConfig, build_series, find_entry, session_times

cfg = GoldAmdConfig(tp_r=2.0, min_fvg=0.5, spread_floor=0.15)   # the paper launcher's settings


def replay(folder, name, start):
    m1 = load_m1("XAUUSD_" if folder == "cfi" else "XAUUSD", Path(r"C:\tradebot\data\history") / folder)
    m1 = trim_to_real_m1(m1)
    ts, o, h, l, c, sp = m1.ts, m1.open, m1.high, m1.low, m1.close, m1.spread
    s = build_series(ts, o, h, l, c, sp, cfg)
    d0 = max(start, pd.Timestamp(ts[0], unit="s").date())
    d1 = pd.Timestamp(ts[-1], unit="s").date()
    rows = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            t = session_times(d, cfg)
            scan = find_entry(s, t, cfg)
            e = scan.entry
            if e is not None:
                risk = abs(e.entry - e.stop)
                tgt = e.entry + e.direction * cfg.tp_r * risk
                i = e.index
                j_end = int(np.searchsorted(ts, t.flat))      # first bar at/after the flat time
                R, why = None, "FLAT"
                for k in range(i, min(j_end, len(ts))):
                    if e.direction == 1:
                        if l[k] <= e.stop: R, why = -1.0, "SL"; break
                        if h[k] >= tgt: R, why = cfg.tp_r, "TP"; break
                    else:
                        if h[k] + sp[k] >= e.stop: R, why = -1.0, "SL"; break
                        if l[k] + sp[k] <= tgt: R, why = cfg.tp_r, "TP"; break
                if R is None:
                    k = min(j_end, len(ts)) - 1
                    px = c[k] if e.direction == 1 else c[k] + sp[k]
                    R = e.direction * (px - e.entry) / risk
                rows.append((d, e.direction, e.entry, e.stop, risk, R, why))
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["date", "dir", "entry", "stop", "risk", "R", "exit"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def stats(df):
    if df.empty:
        return "n=0"
    w, lo = df.R[df.R > 0].sum(), -df.R[df.R <= 0].sum()
    eq = df.R.cumsum(); dd = (eq.cummax() - eq).max()
    return f"n={len(df):3d} WR={100*(df.R>0).mean():5.1f}% PF={(w/lo if lo else float('inf')):5.2f} net={df.R.sum():+7.2f}R DD={max(dd,0):.2f}R avgR={df.R.mean():+.2f}"


out = Path(sys.argv[1])
for folder, name, start in [("cfi", "CFI", date(2024, 1, 1)), ("fundingpips", "FundingPips", date(2025, 3, 10))]:
    df = replay(folder, name, start)
    df.to_csv(out / f"amd_{name}_trades.csv", index=False)
    last = df.date.max()
    print(f"== {name}  {df.date.min().date()} .. {last.date()}")
    print("  all           ", stats(df))
    print("  longs         ", stats(df[df.dir == 1]))
    print("  shorts        ", stats(df[df.dir == -1]))
    print("  last 12m      ", stats(df[df.date >= pd.Timestamp("2025-10-09")]))
    print("  last 6m       ", stats(df[df.date >= pd.Timestamp("2026-04-09")]))
    print("  since 09-21   ", stats(df[df.date >= pd.Timestamp("2026-09-21")]))
    print("  since 10-06   ", stats(df[df.date >= pd.Timestamp("2026-10-06")]))
    print(df.tail(4).to_string(index=False))
