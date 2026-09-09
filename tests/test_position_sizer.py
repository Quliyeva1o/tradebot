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


# ---------------------------------------------------------------------------
# SizingOutcome: making the volume_min clamp visible
# ---------------------------------------------------------------------------
# The clamp can only ever RAISE risk -- when the budget buys less than one
# minimum lot, the venue has nothing smaller to sell, so the trade goes on at
# more than the requested percentage. It was silent until 2026-09-09, when an
# NDX100 Demo entry risked 0.688% of a $5,008 account against a 0.5% setting.


def test_sizing_outcome_records_min_lot_clamp_with_real_ndx100_numbers():
    """Reproduces the live 2026-09-09 NDX100 entry exactly.

    Real values from ticket 12261768: BUY 0.01 lot @ 29564.38, SL 29391.88 on a
    $5007.94 account at 0.5%. MT5 reported tick_size 0.01 / tick_value 0.2, so a
    172.50-point stop costs $34.50 per 0.01 lot -- 1.378x the $25.04 budget.
    """
    sizer = PositionSizer(risk_per_trade_pct=0.005)
    ndx = _constraints(tick_size=0.01, tick_value=0.2, volume_min=0.01, volume_step=0.01)

    volume = sizer.calculate_size(5007.94, 29564.38, 29391.88, ndx)

    assert volume == 0.01
    outcome = sizer.last_sizing
    assert outcome is not None
    assert outcome.clamped_to_min is True
    assert outcome.wanted_volume == pytest.approx(0.00726, abs=1e-4)
    assert outcome.risk_amount == pytest.approx(25.04, abs=0.01)
    assert outcome.actual_risk == pytest.approx(34.50, abs=0.01)
    assert outcome.risk_multiple == pytest.approx(1.378, abs=0.001)


def test_sizing_outcome_not_clamped_when_budget_buys_more_than_min_lot():
    sizer = PositionSizer(risk_per_trade_pct=0.005)
    xau = _constraints(tick_size=0.01, tick_value=0.01, volume_min=0.01, volume_step=0.01)

    volume = sizer.calculate_size(5007.94, 4420.66, 4392.32, xau)

    outcome = sizer.last_sizing
    assert outcome is not None
    assert outcome.clamped_to_min is False
    assert volume > xau.volume_min
    # Stepping rounds DOWN, so a non-clamped size never exceeds the budget.
    assert outcome.risk_multiple <= 1.0


def test_sizing_outcome_ignores_the_volume_max_clamp():
    """volume_max clamps downward, which under-risks -- never dangerous."""
    sizer = PositionSizer(risk_per_trade_pct=0.5)
    tiny_cap = _constraints(tick_size=0.01, tick_value=0.01, volume_max=0.02)

    sizer.calculate_size(100_000.0, 4420.66, 4392.32, tiny_cap)

    outcome = sizer.last_sizing
    assert outcome is not None
    assert outcome.volume == 0.02
    assert outcome.clamped_to_min is False
    assert outcome.risk_multiple < 1.0


def test_sizing_outcome_is_cleared_when_sizing_is_undefined():
    """A zero stop distance returns 0.0 lots; no stale outcome may survive."""
    sizer = PositionSizer(risk_per_trade_pct=0.005)
    c = _constraints()
    sizer.calculate_size(5007.94, 4420.66, 4392.32, c)
    assert sizer.last_sizing is not None

    assert sizer.calculate_size(5007.94, 100.0, 100.0, c) == 0.0
    assert sizer.last_sizing is None
