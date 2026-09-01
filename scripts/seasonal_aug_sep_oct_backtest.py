"""Seasonality check for SR+Bias and XAUUSD ORB: performance in August,
September, and October specifically, broken out by year across the full
backtest history, at each strategy's CURRENT live risk level (SR 0.2%,
ORB 2% -- see run_live_sr_bias_nas100_demo.bat / run_live_xauusd_orb_demo.bat).

Motivation: today is 2026-09-01 -- both live bots are about to trade
through exactly this Aug/Sep/Oct window for the first time with real
capital/demo orders. This is a descriptive seasonality read, not a new
statistical test; see the CAVEAT below on sample size per bucket.

CAVEAT: SR fires ~120 trades/year (~10/month), so an Aug/Sep/Oct slice is
~30 trades/year -- reasonably sized. ORB fires ~20 trades/year (~5/quarter),
so its Aug/Sep/Oct slice is ~5 trades/year -- read those numbers as "what
happened," not as an independently significant result.

Usage:
    python -m scripts.seasonal_aug_sep_oct_backtest
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(str(Path(__file__).parent.parent.resolve()))

SR_CSV = "artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv"
SR_REGIME_GATED_CSV = "artifacts/sr_daily_bias_live_class_regime_trades.csv"  # matches what SRBias_NAS100_Demo actually runs (--require-ranging-regime)
ORB_CSV = "artifacts/xauusd_orb_M15_nextopen_reversal_only_trades.csv"
ORB_SPREAD_POINTS = 0.39

SR_RISK_PCT = 0.002   # matches run_live_sr_bias_nas100_demo.bat (current live)
ORB_RISK_PCT = 0.02   # matches run_live_xauusd_orb_demo.bat (current live)
BASELINE = 100_000.0
SEASON_MONTHS = {8: "Avqust", 9: "Sentyabr", 10: "Oktyabr"}


def load_sr(path: str = SR_CSV) -> list[tuple[datetime, float]]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append((datetime.fromisoformat(row["entry_time"]), float(row["r_multiple_net"])))
    return out


def load_orb() -> list[tuple[datetime, float]]:
    out = []
    with open(ORB_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entry, stop = float(row["entry_price"]), float(row["stop"])
            risk_points = abs(entry - stop)
            cost_r = ORB_SPREAD_POINTS / risk_points if risk_points > 0 else 0.0
            r_net = float(row["r_multiple"]) - cost_r
            out.append((datetime.fromisoformat(row["entry_time"]), r_net))
    return out


def pf_of(rs: list[float]) -> float:
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def fmt_row(label: str, rs: list[float], risk_pct: float) -> str:
    n = len(rs)
    if n == 0:
        return f"{label:<14}{0:>4}{'--':>8}{'--':>8}{'--':>9}{'--':>15}"
    wr = sum(1 for r in rs if r > 0) / n * 100
    pf = pf_of(rs)
    net_r = sum(rs)
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    dollar = net_r * risk_pct * BASELINE
    return f"{label:<14}{n:>4}{wr:>7.1f}%{pf_str:>8}{net_r:>+9.2f}{dollar:>+15,.0f}"


def print_strategy(title: str, trades: list[tuple[datetime, float]], risk_pct: float) -> None:
    print(f"\n{'=' * 78}\n{title}  (risk={risk_pct*100:.2f}%, $ column = flat $100k baseline)\n{'=' * 78}")
    print(f"{'Dövr':<14}{'n':>4}{'WR':>8}{'PF':>8}{'netR':>9}{'$ nəticə':>15}")

    by_year_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ts, r in trades:
        if ts.month in SEASON_MONTHS:
            by_year_month[(ts.year, ts.month)].append(r)

    years = sorted({y for y, m in by_year_month})
    all_season_rs: list[float] = []
    for y in years:
        for m in sorted(SEASON_MONTHS):
            rs = by_year_month.get((y, m), [])
            all_season_rs.extend(rs)
            print(fmt_row(f"{y}-{SEASON_MONTHS[m]}", rs, risk_pct))

    print("-" * 78)
    print(fmt_row("CƏM (hamısı)", all_season_rs, risk_pct))

    print(f"\n  -- Ay üzrə (bütün illər birləşdirilib) --")
    for m, name in SEASON_MONTHS.items():
        month_rs = [r for (y, mm), rs in by_year_month.items() if mm == m for r in rs]
        print("  " + fmt_row(name, month_rs, risk_pct))


def main() -> None:
    sr = load_sr()
    sr_gated = load_sr(SR_REGIME_GATED_CSV)
    orb = load_orb()
    print(f"SR+Bias (ungated): n={len(sr)} total  ({min(t[0] for t in sr).date()} -> {max(t[0] for t in sr).date()})")
    print(f"SR+Bias (RANGING-gate ON, matches SRBias_NAS100_Demo): n={len(sr_gated)} total")
    print(f"XAUUSD ORB: n={len(orb)} total  ({min(t[0] for t in orb).date()} -> {max(t[0] for t in orb).date()})")

    print_strategy("SR+Bias, UNGATED -- Avqust/Sentyabr/Oktyabr, ilbəil", sr, SR_RISK_PCT)
    print_strategy("SR+Bias, RANGING-GATE ON (canlıda işləyən konfiqurasiya) -- Avqust/Sentyabr/Oktyabr, ilbəil", sr_gated, SR_RISK_PCT)
    print_strategy("XAUUSD ORB (M15/next-open) -- Avqust/Sentyabr/Oktyabr, ilbəil", orb, ORB_RISK_PCT)


if __name__ == "__main__":
    main()
