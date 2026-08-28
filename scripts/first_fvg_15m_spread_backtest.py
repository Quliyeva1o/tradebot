"""First FVG strategy (15m by default, any timeframe via --timeframe-minutes),
backtested with historical spread, for BOTH session anchors (00:00 NY and
09:30 NY), separately, over several windows.

Reuses the detection/simulation logic in scripts/nas100_first_fvg_15m_backtest.py
(the "first fvg 9:30 15m timeframe" strategy already in this repo, commit
005b05e) UNCHANGED -- find_first_fvg() and simulate_trade() are imported, not
reimplemented. This script only adds the two things that script's own
docstring explicitly flagged as not yet done:

  1. A configurable session anchor (00:00 OR 09:30 NY) instead of only 09:30.
  2. Spread cost, charged once per trade the same way
     scripts/robustness_analysis.py already does it:
         cost_r = spread_points / risk_distance
     subtracted from r_multiple, then pnl recomputed from the net R.

Spread value used: the FIXED 3.0-point NAS100 round-trip constant from
scripts/robustness_analysis.SPREAD_BY_SYMBOL, not the per-bar "spread"
column recorded in the raw CSV. That column is 0.0 for every bar before
2024 (checked: 2020-2023 mean/max are both exactly 0.0, real values only
start appearing in 2024) -- it reflects when the broker's feed started
recording live spread, not zero-cost history. Using it directly would make
2020-2023 trades look free-to-trade, which is worse than not modeling
spread at all. The flat constant is this repo's own existing convention for
"what does NAS100 actually cost to trade" and stays consistent across the
whole window.

Data source: for 5m and 15m, the broker's own NATIVE bars --
data/history/NAS100_M5.csv / NAS100_M15.csv, freshly pulled via
`python -m data.download_history --symbols NAS100 --timeframe M5/M15
--start 2020-01-01` (symbol is "NAS100" on this broker, not "USTEC" --
"USTEC" is not in this account's symbol list). Any OTHER --timeframe-minutes
value falls back to resampling data/history/NAS100_M1.csv locally.
data/history/USTEC_M15.csv was NOT used -- it only covers ~3 months, too
short for the 5y/1y windows requested.

All "last N" windows are anchored to the LAST bar in the dataset
(2026-08-26), not to today's calendar date, since that is the last point
for which this repo has data.

Usage:
    python -m scripts.first_fvg_15m_spread_backtest
    python -m scripts.first_fvg_15m_spread_backtest --timeframe-minutes 5
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.backtest_common import NY
from scripts.nas100_first_fvg_15m_backtest import (
    RISK_PCT,
    STARTING_BALANCE,
    TP_R,
    find_first_fvg,
    simulate_trade,
)
from scripts.robustness_analysis import SPREAD_BY_SYMBOL

BROKER_TZ = ZoneInfo("Europe/Bucharest")
RAW_M1_CSV = "data/history/NAS100_M1.csv"
SPREAD_POINTS = SPREAD_BY_SYMBOL["NAS100"]  # fixed round-trip cost, see module docstring

# Native-timeframe files pulled directly from MT5 via data/download_history.py
# (data.download_history --symbols NAS100 --timeframe M5/M15 --start 2020-01-01).
# Preferred over resampling M1 when available: these are the broker's own
# M5/M15 bars, not an approximation built from 1-minute aggregation.
NATIVE_CSV = {
    5: "data/history/NAS100_M5.csv",
    15: "data/history/NAS100_M15.csv",
}

SESSIONS = {
    "00:00": time(0, 0),
    "09:30": time(9, 30),
}


@dataclass
class NetTrade:
    day: str
    session: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    exit_reason: str
    spread: float
    risk: float
    r_multiple_gross: float
    r_multiple: float  # net of spread
    pnl_usd_gross: float
    pnl_usd: float  # net of spread


# --------------------------------------------------------------------------
# Data loading -- mirrors backtest_common.load_m1 but also keeps the spread
# column (load_m1 drops it), and resamples locally so backtest_common.py
# (shared by several other strategies) is not touched.
# --------------------------------------------------------------------------

def load_m1_with_spread(path: str) -> pd.DataFrame:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append(
                (ny_ts, float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row.get("spread") or 0.0))
            )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "spread"])
    return df.set_index("ts").sort_index()


def resample_tf(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    out = df.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "spread": "mean"}
    )
    return out.dropna(subset=["open"])


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------

def run_session(bars: pd.DataFrame, session_label: str, session_start: time, tp_r: float = TP_R) -> list[NetTrade]:
    trades: list[NetTrade] = []
    day_keys = pd.Series(bars.index.date, index=bars.index)
    for _day, day_df in bars.groupby(day_keys):
        session = day_df[(day_df.index.hour > session_start.hour) |
                          ((day_df.index.hour == session_start.hour) & (day_df.index.minute >= session_start.minute))]
        if session.empty or session.index[0].hour != session_start.hour or session.index[0].minute != session_start.minute:
            continue

        fvg = find_first_fvg(session)
        if fvg is None:
            continue
        trade = simulate_trade(session, fvg, long_only=False, tp_r=tp_r)
        if trade is None:
            continue

        recorded_spread = float(session.loc[trade.entry_time, "spread"]) if trade.entry_time in session.index else 0.0
        risk = abs(trade.entry_price - trade.stop)
        cost_r = (SPREAD_POINTS / risk) if risk > 0 else 0.0
        net_r = trade.r_multiple - cost_r
        net_pnl = net_r * STARTING_BALANCE * RISK_PCT

        trades.append(NetTrade(
            day=trade.day, session=session_label, direction=trade.direction,
            entry_time=trade.entry_time, entry_price=trade.entry_price, stop=trade.stop,
            exit_reason=trade.exit_reason, spread=round(recorded_spread, 3), risk=round(risk, 3),
            r_multiple_gross=round(trade.r_multiple, 4), r_multiple=round(net_r, 4),
            pnl_usd_gross=round(trade.pnl_usd, 2), pnl_usd=round(net_pnl, 2),
        ))
    return trades


def summarize(trades: list[NetTrade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None, "net_pnl": 0.0}
    wins = sum(1 for t in trades if t.r_multiple > 0)
    gp = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gl = abs(sum(t.r_multiple for t in trades if t.r_multiple <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    return {
        "trades": n,
        "win_rate": round(wins / n * 100, 1),
        "profit_factor": (round(pf, 3) if gl > 0 else None),
        "net_pnl": round(sum(t.pnl_usd for t in trades), 2),
    }


def write_trades_csv(trades: list[NetTrade], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "session", "direction", "entry_time", "entry_price", "stop", "exit_reason",
                    "recorded_spread_at_entry", "spread_cost_used", "risk", "r_multiple_gross", "r_multiple_net",
                    "pnl_usd_gross", "pnl_usd_net"])
        for t in trades:
            w.writerow([t.day, t.session, t.direction, t.entry_time, t.entry_price, t.stop, t.exit_reason,
                        t.spread, SPREAD_POINTS, t.risk, t.r_multiple_gross, t.r_multiple, t.pnl_usd_gross, t.pnl_usd])


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe-minutes", type=int, default=15)
    parser.add_argument("--tp-r", type=float, default=TP_R)
    args = parser.parse_args()
    tf = args.timeframe_minutes
    tf_tag = f"{tf}m"
    tp_r = args.tp_r
    tp_tag = f"{tp_r:g}R"

    if tf in NATIVE_CSV:
        bars = load_m1_with_spread(NATIVE_CSV[tf])
        source_desc = f"{NATIVE_CSV[tf]} (native {tf_tag}, no resampling)"
    else:
        m1 = load_m1_with_spread(RAW_M1_CSV)
        bars = resample_tf(m1, tf)
        source_desc = f"{RAW_M1_CSV} -> resampled {tf_tag}"
    last_ts = bars.index.max()
    end_date = last_ts.date()
    print(f"Data: {source_desc}, {bars.index.min()} .. {last_ts} (NY)")
    print(f"Anchor date for all 'last N' windows: {end_date}\n")

    windows = {
        "5y": pd.DateOffset(years=5),
        "1y": pd.DateOffset(years=1),
        "3mo": pd.DateOffset(months=3),
        "1mo": pd.DateOffset(months=1),
    }
    window_starts = {label: (pd.Timestamp(end_date) - off).date() for label, off in windows.items()}

    all_results: dict[str, dict] = {}
    for session_label, session_start in SESSIONS.items():
        trades = run_session(bars, session_label, session_start, tp_r=tp_r)
        write_trades_csv(trades, f"artifacts/first_fvg_{tf_tag}_{tp_tag}_spread_{session_label.replace(':', '')}_all.csv")

        by_day = {t.day: t for t in trades}  # at most one trade/day/session by construction

        print(f"{'=' * 70}\nSESSION {session_label} NY -- total trades in full history: {len(trades)}\n{'=' * 70}")

        window_summaries = {}
        for label in ["5y", "1y", "3mo", "1mo"]:
            wstart = window_starts[label]
            sub = [t for t in trades if wstart.isoformat() <= t.day <= end_date.isoformat()]
            s = summarize(sub)
            window_summaries[label] = s
            print(f"  [{label:>4}] {s}")

        # Month-by-month over the last 1y window
        y1_start = window_starts["1y"]
        y1_trades = [t for t in trades if y1_start.isoformat() <= t.day <= end_date.isoformat()]
        monthly = {}
        for t in y1_trades:
            ym = t.day[:7]
            monthly.setdefault(ym, []).append(t)
        monthly_summary = {ym: summarize(ts) for ym, ts in sorted(monthly.items())}
        print("  monthly (last 1y):")
        for ym, s in monthly_summary.items():
            print(f"    {ym}: {s}")

        # Day-by-day over the last 1mo window
        m1_start = window_starts["1mo"]
        m1_trades = [t for t in trades if m1_start.isoformat() <= t.day <= end_date.isoformat()]
        daily_summary = {t.day: summarize([t]) for t in sorted(m1_trades, key=lambda t: t.day)}
        print("  daily (last 1mo):")
        for d, s in daily_summary.items():
            print(f"    {d}: {s}")
        print()

        all_results[session_label] = {
            "windows": window_summaries,
            "monthly": monthly_summary,
            "daily": daily_summary,
            "window_starts": {k: v.isoformat() for k, v in window_starts.items()},
            "end_date": end_date.isoformat(),
        }

    import json
    out_json = f"artifacts/first_fvg_{tf_tag}_{tp_tag}_spread_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"Summary JSON written to {out_json}")


if __name__ == "__main__":
    main()
