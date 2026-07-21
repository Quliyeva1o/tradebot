"""Unit tests for strategy/risk_reward.py.

calculate_take_profit is cross-checked against the arithmetic it replaced in
NasdaqMidlineSweepStrategy.evaluate() (see the mandatory regression test,
tests/test_nasdaq_midline_sweep_regression.py, for the full-strategy check).
"""

from datetime import UTC, datetime

import pytest

from core.models import SignalDirection, Timeframe
from strategy.models import TradeSetup
from strategy.risk_reward import calculate_take_profit, resolve_stop_and_target


def _setup(
    direction: SignalDirection,
    stop_zone: tuple[float, float],
    target_zone: tuple[float, float],
) -> TradeSetup:
    return TradeSetup(
        setup_id="s1",
        symbol="USTEC",
        timeframe=Timeframe.M5,
        direction=direction,
        entry_zone=(100.0, 100.0),
        stop_zone=stop_zone,
        target_zone=target_zone,
        confidence_score=1.0,
        confluence=[],
        trigger_reason="",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestCalculateTakeProfit:
    """Tests for calculate_take_profit()."""

    def test_buy_target_is_above_entry(self) -> None:
        tp = calculate_take_profit(entry=100.0, direction=SignalDirection.BUY, risk_dist=10.0, risk_reward=2.0)
        assert tp == pytest.approx(120.0)

    def test_sell_target_is_below_entry(self) -> None:
        tp = calculate_take_profit(entry=100.0, direction=SignalDirection.SELL, risk_dist=10.0, risk_reward=2.0)
        assert tp == pytest.approx(80.0)

    def test_matches_nasdaq_midline_sweep_original_inline_formula(self) -> None:
        """Reproduces the exact arithmetic this function replaced.

        See strategy/risk_reward.py's module docstring.
        """
        entry, risk_dist, risk_reward = 29_050.0, 15.0, 2.0

        reward_dist = risk_dist * risk_reward
        expected_buy = entry + reward_dist
        expected_sell = entry - reward_dist

        assert calculate_take_profit(entry, SignalDirection.BUY, risk_dist, risk_reward) == pytest.approx(
            expected_buy
        )
        assert calculate_take_profit(entry, SignalDirection.SELL, risk_dist, risk_reward) == pytest.approx(
            expected_sell
        )

    def test_risk_reward_of_one_targets_equal_to_risk(self) -> None:
        tp = calculate_take_profit(entry=100.0, direction=SignalDirection.BUY, risk_dist=5.0, risk_reward=1.0)
        assert tp == pytest.approx(105.0)


class TestResolveStopAndTarget:
    """Tests for resolve_stop_and_target()."""

    def test_buy_uses_low_edge_of_each_zone(self) -> None:
        setup = _setup(SignalDirection.BUY, stop_zone=(90.0, 95.0), target_zone=(110.0, 115.0))
        stop_loss, take_profit = resolve_stop_and_target(setup)
        assert stop_loss == 90.0
        assert take_profit == 110.0

    def test_sell_uses_high_edge_of_each_zone(self) -> None:
        setup = _setup(SignalDirection.SELL, stop_zone=(90.0, 95.0), target_zone=(110.0, 115.0))
        stop_loss, take_profit = resolve_stop_and_target(setup)
        assert stop_loss == 95.0
        assert take_profit == 115.0

    def test_single_point_zone_resolves_to_that_point_either_direction(self) -> None:
        buy_setup = _setup(SignalDirection.BUY, stop_zone=(80.0, 80.0), target_zone=(120.0, 120.0))
        sell_setup = _setup(SignalDirection.SELL, stop_zone=(80.0, 80.0), target_zone=(120.0, 120.0))
        assert resolve_stop_and_target(buy_setup) == (80.0, 120.0)
        assert resolve_stop_and_target(sell_setup) == (80.0, 120.0)
