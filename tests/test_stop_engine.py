"""Unit tests for execution/stop_engine.py.

FixedStopEngine.resolve_stop() is cross-checked against calling
strategy.risk_reward.resolve_stop_and_target() directly on the same
TradeSetup, to confirm the adapter reproduces the exact same value (this
sprint is a pure interface extraction, not new logic).
"""

from datetime import UTC, datetime

import pytest

from core.models import SignalDirection, Timeframe
from execution.stop_engine import FixedStopEngine, StopEngine
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_stop_and_target


def _setup(
    direction: SignalDirection,
    stop_zone: tuple[float, float],
    target_zone: tuple[float, float] = (0.0, 0.0),
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


class TestStopEngineProtocolCompliance:
    """Structural (duck-typed) compliance checks for FixedStopEngine against StopEngine."""

    def test_fixed_stop_engine_satisfies_stop_engine(self) -> None:
        assert isinstance(FixedStopEngine(), StopEngine)

    def test_fixed_stop_engine_class_satisfies_stop_engine(self) -> None:
        assert issubclass(FixedStopEngine, StopEngine)

    def test_object_missing_resolve_stop_does_not_satisfy_stop_engine(self) -> None:
        class NotAStopEngine:
            pass

        assert not isinstance(NotAStopEngine(), StopEngine)

    def test_stop_engine_declares_resolve_stop(self) -> None:
        assert "resolve_stop" in dir(StopEngine)


class TestFixedStopEngineMatchesRiskReward:
    """FixedStopEngine must reproduce resolve_stop_and_target()'s stop_loss exactly."""

    def test_buy_matches_direct_call(self) -> None:
        setup = _setup(SignalDirection.BUY, stop_zone=(90.0, 95.0))
        expected_stop_loss, _ = resolve_stop_and_target(setup)

        assert FixedStopEngine().resolve_stop(setup) == expected_stop_loss

    def test_sell_matches_direct_call(self) -> None:
        setup = _setup(SignalDirection.SELL, stop_zone=(90.0, 95.0))
        expected_stop_loss, _ = resolve_stop_and_target(setup)

        assert FixedStopEngine().resolve_stop(setup) == expected_stop_loss

    @pytest.mark.parametrize(
        ("direction", "stop_zone"),
        [
            (SignalDirection.BUY, (28_900.0, 28_900.0)),
            (SignalDirection.SELL, (29_100.0, 29_100.0)),
            (SignalDirection.BUY, (10.0, 20.0)),
            (SignalDirection.SELL, (10.0, 20.0)),
        ],
    )
    def test_matches_direct_call_across_examples(
        self, direction: SignalDirection, stop_zone: tuple[float, float]
    ) -> None:
        setup = _setup(direction, stop_zone=stop_zone)
        expected_stop_loss, _ = resolve_stop_and_target(setup)

        assert FixedStopEngine().resolve_stop(setup) == expected_stop_loss
