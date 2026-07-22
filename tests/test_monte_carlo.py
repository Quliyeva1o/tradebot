"""Unit tests for research/monte_carlo.py (MonteCarloSimulator).

Bug #75: the noise_cost formula previously read
`noise_pips * pip_size * pos_size * 100000.0`, hardcoding an FX-standard-lot
(100,000 units/lot) unit-conversion constant. This did not match how this
codebase's own SimplePositionSizer actually denominates position_size --
already "P&L dollars per unit price move" by construction, matching
backtest/engine.py's own gross_pnl = (exit_price - entry_price) * pos_size
convention with no separate lot-size multiplier. The bug was NOT
instrument-specific: verified empirically (Sprint 6b/Bug #75 investigation)
against both USTEC (an index CFD) and real EURUSD continuation-strategy
trades before the fix -- both produced worst-case per-trade noise costs of
hundreds of thousands of dollars against a $10k account, i.e. the identical
100% risk-of-ruin / -initial_balance "ruin" artifact, using the tool's own
default pip_size in the FX case. There was no real (non-synthetic) case
where the old constant was ever correct -- the fix removes it outright
rather than adding a per-instrument scale parameter.
"""

import random
from datetime import datetime

import pytest

from backtest.models import BacktestResult, BacktestTrade, TradeResult
from core.models import SignalDirection
from research.monte_carlo import MonteCarloSimulator


def _trade(pnl: float, position_size: float) -> BacktestTrade:
    return BacktestTrade(
        entry_time=datetime(2026, 1, 1),
        exit_time=datetime(2026, 1, 1),
        direction=SignalDirection.BUY,
        entry_price=100.0,
        exit_price=101.0,
        stop_loss=99.0,
        take_profit=101.0,
        result=TradeResult.WIN if pnl > 0 else TradeResult.LOSS,
        pnl=pnl,
        r_multiple=pnl / 100.0,
        position_size=position_size,
    )


class TestBug75InstrumentScaleFix:
    """Reproduces the exact pre-fix bug scenarios and confirms neither occurs anymore."""

    def test_ustec_scale_position_size_no_longer_produces_false_ruin(self) -> None:
        """The exact scenario Sprint 6b found.

        USTEC-scale position_size (~3.24 avg, matching SimplePositionSizer's real
        output for this instrument at 1% risk on a $10k account) combined with an
        instrument-appropriate pip_size (1.0, ~1 USTEC point) previously produced
        worst-case noise_cost of $420k-$650k per trade against a $10k account --
        guaranteed, instant ruin every trial.
        """
        trades = tuple(_trade(pnl=3.58, position_size=3.24) for _ in range(106))
        result = BacktestResult(
            trades=trades,
            total_profit=379.24,
            win_rate=0.3585,
            max_drawdown=0.1008,
            profit_factor=1.0510,
            initial_balance=10_000.0,
            final_balance=10_379.24,
        )

        summary = MonteCarloSimulator(n=200).run(result, pip_size=1.0)

        assert summary["risk_of_ruin"] < 100.0
        assert summary["expected_return"] > -10_000.0
        assert summary["confidence_interval_95"] != (0.0, 0.0)
        assert summary["worst_drawdown"] < 1.0

    def test_real_eurusd_scale_position_size_also_previously_broke(self) -> None:
        """Confirms the bug was NOT USTEC-specific.

        Real continuation-strategy FX position sizes (tens of thousands of
        currency units, per SimplePositionSizer) combined with the tool's own
        DEFAULT pip_size (0.0001) also produced worst-case noise_cost of
        $600k-$1.1M per trade before the fix -- the constant was always wrong,
        not just wrong for non-FX instruments.
        """
        trades = tuple(_trade(pnl=-107.0, position_size=68_215.84) for _ in range(3))
        result = BacktestResult(
            trades=trades,
            total_profit=-320.98,
            win_rate=0.0,
            max_drawdown=0.03,
            profit_factor=0.0,
            initial_balance=10_000.0,
            final_balance=9_679.02,
        )

        summary = MonteCarloSimulator(n=200).run(result)  # default pip_size=0.0001

        assert summary["risk_of_ruin"] < 100.0
        assert summary["expected_return"] > -10_000.0
        assert summary["confidence_interval_95"] != (0.0, 0.0)

    def test_noise_cost_formula_has_no_extra_unit_conversion_constant(self) -> None:
        """Locks in the exact corrected formula.

        noise_cost = noise_pips * pip_size * pos_size, with no *100000.0 (or any
        other) multiplier -- a fully deterministic, hand-computed cross-check via
        a pinned random seed.
        """
        trade = _trade(pnl=50.0, position_size=10.0)
        result = BacktestResult(
            trades=(trade,),
            total_profit=50.0,
            win_rate=1.0,
            max_drawdown=0.0,
            profit_factor=999.0,
            initial_balance=10_000.0,
            final_balance=10_050.0,
        )

        random.seed(42)
        random.choices([trade], k=1)  # consumes the same RNG draw run() makes to resample
        expected_noise_pips = random.uniform(0.0, 1.5)
        expected_noise_cost = expected_noise_pips * 0.0001 * 10.0  # pip_size * pos_size, no *100000
        expected_return = 50.0 - expected_noise_cost

        random.seed(42)
        summary = MonteCarloSimulator(n=1).run(result)

        assert summary["expected_return"] == pytest.approx(expected_return)


class TestPreExistingBehaviorUnaffected:
    """Reproduces the one pre-existing MonteCarloSimulator test scenario.

    tests/test_research.py's existing test never exercised a real, non-zero
    position_size (its hand-built BacktestTrade leaves position_size at its
    dataclass default 0.0, which run() falls back to 0.1 for) -- so there was
    no real "previously correct" numeric baseline to preserve. This confirms
    its (weak, ruin-threshold-only) assertions still hold after the fix.
    """

    def test_single_synthetic_trade_with_unset_position_size_still_has_no_ruin(self) -> None:
        trade = BacktestTrade(
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            direction=SignalDirection.BUY,
            entry_price=1.1000,
            exit_price=1.1010,
            stop_loss=1.0990,
            take_profit=1.1020,
            result=TradeResult.WIN,
            pnl=100.0,
            r_multiple=2.0,
        )
        result = BacktestResult(
            trades=(trade,),
            total_profit=100.0,
            win_rate=1.0,
            max_drawdown=0.0,
            profit_factor=999.0,
            initial_balance=10_000.0,
            final_balance=10_100.0,
            account_blown=False,
        )

        summary = MonteCarloSimulator(n=10).run(result)

        assert summary["expected_return"] is not None
        assert summary["risk_of_ruin"] == 0.0


class TestNoTradesEdgeCase:
    """Unaffected by the fix -- the empty-trades short-circuit never reaches noise_cost."""

    def test_empty_trades_returns_zeroed_summary(self) -> None:
        result = BacktestResult(
            trades=(),
            total_profit=0.0,
            win_rate=0.0,
            max_drawdown=0.0,
            profit_factor=0.0,
            initial_balance=10_000.0,
            final_balance=10_000.0,
        )

        summary = MonteCarloSimulator(n=10).run(result)

        assert summary["expected_return"] == 0.0
        assert summary["risk_of_ruin"] == 0.0
