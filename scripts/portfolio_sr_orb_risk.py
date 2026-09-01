"""Combined-account simulation of SR+Bias (NAS100 30m liquidity-TP) and
XAUUSD 09:30 ORB + Liquidity-Sweep (M15, realistic next-open fill) running
SIMULTANEOUSLY on the same account.

Unlike scripts/portfolio_combined_risk.py (First FVG + SR, both NAS100,
sharing ONE position slot via real trade_manager exclusivity), these two
strategies trade DIFFERENT symbols (NAS100 vs XAUUSD) and do NOT share a
position slot in the real system -- run_live_*.py's _partition_positions()
only blocks same-symbol bots (see the "Position ownership between bots" fix,
SESSION_HANDOFF.md §2.5). Both can genuinely be open AT THE SAME TIME. So
the question here is different from the FVG+SR script's ("how many trades
get crowded out by a shared slot"): it's "what happens to ONE shared
account's drawdown/return when both bots size positions independently off
the SAME (compounding) balance, simultaneously" -- the "aggregate risk"
concern SESSION_HANDOFF.md §3.3 already named but never measured for a
concrete pair.

Event-driven balance model (not a simple sequential trade-by-trade update,
since positions genuinely overlap in time here): every trade contributes an
ENTRY event (snapshots its risk $ amount off the CURRENT shared balance at
that exact moment -- correctly ignoring any other position's still-
unrealized P&L, matching how PositionSizer reads account equity fresh at
signal time) and an EXIT event (applies that trade's R-multiple outcome to
the shared balance). All events across both strategies are processed in one
global chronological order; an exit and an entry landing at the exact same
timestamp process the exit first (capital frees up before new risk is
allocated).

Usage:
    python -m scripts.portfolio_sr_orb_risk
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.portfolio_combined_risk import load_sr_trades  # reuse unchanged

INITIAL_BALANCE = 100_000.0
SR_RISK_PCT = 0.0025      # matches run_live_sr_bias.bat's --risk-per-trade-pct
ORB_RISK_PCT = 0.005      # matches run_live_xauusd_orb.py's DEFAULT_RISK_PER_TRADE_PCT
ORB_SPREAD_POINTS = 0.39  # robustness_analysis.SPREAD_BY_SYMBOL["XAUUSD"]
ORB_CSV = "artifacts/xauusd_orb_M15_nextopen_reversal_only_trades.csv"


@dataclass
class PortfolioTrade:
    source: str
    entry_time: datetime
    exit_time: datetime
    r_net: float
    risk_pct: float


def load_orb_trades(risk_pct: float = ORB_RISK_PCT) -> list[PortfolioTrade]:
    """ORB_CSV is gross (spread_points=0.0 baked into the batch run that
    generated it); nets the same 0.39pt spread
    scripts/xauusd_orb_validation.py's own load_wfmc_trades() uses."""
    out: list[PortfolioTrade] = []
    with open(ORB_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entry, stop = float(row["entry_price"]), float(row["stop"])
            risk_points = abs(entry - stop)
            cost_r = ORB_SPREAD_POINTS / risk_points if risk_points > 0 else 0.0
            r_net = float(row["r_multiple"]) - cost_r
            out.append(PortfolioTrade(
                "ORB",
                datetime.fromisoformat(row["entry_time"]),
                datetime.fromisoformat(row["exit_time"]),
                r_net,
                risk_pct,
            ))
    return out


def load_sr_trades_tagged(risk_pct: float = SR_RISK_PCT) -> list[PortfolioTrade]:
    return [PortfolioTrade("SR", t.entry_time, t.exit_time, t.r_net, risk_pct) for t in load_sr_trades()]


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def simulate_solo(trades: list[PortfolioTrade]) -> dict:
    balance = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0.0
    for t in sorted(trades, key=lambda t: t.exit_time):
        balance += t.r_net * balance * t.risk_pct
        peak = max(peak, balance)
        dd = (peak - balance) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return {"final_balance": balance, "max_drawdown": max_dd, "return_pct": (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100}


@dataclass
class _Event:
    ts: datetime
    kind: str  # "entry" | "exit"
    trade: PortfolioTrade


def simulate_combined(all_trades: list[PortfolioTrade]) -> dict:
    events: list[_Event] = []
    for t in all_trades:
        events.append(_Event(t.entry_time, "entry", t))
        events.append(_Event(t.exit_time, "exit", t))
    events.sort(key=lambda e: (e.ts, 0 if e.kind == "exit" else 1))

    balance = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0.0
    concurrent_open = 0
    max_concurrent = 0
    pending_risk_amount: dict[int, float] = {}

    for ev in events:
        if ev.kind == "entry":
            pending_risk_amount[id(ev.trade)] = balance * ev.trade.risk_pct
            concurrent_open += 1
            max_concurrent = max(max_concurrent, concurrent_open)
        else:
            risk_amount = pending_risk_amount.pop(id(ev.trade))
            balance += ev.trade.r_net * risk_amount
            peak = max(peak, balance)
            dd = (peak - balance) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            concurrent_open -= 1

    return {
        "final_balance": balance, "max_drawdown": max_dd,
        "return_pct": (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100,
        "max_concurrent_open": max_concurrent,
    }


def monthly_correlation(sr: list[PortfolioTrade], orb: list[PortfolioTrade]) -> float:
    import pandas as pd

    def monthly_r(trades: list[PortfolioTrade]) -> "pd.Series":
        s = pd.Series({t.exit_time: t.r_net for t in trades})
        s.index = pd.to_datetime(s.index, utc=True)
        return s.groupby(s.index.to_period("M")).sum()

    sr_m, orb_m = monthly_r(sr), monthly_r(orb)
    both = pd.concat([sr_m.rename("sr"), orb_m.rename("orb")], axis=1).fillna(0.0)
    return float(both["sr"].corr(both["orb"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sr-risk-pct", type=float, default=SR_RISK_PCT, help="SR+Bias risk-per-trade, as a fraction (0.0025 = 0.25%)")
    parser.add_argument("--orb-risk-pct", type=float, default=ORB_RISK_PCT, help="XAUUSD ORB risk-per-trade, as a fraction (0.005 = 0.5%)")
    args = parser.parse_args()

    sr = load_sr_trades_tagged(args.sr_risk_pct)
    orb = load_orb_trades(args.orb_risk_pct)
    print(f"SR+Bias: n={len(sr)}  ({sr[0].entry_time.date()} -> {sr[-1].exit_time.date()})  risk={args.sr_risk_pct*100:.2f}%")
    print(f"XAUUSD ORB: n={len(orb)}  ({orb[0].entry_time.date()} -> {orb[-1].exit_time.date()})  risk={args.orb_risk_pct*100:.2f}%")

    print(f"\n{'=' * 78}\nSOLO (each alone, own $100k, own risk%)\n{'=' * 78}")
    for label, ts in [(f"SR+Bias ({args.sr_risk_pct*100:.2f}% risk)", sr), (f"XAUUSD ORB ({args.orb_risk_pct*100:.2f}% risk)", orb)]:
        rs = [t.r_net for t in ts]
        eq = simulate_solo(ts)
        print(f"  {label:24s}: n={len(ts):4d}  PF={pf_of(rs):.3f}  "
              f"return={eq['return_pct']:+.1f}%  max_DD={eq['max_drawdown']*100:.1f}%")

    print(f"\n{'=' * 78}\nCOMBINED (ONE shared $100k account, both trading simultaneously, own risk% each)\n{'=' * 78}")
    combined = simulate_combined(sr + orb)
    print(f"  Combined: return={combined['return_pct']:+.1f}%  max_DD={combined['max_drawdown']*100:.1f}%  "
          f"max_concurrent_open={combined['max_concurrent_open']}")
    naive_sum = simulate_solo(sr)["return_pct"] + simulate_solo(orb)["return_pct"]
    print(f"  (naive sum of both solo returns: {naive_sum:+.1f}% -- combined will differ because both "
          f"compound off ONE shared, simultaneously-moving balance, not two independent $100k accounts)")

    corr = monthly_correlation(sr, orb)
    print(f"\nMonthly R-multiple-sum correlation (SR vs ORB): {corr:+.3f}")


if __name__ == "__main__":
    main()
