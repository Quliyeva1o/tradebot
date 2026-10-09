"""Collects every surviving strategy's trades into trades.csv (strat, broker, t, r). Run from C:\\tradebot."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"C:\tradebot")
SCR = Path(__file__).parent
rows = []


def add(strat, broker, t, r):
    d = pd.DataFrame({"t": pd.to_datetime(t, utc=True), "r": r})
    d["strat"], d["broker"] = strat, broker
    rows.append(d[["strat", "broker", "t", "r"]])


# --- ORB breakout (incl. wf XAUUSD), 1 trade file per bot ---
ORB = {"OrbBreakoutwf_XAUUSD_Paper": "ORB breakout wf XAUUSD", "OrbBreakout_DJI30_Paper": "ORB breakout DJI30",
       "OrbBreakout_GER40_Paper": "ORB breakout GER40", "OrbBreakout_JP225_Paper": "ORB breakout JP225",
       "OrbBreakout_NDX100_Paper": "ORB breakout NDX100", "OrbBreakout_SPX500_Paper": "ORB breakout SPX500"}
for broker in ("cfi", "fundingpips"):
    for task, name in ORB.items():
        f = SCR / "orb" / broker / f"{task}_trades.csv"
        if f.exists():
            d = pd.read_csv(f)
            add(name, broker, d["entry_time"], d["r"])
# --- ORB sweep ---
for broker in ("cfi", "fundingpips"):
    for task, name in {"OrbSweep_GER40_Paper": "ORB sweep GER40", "OrbSweep_XAUUSD_Paper": "ORB sweep XAUUSD"}.items():
        f = SCR / f"sweep_{broker}" / f"{task}_trades.csv"
        if f.exists():
            d = pd.read_csv(f)
            add(name, broker, d["entry_time"], d["r"])
# --- FVG window NDX ---
from scripts.fvg_window_envelope import backtest_trades
for broker in ("cfi", "fundingpips"):
    fr = backtest_trades(broker_name=broker)
    if not fr.empty:
        add("FVG window NDX100", broker, pd.to_datetime(fr["day"]) + pd.Timedelta(hours=15), fr["r_swap"])
# --- AMD gold ---
for broker, f in (("cfi", "amd_CFI_trades.csv"), ("fundingpips", "amd_FundingPips_trades.csv")):
    d = pd.read_csv(SCR / f)
    add("AMD gold", broker, pd.to_datetime(d["date"]) + pd.Timedelta(hours=15), d["R"])
# --- ICT A+ 2R book (CFI feed; ict_lab backtest to 2026-09) ---
d = pd.read_csv(Path(r"C:\tradebot\ict_lab\out\trades_all.csv"))
d = d[(d.grade == "A+") & (d.variant == "r2")]
add("ICT A+ 2R (all models)", "cfi", pd.to_datetime(d["date"]) + pd.Timedelta(hours=14), d["R"])
for sym, g in d.groupby("sym"):
    add(f"ICT A+ 2R {sym}", "cfi", pd.to_datetime(g["date"]) + pd.Timedelta(hours=14), g["R"])

out = pd.concat(rows)
out.to_csv(SCR / "trades.csv", index=False)
print(out.groupby(["strat", "broker"]).agg(n=("r", "size"), first=("t", "min"), last=("t", "max")).to_string())
