"""Tests for scripts/sweep_bos_portfolio.py -- the aggregation, not the rule.

The per-symbol backtest is covered by tests/test_liquidity_sweep_bos_backtest.py. What
matters here is that the portfolio arithmetic is honest: a drawdown measured peak to
trough, a Sharpe that counts the flat days, and an alignment that does not quietly drop
a symbol's non-trading days and flatter the correlations.
"""

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from scripts.liquidity_sweep_bos_backtest import SweepTrade
from scripts.sweep_bos_portfolio import (
    PORTFOLIO_CONFIG,
    align,
    daily_r,
    max_drawdown,
    profit_factor,
    sharpe,
)
from strategy.liquidity_sweep_bos import RetraceMode, StopMode, TargetMode


def _trade(day: date, r_net: float) -> SweepTrade:
    """A SweepTrade carrying only the fields the aggregation reads."""
    stamp = datetime(day.year, day.month, day.day)
    return SweepTrade(
        day=day, setup_id="x", direction="LONG", level_name="ONL", level_price=1.0,
        sweep_extreme=1.0, leg_high=2.0, target_name="ONH", entry_time=stamp, entry=1.0,
        stop=0.9, target=2.0, exit_time=stamp, exit_price=2.0, reason="TP", bars_held=1,
        r_gross=r_net, r_net=r_net,
    )


class TestConfigIsPreRegistered:
    """The whole exercise depends on this cell not being re-picked per symbol."""

    def test_it_is_the_cell_that_was_tested_out_of_sample(self) -> None:
        assert PORTFOLIO_CONFIG.retrace_mode is RetraceMode.EQUILIBRIUM
        assert PORTFOLIO_CONFIG.stop_mode is StopMode.SWEEP_EXTREME
        assert PORTFOLIO_CONFIG.target_mode is TargetMode.NEXT_LIQUIDITY


class TestDailyR:
    def test_trades_on_one_day_are_summed(self) -> None:
        d = date(2026, 3, 2)

        out = daily_r([_trade(d, 1.5), _trade(d, -1.0), _trade(date(2026, 3, 3), 0.5)])

        assert out[d] == pytest.approx(0.5)
        assert out[date(2026, 3, 3)] == pytest.approx(0.5)

    def test_no_trades_gives_no_days(self) -> None:
        assert daily_r([]) == {}


class TestAlign:
    def test_a_day_one_symbol_missed_becomes_a_zero_not_a_gap(self) -> None:
        a = {date(2026, 3, 2): 1.0, date(2026, 3, 4): 2.0}
        b = {date(2026, 3, 3): -1.0}

        days, matrix = align({"A": a, "B": b})

        assert days == [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
        assert matrix.shape == (2, 3)
        np.testing.assert_allclose(matrix[0], [1.0, 0.0, 2.0])
        np.testing.assert_allclose(matrix[1], [0.0, -1.0, 0.0])

    def test_a_calendar_supplies_the_days_nothing_traded(self) -> None:
        # The bug this guards: without a calendar the index is only days something
        # traded, so every flat day vanishes and the risk figures come out flattered.
        a = {date(2026, 3, 2): 1.0}
        calendar = {date(2026, 3, 2) + timedelta(days=i) for i in range(5)}

        days, matrix = align({"A": a}, calendar)

        assert len(days) == 5
        np.testing.assert_allclose(matrix[0], [1.0, 0.0, 0.0, 0.0, 0.0])

    def test_two_sleeves_that_never_trade_together_are_uncorrelated(self) -> None:
        # The zeros are the point: dropping them would make these look related.
        a = {date(2026, 3, 1) + timedelta(days=2 * i): 1.0 for i in range(20)}
        b = {date(2026, 3, 2) + timedelta(days=2 * i): 1.0 for i in range(20)}

        _, matrix = align({"A": a, "B": b})

        assert np.corrcoef(matrix)[0, 1] < 0


class TestMaxDrawdown:
    def test_it_is_measured_from_the_running_peak(self) -> None:
        # Up to 10, down to 4, back to 12: the fall that counts is 6, not 12 - 4.
        curve = np.array([0.0, 5.0, 10.0, 7.0, 4.0, 9.0, 12.0])

        assert max_drawdown(curve) == pytest.approx(6.0)

    def test_a_curve_that_only_rises_has_none(self) -> None:
        assert max_drawdown(np.array([0.0, 1.0, 2.0, 3.0])) == pytest.approx(0.0)

    def test_an_empty_curve_does_not_raise(self) -> None:
        assert max_drawdown(np.array([])) == 0.0


class TestSharpe:
    def test_flat_days_are_in_the_denominator(self) -> None:
        # The same four winning days, once dense and once spread across a quiet month.
        dense = np.array([1.0, 1.0, 1.0, 1.0])
        sparse = np.concatenate([np.array([1.0, 1.0, 1.0, 1.0]), np.zeros(26)])

        assert sharpe(sparse) < sharpe(dense) or sharpe(dense) == 0.0
        assert sharpe(sparse) > 0

    def test_a_series_that_never_moves_is_zero_not_infinite(self) -> None:
        assert sharpe(np.zeros(50)) == 0.0

    def test_one_day_is_not_enough_to_annualise(self) -> None:
        assert sharpe(np.array([1.0])) == 0.0


class TestProfitFactor:
    def test_it_is_gross_win_over_gross_loss(self) -> None:
        assert profit_factor([2.0, -1.0, 1.0, -1.0]) == pytest.approx(1.5)

    def test_no_losers_is_infinite_rather_than_a_division_error(self) -> None:
        assert profit_factor([1.0, 2.0]) == float("inf")
