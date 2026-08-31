"""Walk-forward consistency check + Monte Carlo drawdown/risk-of-ruin stress
test for the two production configs (First FVG 09:30/15m/2R, SR+Bias NAS100
30m liquidity-TP), using their already-validated net-of-spread trade logs.

Deliberately does NOT route through backtest.engine.BacktestEngine /
research.walk_forward.WalkForwardRunner: that engine fills setups as a
pending LIMIT order on bar N+1 (see backtest/engine.py's pending_setup
logic), which is a different entry model than either strategy actually
uses (First FVG enters on first zone touch within the session; SR enters
at the signal bar's own close -- see SR_DAILY_BIAS_SPREAD_REPORT.md #0).
Plugging these strategies into that engine would silently test a THIRD,
unvalidated execution model instead of the one already verified against
the live classes this session. Folding/resampling the real trade logs
keeps the entry semantics that were actually verified.

Walk-forward here means rolling-window consistency, not train/optimize/
validate: both strategies use fixed, hand-specified parameters (no
in-sample fitting step), so the meaningful question is "does PF stay >=1.0
across every independent chronological slice," not "does an optimized
parameter generalize."

Monte Carlo reuses research/monte_carlo.py's exact method and metric
definitions (bootstrap trade resampling + uniform 0-1.5 unit adverse noise
per trade, risk-of-ruin = equity ever < 30% of initial, 95% CI on final
balance) so the numbers are directly comparable to that module's
convention, just computed from the R-multiple trade logs instead of a
BacktestResult.

Usage:
    python -m scripts.walk_forward_montecarlo
"""

from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass
from datetime import datetime

import numpy as np

INITIAL_BALANCE = 100_000.0
RISK_PER_TRADE_USD = 1_000.0  # 1% of $100k -- the convention used throughout this session's $ figures
N_FOLDS = 10
MC_TRIALS = 5000
NOISE_MIN_POINTS = 0.0
NOISE_MAX_POINTS = 1.5  # extra adverse slippage beyond the 3.0pt spread already baked into pnl_usd_net
RUIN_THRESHOLD_FRAC = 0.30  # matches research/monte_carlo.py's convention


@dataclass
class Trade:
    ts: datetime
    pnl: float          # dollars, net of spread, at the $1000/R convention
    r_net: float
    risk_points: float  # |entry - stop|, used to convert Monte Carlo noise (points) to dollars


TRADE_LOGS = {
    "First FVG (NAS100 09:30/15m/2R)": {
        "path": "artifacts/first_fvg_15m_2R_spread_0930_all.csv",
        "entry_col": "entry_time",
        "pnl_col": "pnl_usd_net",
        "r_col": "r_multiple_net",
        "entry_price_col": "entry_price",
        "stop_col": "stop",
    },
    "SR+Bias (NAS100 30m liquidity-TP)": {
        "path": "artifacts/sr_sweep_NAS100_30m_liquidity_trades.csv",
        "entry_col": "entry_time",
        "pnl_col": "pnl_usd_net",
        "r_col": "r_multiple_net",
        "entry_price_col": "entry_price",
        "stop_col": "stop",
    },
}


def load_trades(spec: dict) -> list[Trade]:
    trades = []
    with open(spec["path"], newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row[spec["entry_col"]])
            risk_points = abs(float(row[spec["entry_price_col"]]) - float(row[spec["stop_col"]]))
            trades.append(Trade(
                ts=ts,
                pnl=float(row[spec["pnl_col"]]),
                r_net=float(row[spec["r_col"]]),
                risk_points=risk_points,
            ))
    trades.sort(key=lambda t: t.ts)
    return trades


def pf_of(pnls: list[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    if gl == 0:
        return float("inf") if gp > 0 else 1.0
    return gp / gl


def walk_forward(trades: list[Trade], n_folds: int) -> list[dict]:
    """Splits the full trade history into n_folds equal CALENDAR-time slices
    (not equal trade-count slices, so a quiet fold is visible as a quiet
    fold, not hidden by padding it out with more trades)."""
    start, end = trades[0].ts, trades[-1].ts
    span = end - start
    folds = []
    for k in range(n_folds):
        fold_start = start + span * (k / n_folds)
        fold_end = start + span * ((k + 1) / n_folds)
        sub = [t for t in trades if fold_start <= t.ts < fold_end] if k < n_folds - 1 else \
              [t for t in trades if fold_start <= t.ts <= fold_end]
        pnls = [t.r_net for t in sub]
        wins = sum(1 for r in pnls if r > 0)
        folds.append({
            "fold": k + 1,
            "start": fold_start.date(),
            "end": fold_end.date(),
            "n": len(sub),
            "win_rate": (wins / len(sub) * 100) if sub else 0.0,
            "pf": pf_of(pnls) if sub else 0.0,
            "net_r": sum(pnls),
        })
    return folds


def monte_carlo(trades: list[Trade], n_trials: int, fixed_fractional: bool, risk_pct: float = 0.01) -> dict:
    """Two distinct sizing models, both starting from the same resampled
    trade sequence + adverse-noise draw:

    fixed_fractional=False: research/monte_carlo.py's own convention -- every
    resampled trade risks the SAME dollar amount (RISK_PER_TRADE_USD)
    regardless of the running balance. This is a legitimate stress test (a
    fixed-size bet sequence hitting a bad streak) but does NOT match how the
    live bots actually size (--risk-per-trade-pct), so it overstates ruin
    risk during a drawdown: a real fixed-% bot automatically bets smaller as
    balance falls, this model keeps betting the original size.

    fixed_fractional=True: risks risk_pct of the CURRENT (compounding)
    balance every trade, exactly matching how the live bots size positions.
    This is the realistic model for "what would actually happen to this
    account."
    """
    final_balances = []
    max_drawdowns = []
    ruin_count = 0
    new_high_count = 0

    rng = random.Random(42)

    for _ in range(n_trials):
        resampled = rng.choices(trades, k=len(trades))
        balance = INITIAL_BALANCE
        peak = INITIAL_BALANCE
        max_dd = 0.0
        min_balance = INITIAL_BALANCE

        for t in resampled:
            noise_points = rng.uniform(NOISE_MIN_POINTS, NOISE_MAX_POINTS)
            if fixed_fractional:
                risk_amount = balance * risk_pct
                pos_size = risk_amount / t.risk_points if t.risk_points > 0 else 0.0
                pnl = t.r_net * risk_amount - noise_points * pos_size
            else:
                pos_size = RISK_PER_TRADE_USD / t.risk_points if t.risk_points > 0 else 0.0
                pnl = t.pnl - noise_points * pos_size
            balance = max(0.0, balance + pnl)
            peak = max(peak, balance)
            min_balance = min(min_balance, balance)
            dd = (peak - balance) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        final_balances.append(balance)
        max_drawdowns.append(max_dd)
        if min_balance < RUIN_THRESHOLD_FRAC * INITIAL_BALANCE:
            ruin_count += 1
        if balance >= peak and balance > INITIAL_BALANCE:
            new_high_count += 1

    return {
        "expected_return": statistics.mean(final_balances) - INITIAL_BALANCE,
        "median_final_balance": statistics.median(final_balances),
        "worst_drawdown": max(max_drawdowns),
        "median_drawdown": statistics.median(max_drawdowns),
        "ci95_final_balance": (
            float(np.percentile(final_balances, 2.5)),
            float(np.percentile(final_balances, 97.5)),
        ),
        "risk_of_ruin_pct": ruin_count / n_trials * 100,
        "prob_new_high_pct": new_high_count / n_trials * 100,
    }


def main() -> None:
    for label, spec in TRADE_LOGS.items():
        trades = load_trades(spec)
        print(f"\n{'=' * 78}\n{label}  (n={len(trades)}, {trades[0].ts.date()} -> {trades[-1].ts.date()})\n{'=' * 78}")

        print(f"\n-- Walk-forward: {N_FOLDS} equal calendar-time folds --")
        folds = walk_forward(trades, N_FOLDS)
        passing = 0
        for f in folds:
            tag = "OK " if f["pf"] >= 1.0 else "FAIL"
            if f["pf"] >= 1.0:
                passing += 1
            print(f"  [{tag}] Fold {f['fold']:2d} {f['start']} -> {f['end']}: "
                  f"n={f['n']:4d}  WR={f['win_rate']:5.1f}%  PF={f['pf']:.3f}  net_R={f['net_r']:+.2f}")
        print(f"  Folds with PF>=1.0: {passing}/{N_FOLDS} ({passing/N_FOLDS*100:.0f}%)")

        for model_label, is_ff, rp in [
            (f"FIXED $ per trade (research/monte_carlo.py convention, ${RISK_PER_TRADE_USD:.0f}/trade always)", False, None),
            ("FIXED-FRACTIONAL 1% of current balance (matches live bot sizing style)", True, 0.01),
            ("FIXED-FRACTIONAL 0.25% of current balance (actual live risk-per-trade-pct)", True, 0.0025),
        ]:
            print(f"\n-- Monte Carlo ({MC_TRIALS} trials, bootstrap resample + "
                  f"{NOISE_MIN_POINTS}-{NOISE_MAX_POINTS}pt extra adverse noise/trade) -- {model_label} --")
            mc = monte_carlo(trades, MC_TRIALS, fixed_fractional=is_ff, risk_pct=rp or 0.01)
            print(f"  Expected return:        ${mc['expected_return']:+,.0f} "
                  f"({mc['expected_return']/INITIAL_BALANCE*100:+.1f}%)")
            print(f"  Median final balance:   ${mc['median_final_balance']:,.0f}")
            print(f"  95% CI final balance:   ${mc['ci95_final_balance'][0]:,.0f} -> ${mc['ci95_final_balance'][1]:,.0f}")
            print(f"  Median max drawdown:    {mc['median_drawdown']*100:.1f}%")
            print(f"  Worst-case max drawdown (of {MC_TRIALS} trials): {mc['worst_drawdown']*100:.1f}%")
            print(f"  Risk of ruin (<{RUIN_THRESHOLD_FRAC*100:.0f}% of start): {mc['risk_of_ruin_pct']:.2f}%")
            print(f"  P(finish at new equity high): {mc['prob_new_high_pct']:.1f}%")


if __name__ == "__main__":
    main()
