"""Combined-account simulation of First FVG (NAS100 09:30/15m/2R) and
SR+Bias (NAS100 30m liquidity-TP) running SIMULTANEOUSLY on the same real
account, honoring the SAME mutual-exclusion rule the live bots actually
enforce: trade_manager.py rejects a new entry while a position is already
open on that symbol from EITHER bot (see run_live_first_fvg_15m.log's
"Skipping NAS100: 1 position(s) held by another strategy"). This replaces
SESSION_HANDOFF.md's older "3 bots size independently -> ~3x exposure"
worry, which predates this session's reconciliation down to 2 bots BOTH on
NAS100 -- that worry was about cross-SYMBOL bots (XAUUSD+NAS100, now
disabled), not the current same-symbol pair, which self-caps concurrent
exposure at 1x by construction. What was never checked: how much each
strategy's SOLO backtest result changes once trades that would have been
crowded out by the other bot's still-open position are actually dropped,
and what the resulting single combined equity curve looks like.

First FVG's own trade CSV doesn't carry exit_time (only entry data), so
this script recomputes it by replaying scripts.nas100_first_fvg_15m_backtest's
find_first_fvg()/simulate_trade() (UNCHANGED, same functions the validated
batch script already uses) instead of re-deriving the strategy logic.

Usage:
    python -m scripts.portfolio_combined_risk
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.first_fvg_15m_spread_backtest import (
    SPREAD_POINTS as FVG_SPREAD_POINTS,
    load_m1_with_spread,
    resample_tf,
)
from scripts.nas100_first_fvg_15m_backtest import find_first_fvg, simulate_trade

INITIAL_BALANCE = 100_000.0
RISK_PCT = 0.0025  # matches run_live_first_fvg_15m.bat / run_live_sr_bias.bat's --risk-per-trade-pct


@dataclass
class PortfolioTrade:
    source: str
    entry_time: datetime
    exit_time: datetime
    r_net: float
    risk_points: float


def load_fvg_trades_with_exit() -> list[PortfolioTrade]:
    """Replays the 09:30/15m session exactly like first_fvg_15m_spread_backtest.run_session(),
    but also keeps trade.exit_time (available on the underlying Trade object,
    just never written to the CSV)."""
    m1 = load_m1_with_spread("data/history/NAS100_M1.csv")
    bars = resample_tf(m1, 15)

    out: list[PortfolioTrade] = []
    day_keys = bars.index.date
    import pandas as pd
    for _day, day_df in bars.groupby(pd.Series(day_keys, index=bars.index)):
        session_start = time(9, 30)
        session = day_df[(day_df.index.hour > session_start.hour) |
                          ((day_df.index.hour == session_start.hour) & (day_df.index.minute >= session_start.minute))]
        if session.empty or session.index[0].hour != 9 or session.index[0].minute != 30:
            continue
        fvg = find_first_fvg(session)
        if fvg is None:
            continue
        trade = simulate_trade(session, fvg, long_only=False, tp_r=2.0)
        if trade is None:
            continue
        risk = abs(trade.entry_price - trade.stop)
        cost_r = (FVG_SPREAD_POINTS / risk) if risk > 0 else 0.0
        net_r = trade.r_multiple - cost_r
        out.append(PortfolioTrade("FVG", trade.entry_time, trade.exit_time, net_r, risk))
    return out


def load_sr_trades() -> list[PortfolioTrade]:
    out = []
    with open("artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            risk = abs(float(row["entry_price"]) - float(row["stop"]))
            out.append(PortfolioTrade(
                "SR",
                datetime.fromisoformat(row["entry_time"]),
                datetime.fromisoformat(row["exit_time"]),
                float(row["r_multiple_net"]),
                risk,
            ))
    return out


def merge_with_exclusivity(trades: list[PortfolioTrade]) -> tuple[list[PortfolioTrade], dict[str, int]]:
    """Chronological single-slot merge: a trade whose entry_time falls before
    the currently-open trade's exit_time is dropped, mirroring trade_manager's
    real 'position held by another strategy' rejection."""
    trades_sorted = sorted(trades, key=lambda t: t.entry_time)
    accepted: list[PortfolioTrade] = []
    dropped = {"FVG": 0, "SR": 0}
    open_until: datetime | None = None
    for t in trades_sorted:
        if open_until is not None and t.entry_time < open_until:
            dropped[t.source] += 1
            continue
        accepted.append(t)
        open_until = t.exit_time
    return accepted, dropped


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def simulate_equity(trades: list[PortfolioTrade]) -> dict:
    balance = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0.0
    for t in trades:
        risk_amount = balance * RISK_PCT
        pnl = t.r_net * risk_amount
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return {"final_balance": balance, "max_drawdown": max_dd, "return_pct": (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100}


def main() -> None:
    print("Replaying First FVG trades (with exit_time)...")
    fvg = load_fvg_trades_with_exit()
    print(f"  {len(fvg)} FVG trades")
    sr = load_sr_trades()
    print(f"  {len(sr)} SR trades")

    all_trades = fvg + sr
    accepted, dropped = merge_with_exclusivity(all_trades)

    print(f"\n{'=' * 78}\nSOLO (as if each bot were the only one trading NAS100)\n{'=' * 78}")
    for label, ts in [("First FVG", fvg), ("SR+Bias", sr)]:
        rs = [t.r_net for t in ts]
        eq = simulate_equity(ts)
        print(f"  {label:12s}: n={len(ts):4d}  PF={pf_of(rs):.3f}  "
              f"return={eq['return_pct']:+.1f}%  max_DD={eq['max_drawdown']*100:.1f}%")

    print(f"\n{'=' * 78}\nCOMBINED (single NAS100 position slot, real trade_manager exclusivity)\n{'=' * 78}")
    print(f"  Trades dropped due to the other bot already holding the position: "
          f"FVG lost {dropped['FVG']}/{len(fvg)}, SR lost {dropped['SR']}/{len(sr)}")
    rs = [t.r_net for t in accepted]
    eq = simulate_equity(accepted)
    print(f"  Combined: n={len(accepted)}  PF={pf_of(rs):.3f}  "
          f"return={eq['return_pct']:+.1f}%  max_DD={eq['max_drawdown']*100:.1f}%")
    print(f"  (vs. naively adding both solo returns: "
          f"{simulate_equity(fvg)['return_pct'] + simulate_equity(sr)['return_pct']:+.1f}%"
          f" -- NOT achievable since only one can be in the market at a time)")

    won_by = {"FVG": sum(1 for t in accepted if t.source == "FVG"), "SR": sum(1 for t in accepted if t.source == "SR")}
    print(f"  Of the {len(accepted)} trades that actually got the slot: {won_by['FVG']} FVG, {won_by['SR']} SR")


if __name__ == "__main__":
    main()
