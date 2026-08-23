"""Unit tests for execution/position_sizer.py."""

import pytest

from config.settings import Settings
from core.models import SymbolConstraints
from execution.position_sizer import PositionSizer


def _constraints(
    tick_size: float = 0.25,
    tick_value: float = 1.0,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
) -> SymbolConstraints:
    return SymbolConstraints(
        symbol="USTEC",
        contract_size=1.0,
        tick_size=tick_size,
        tick_value=tick_value,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
    )


class TestRiskPerTradePctDefault:
    def test_defaults_to_settings_value_when_not_given(self) -> None:
        sizer = PositionSizer()
        assert sizer.risk_per_trade_pct == Settings.load().RISK_PER_TRADE_PCT

    def test_explicit_value_overrides_settings_default(self) -> None:
        sizer = PositionSizer(risk_per_trade_pct=0.02)
        assert sizer.risk_per_trade_pct == 0.02

    def test_non_positive_value_raises(self) -> None:
        with pytest.raises(ValueError, match="risk_per_trade_pct"):
            PositionSizer(risk_per_trade_pct=0.0)


class TestCalculateSize:
    def test_matches_hand_computed_arithmetic(self) -> None:
        # balance=10_000, risk 1% -> risk_amount=100. entry-stop distance=10
        # price units, tick_size=0.25 -> 40 ticks, tick_value=1.0 ->
        # loss_per_lot=40. raw_volume = 100 / 40 = 2.5, stepped to 0.01 -> 2.5.
        sizer = PositionSizer(risk_per_trade_pct=0.01)
        constraints = _constraints(tick_size=0.25, tick_value=1.0, volume_step=0.01)

        volume = sizer.calculate_size(
            balance=10_000.0, entry_price=29_200.0, stop_loss=29_190.0, constraints=constraints
        )

        assert volume == pytest.approx(2.5)

    def test_rounds_down_to_volume_step(self) -> None:
        # raw_volume = 100 / 33.333.. -> not an exact multiple of 0.1.
        sizer = PositionSizer(risk_per_trade_pct=0.01)
        constraints = _constraints(tick_size=1.0, tick_value=3.0, volume_step=0.1)

        volume = sizer.calculate_size(
            balance=10_000.0, entry_price=100.0, stop_loss=90.0, constraints=constraints
        )

        # risk_amount=100, distance_ticks=10, loss_per_lot=30, raw=3.333.. -> floor to 0.1 steps = 3.3
        assert volume == pytest.approx(3.3)

    def test_clamps_to_volume_max(self) -> None:
        sizer = PositionSizer(risk_per_trade_pct=0.5)
        constraints = _constraints(tick_size=0.25, tick_value=1.0, volume_max=1.0)

        volume = sizer.calculate_size(
            balance=10_000.0, entry_price=100.0, stop_loss=99.75, constraints=constraints
        )

        assert volume == 1.0

    def test_clamps_to_volume_min(self) -> None:
        sizer = PositionSizer(risk_per_trade_pct=0.0001)
        constraints = _constraints(tick_size=0.25, tick_value=1.0, volume_min=0.05)

        volume = sizer.calculate_size(
            balance=10_000.0, entry_price=29_200.0, stop_loss=29_100.0, constraints=constraints
        )

        assert volume == 0.05

    def test_zero_distance_returns_zero_not_zero_division_error(self) -> None:
        sizer = PositionSizer(risk_per_trade_pct=0.01)
        constraints = _constraints()

        volume = sizer.calculate_size(
            balance=10_000.0, entry_price=100.0, stop_loss=100.0, constraints=constraints
        )

        assert volume == 0.0

    def test_non_positive_balance_raises(self) -> None:
        sizer = PositionSizer(risk_per_trade_pct=0.01)
        with pytest.raises(ValueError, match="balance"):
            sizer.calculate_size(
                balance=0.0, entry_price=100.0, stop_loss=90.0, constraints=_constraints()
            )
