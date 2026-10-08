"""Put the FundingPips and CFI paper books side by side: python -m ict_lab.report

One row per setup either feed traded. The same setup (same symbol, model, window, side, sweep level and signal
bar) is matched across the two books; where only one feed formed it, the other side reads "yoxdur".
Only A+ and the fixed 2R exit (the book tracks nothing else since 2026-10-08).
Writes ict_lab/paper_compare.csv and prints totals for each feed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# CFI only since 2026-10-08 (the FundingPips book is frozen); python -m ict_lab.report --both lines the two up again
import sys

BOOKS = {"FP": "ict_lab/live_state.json", "CFI": "ict_lab/live_state_cfi.json"}
if "--both" not in sys.argv:
    BOOKS = {"CFI": BOOKS["CFI"]}


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
    ids = {sid for c in closed.values() for sid in c if any(b["setups"].get(sid, {}).get("grade") == "A+" for b in books.values())}
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
            st = (r or {}).get("states", {}).get("r2")
            row[f"R_2R_{k}"] = cell("closed" if c else st, c["R_r2"] if c else None)
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
        p = c[c.grade == "A+"]
        print(f"{k}: A+ {len(p)} trade | 2R {p.R_r2.sum():+.1f}R | win {(p.R_r2 > 0).mean():.0%}")


if __name__ == "__main__":
    main()
