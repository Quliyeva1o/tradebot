"""Unit tests for execution/take_profit_engine.py.

FixedTakeProfitEngine.resolve_take_profit() is cross-checked against
calling strategy.risk_reward.resolve_stop_and_target() directly on the same
TradeSetup, to confirm the adapter reproduces the exact same value (this
sprint is a pure interface extraction, not new logic).
"""

from datetime import UTC, datetime

import pytest

from core.models import SignalDirection, Timeframe
from execution.take_profit_engine import FixedTakeProfitEngine, TakeProfitEngine
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_stop_and_target


def _setup(
    direction: SignalDirection,
    target_zone: tuple[float, float],
    stop_zone: tuple[float, float] = (0.0, 0.0),
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


class TestTakeProfitEngineProtocolCompliance:
    """Structural (duck-typed) compliance checks for FixedTakeProfitEngine against TakeProfitEngine."""

    def test_fixed_take_profit_engine_satisfies_take_profit_engine(self) -> None:
        assert isinstance(FixedTakeProfitEngine(), TakeProfitEngine)

    def test_fixed_take_profit_engine_class_satisfies_take_profit_engine(self) -> None:
        assert issubclass(FixedTakeProfitEngine, TakeProfitEngine)

    def test_object_missing_resolve_take_profit_does_not_satisfy_take_profit_engine(self) -> None:
        class NotATakeProfitEngine:
            pass

        assert not isinstance(NotATakeProfitEngine(), TakeProfitEngine)

    def test_take_profit_engine_declares_resolve_take_profit(self) -> None:
        assert "resolve_take_profit" in dir(TakeProfitEngine)


class TestFixedTakeProfitEngineMatchesRiskReward:
    """FixedTakeProfitEngine must reproduce resolve_stop_and_target()'s take_profit exactly."""

    def test_buy_matches_direct_call(self) -> None:
        setup = _setup(SignalDirection.BUY, target_zone=(110.0, 115.0))
        _, expected_take_profit = resolve_stop_and_target(setup)

        assert FixedTakeProfitEngine().resolve_take_profit(setup) == expected_take_profit

    def test_sell_matches_direct_call(self) -> None:
        setup = _setup(SignalDirection.SELL, target_zone=(110.0, 115.0))
        _, expected_take_profit = resolve_stop_and_target(setup)

        assert FixedTakeProfitEngine().resolve_take_profit(setup) == expected_take_profit

    @pytest.mark.parametrize(
        ("direction", "target_zone"),
        [
            (SignalDirection.BUY, (29_200.0, 29_200.0)),
            (SignalDirection.SELL, (28_800.0, 28_800.0)),
            (SignalDirection.BUY, (100.0, 120.0)),
            (SignalDirection.SELL, (100.0, 120.0)),
        ],
    )
    def test_matches_direct_call_across_examples(
        self, direction: SignalDirection, target_zone: tuple[float, float]
    ) -> None:
        setup = _setup(direction, target_zone=target_zone)
        _, expected_take_profit = resolve_stop_and_target(setup)

        assert FixedTakeProfitEngine().resolve_take_profit(setup) == expected_take_profit
