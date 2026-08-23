"""Mandatory Sprint 3 regression test.

Confirms extracting NasdaqMidlineSweepStrategy.evaluate()'s inline take-profit
arithmetic into strategy/risk_reward.py's calculate_take_profit() (a move,
not a rewrite) did not change the strategy's behavior on the previously
validated USTEC out-of-sample backtest.

Baseline, confirmed by running the exact command this test replicates
immediately BEFORE the Sprint 3 refactor (research/run_strategy_backtest.py
--strategy midline_sweep --data-file data/history/USTEC_M5.csv --timeframe M5
--params '{"body_multiplier": 1.5}' --split out_of_sample --split-ratio 0.7,
no --spread override so the CSV's real per-bar spread column is used, per
BacktestEngine._effective_spread()'s candle.spread > 0 precedence):
    total_trades = 106
    profit_factor = 1.0509977052755126 (~1.0510)
    net_profit = 379.23679407752854

Documented previously in walkthrough.md (Bug #24b/#54 sections) and
live_signal_check.py's DEFAULT_LOOKBACK_BARS/DEFAULT_BODY_MULTIPLIER comment.
Re-run identically AFTER the refactor: same 106/1.0509977052755126/379.24 --
see this sprint's deliverable summary. backtest/engine.py is untouched this
sprint (constraint); this test exists specifically to prove the SL/TP
extraction reproduced the exact same arithmetic.

If this ever drifts, STOP and investigate before proceeding -- do not adjust
this test to match a new number (see Sprint 3 task constraints).
"""

import pytest

from core.models import Timeframe
from research.run_strategy_backtest import run_backtest

DATA_FILE = "data/history/USTEC_M5.csv"


def test_midline_sweep_ustec_oos_regression() -> None:
    """Confirms the SL/TP-extraction refactor reproduced the exact pre-refactor result."""
    result = run_backtest(
        strategy_name="midline_sweep",
        data_file=DATA_FILE,
        timeframe=Timeframe.M5,
        params={"body_multiplier": 1.5},
        split="out_of_sample",
        split_ratio=0.7,
        initial_balance=10_000.0,
        spread=0.0002,
        commission=0.0,
        risk_per_trade=0.01,
    )

    assert result["total_trades"] == 106
    assert result["profit_factor"] == pytest.approx(1.0509977052755126, abs=1e-6)
    assert result["net_profit"] == pytest.approx(379.23679407752854, abs=1e-2)
