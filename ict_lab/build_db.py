"""ict_lab/ict.db bazasını ict_spec.py-dan yenidən qurur (results cədvəlinə toxunmur)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ict_lab.ict_spec import (
    BIAS_RULE, CONCEPTS, GENERAL_RULES, GRADES, KILLZONES, STRATEGIES,
)

DB_PATH = Path(__file__).with_name("ict.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    key TEXT PRIMARY KEY, no INTEGER, name TEXT, summary TEXT, time TEXT, tf TEXT,
    steps TEXT, entry TEXT, sl TEXT, tp TEXT, invalid TEXT,
    status TEXT NOT NULL DEFAULT 'not_tested'
);
CREATE TABLE IF NOT EXISTS concepts (term TEXT PRIMARY KEY, meaning TEXT, usage TEXT);
CREATE TABLE IF NOT EXISTS rules (kind TEXT, key TEXT, text TEXT, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_key TEXT NOT NULL REFERENCES strategies(key),
    grade TEXT NOT NULL CHECK (grade IN ('A+','A','B+','B')),
    symbol TEXT NOT NULL, broker TEXT, period TEXT, variant TEXT,
    trades INTEGER, wins INTEGER, net_r REAL, pf REAL, max_dd_r REAL,
    spread_on INTEGER, notes TEXT, run_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def build() -> None:
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    for s in STRATEGIES:
        con.execute(
            "INSERT INTO strategies (key,no,name,summary,time,tf,steps,entry,sl,tp,invalid)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET no=excluded.no, name=excluded.name,"
            " summary=excluded.summary, time=excluded.time, tf=excluded.tf, steps=excluded.steps,"
            " entry=excluded.entry, sl=excluded.sl, tp=excluded.tp, invalid=excluded.invalid",
            (s["key"], s["no"], s["name"], s["summary"], s["time"], s["tf"],
             json.dumps(s["steps"], ensure_ascii=False), s["entry"], s["sl"], s["tp"], s["invalid"]),
        )
    con.executemany(
        "INSERT OR REPLACE INTO concepts VALUES (?,?,?)", CONCEPTS)
    rules = [("killzone", k, v) for k, v in KILLZONES.items()]
    rules.append(("bias", "daily", BIAS_RULE))
    rules += [("general", str(i + 1), t) for i, t in enumerate(GENERAL_RULES)]
    rules.append(("grades", "order", " > ".join(GRADES)))
    con.executemany("INSERT OR REPLACE INTO rules VALUES (?,?,?)", rules)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    con.close()
    print(f"{DB_PATH}: {n} strategiya")


if __name__ == "__main__":
    build()
