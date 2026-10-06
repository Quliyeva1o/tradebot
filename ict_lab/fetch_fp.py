"""Pull FundingPips M1 (bid OHLC + spread) for the ICT scan into data/history/ict_fp: python -m ict_lab.fetch_fp

Attaches to whatever terminal is running with mt5.initialize() (read-only: no login, no orders).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

OUT = Path("data/history/ict_fp")
SYMBOLS = ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "AUDUSD", "USDCHF", "XAUUSD", "NDX100"]


def fetch(sym: str, start: datetime, end: datetime) -> pd.DataFrame:
    parts = []
    t = start
    while t < end:
        t2 = min(t + timedelta(days=60), end)
        r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, t, t2)
        if r is not None and len(r):
            parts.append(pd.DataFrame(r))
        t = t2
    df = pd.concat(parts).drop_duplicates("time").sort_values("time")
    df["time"] = pd.to_datetime(df["time"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    df["volume"] = df["tick_volume"]
    # MT5 spread is in points; the replay files hold price units
    point = mt5.symbol_info(sym).point
    df["spread"] = df["spread"] * point
    return df[["time", "open", "high", "low", "close", "volume", "spread"]]


def main() -> None:
    assert mt5.initialize(), mt5.last_error()
    OUT.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=4)
    for sym in sys.argv[1:] or SYMBOLS:
        mt5.symbol_select(sym, True)
        df = fetch(sym, datetime(2021, 1, 1), end)
        df.to_csv(OUT / f"{sym}_M1.csv", index=False)
        print(sym, len(df), df["time"].iloc[0], df["time"].iloc[-1], flush=True)


if __name__ == "__main__":
    main()
