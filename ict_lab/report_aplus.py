"""A+ trades with levels, FundingPips and CFI side by side: python -m ict_lab.report_aplus -> ict_lab/paper_aplus.csv

Rebuilds each A+ setup from the feed's own M1 bars (the paper books keep results, not prices) and reads the fill and
targets off the same status() the live book uses.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import pandas as pd

import ict_lab.live as L
from ict_lab.core import Mirror, build_setups
from ict_lab.strategies import CFGS

BOOKS = {"FP": "ict_lab/live_state.json", "CFI": "ict_lab/live_state_cfi.json"}
if "--both" not in __import__("sys").argv:   # CFI only since 2026-10-08
    BOOKS = {"CFI": BOOKS["CFI"]}


def levels_for(feed: str, ids: set[str]) -> dict[str, dict]:
    L.FEED = feed.lower()
    path = L.FEEDS[L.FEED]["path"]
    assert (mt5.initialize(path=path) if path else mt5.initialize())
    out = {}
    try:
        syms = {i.split("|")[0] for i in ids}
        frames = {n: L.fetch(mt5, n) for n in sorted(syms | {L.SYMBOLS[s][1] for s in syms})}
        for name in syms:
            sym = L.make_sym(name, frames)
            mir = Mirror(sym)
            for day in sorted({int(i.split("|")[2]) for i in ids if i.startswith(name + "|")}):
                lv = sym.levels(day)
                for cfg in CFGS.values():
                    for st in build_setups(sym, mir, cfg, day, lv):
                        sid = L.setup_id(st)
                        if sid in ids:
                            res = {v: L.status(st, sym, v) for v in ("doc", "r2")}   # doc only to read the fill
                            out[sid] = dict(entry=st.E, sl=st.sl, fill=res["doc"].get("fill") or res["r2"].get("fill"),
                                            tp_doc=res["doc"].get("tp"), tp_2r=res["r2"].get("tp"),
                                            doc=res["doc"], r2=res["r2"])
    finally:
        mt5.shutdown()
    return out


def fmt(sym: str, v):
    if v is None or v != v:
        return ""
    return round(v, 2) if sym in ("XAUUSD", "NDX100") else round(v, 3) if sym == "USDJPY" else round(v, 5)


def main() -> None:
    books = {k: json.load(open(p, encoding="utf8")) for k, p in BOOKS.items()}
    ids = {sid for b in books.values() for sid, r in b["setups"].items() if r["grade"] == "A+"
           and any(c["id"] == sid for c in b["closed"])}
    lv = {k: levels_for(k, ids) for k in BOOKS}
    rows = []
    for sid in ids:
        rec = next(b["setups"][sid] for b in books.values() if sid in b["setups"])
        u = datetime.fromtimestamp(rec["sig_ts"], timezone.utc)
        row = dict(sig_time_Baku=(u + timedelta(hours=4)).strftime("%m-%d %H:%M"), sig_time_NY=(u - timedelta(hours=4)).strftime("%m-%d %H:%M"),
                   sym=rec["sym"], side="LONG" if rec["d"] == 1 else "SHORT", model=rec["strat"], window=rec["win"], _k=(rec["sig_ts"], sid))
        for k in BOOKS:
            x = lv[k].get(sid)
            closed = {c["id"]: c for c in books[k]["closed"]}.get(sid)
            s = rec["sym"]
            row.update({f"entry_{k}": fmt(s, x and x["entry"]), f"fill_{k}": fmt(s, x and x["fill"]), f"SL_{k}": fmt(s, x and x["sl"]),
                        f"TP_2R_{k}": fmt(s, x and x["tp_2r"]),
                        f"R_2R_{k}": round(closed["R_r2"], 2) if closed else "yoxdur"})
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("_k").drop(columns="_k").reset_index(drop=True)
    df.index += 1
    df.to_csv("ict_lab/paper_aplus.csv", index_label="no", encoding="utf-8-sig")
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print(df.to_string())


if __name__ == "__main__":
    main()
