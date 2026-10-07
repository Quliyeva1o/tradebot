"""Put the FundingPips and CFI paper books side by side: python -m ict_lab.report

One row per setup either feed traded. The same setup (same symbol, model, window, side, sweep level and signal
bar) is matched across the two books; where only one feed formed it, the other side reads "yoxdur".
Writes ict_lab/paper_compare.csv and prints per-grade totals for each feed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

BOOKS = {"FP": "ict_lab/live_state.json", "CFI": "ict_lab/live_state_cfi.json"}


def load(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf8")) if p.exists() else {"setups": {}, "closed": []}


def cell(state: str | None, r: float | None) -> object:
    if state == "closed":
        return round(r, 2) if r is not None else "TP yoxdur"
    return {"dead": "dolmadı", "pending": "gözləyir", "open": "açıq", "skip": "TP yoxdur", None: "yoxdur"}.get(state, state)


def main() -> None:
    books = {k: load(v) for k, v in BOOKS.items()}
    closed = {k: {c["id"]: c for c in b["closed"]} for k, b in books.items()}
    ids = set().union(*[set(c) for c in closed.values()])
    rows = []
    for sid in ids:
        rec = next(books[k]["setups"][sid] for k in books if sid in books[k]["setups"])
        u = datetime.fromtimestamp(rec["sig_ts"], timezone.utc)
        row = dict(sig_time_Baku=(u + timedelta(hours=4)).strftime("%m-%d %H:%M"),
                   sig_time_NY=(u + timedelta(hours=-4)).strftime("%m-%d %H:%M"),
                   sym=rec["sym"], side="LONG" if rec["d"] == 1 else "SHORT", model=rec["strat"], window=rec["win"], _k=rec["sig_ts"])
        for k, b in books.items():
            r = b["setups"].get(sid)
            c = closed[k].get(sid)
            row[f"grade_{k}"] = r["grade"] if r else "yoxdur"
            for var, key in (("doc", "R_doc"), ("2R", "R_r2")):
                st = (r or {}).get("states", {}).get("doc" if var == "doc" else "r2")
                row[f"R_{var}_{k}"] = cell("closed" if c else st, c[key] if c else None)
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("_k").drop(columns="_k").reset_index(drop=True)
    df.index += 1
    df.to_csv("ict_lab/paper_compare.csv", index_label="no", encoding="utf-8-sig")
    print(f"{len(df)} setups traded by at least one feed -> ict_lab/paper_compare.csv\n")
    for k, b in books.items():
        c = pd.DataFrame(b["closed"])
        if c.empty:
            print(k, "no closed trades")
            continue
        print(f"{k}: {len(c)} closed | doc {c.R_doc.sum():+.1f}R | 2R {c.R_r2.sum():+.1f}R")
        for g in ("A+", "A", "B+", "B"):
            p = c[c.grade == g]
            if len(p):
                print(f"   {g:>2}: {len(p):3d} trade | doc {p.R_doc.sum():+6.1f}R | 2R {p.R_r2.sum():+6.1f}R")


if __name__ == "__main__":
    main()
