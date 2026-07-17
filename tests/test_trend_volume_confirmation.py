"""Unit tests for TrendVolumeConfirmationStrategy.

Uses a bare MarketState (append_bar/get_latest_bar/bars_view only) with a
manually-assigned structure_state, since this strategy reads only
market_state.structure_state.trend/active_major_high/active_major_low and
raw bars -- it has no dependency on SMC state (order blocks, FVGs,
liquidity, displacement).
"""

from datetime import UTC, datetime

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState, StructureState, StructureTrend
from market_structure.swing_models import Swing, SwingType
from strategy.diagnostics import RejectionReason
from strategy.trend_volume_confirmation import (
    TrendVolumeConfirmationConfig,
    TrendVolumeConfirmationStrategy,
)


def _bar(i: int, o: float, h: float, l: float, c: float, volume: float = 100.0) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 5, 0, i, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=volume,
    )


def _swing(price: float, swing_type: SwingType, index: int = 0) -> Swing:
    return Swing(
        id=f"swing_{index}_{swing_type.value}",
        timestamp=datetime(2026, 1, 4, 0, 0, tzinfo=UTC),
        index=index,
        price=price,
        type=swing_type,
    )


def _new_state(
    trend: StructureTrend = StructureTrend.BULLISH,
    active_major_high: Swing | None = None,
    active_major_low: Swing | None = None,
) -> MarketState:
    state = MarketState(symbol="USTEC", timeframe=Timeframe.M5)
    state.structure_state = StructureState(
        trend=trend,
        active_major_high=active_major_high,
        active_major_low=active_major_low,
    )
    return state


def _feed_flat_volume(state: MarketState, n: int, volume: float = 100.0) -> None:
    """Feeds n low, constant-volume bars so a later spike bar has a clean average."""
    for i in range(n):
        state.append_bar(_bar(i, 1.0, 1.001, 0.999, 1.0, volume=volume))


class TestTrendGate:
    def test_range_trend_rejects(self) -> None:
        state = _new_state(trend=StructureTrend.RANGE)
        strategy = TrendVolumeConfirmationStrategy()
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_transition_trend_rejects(self) -> None:
        state = _new_state(trend=StructureTrend.TRANSITION)
        strategy = TrendVolumeConfirmationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_unknown_trend_rejects(self) -> None:
        state = _new_state(trend=StructureTrend.UNKNOWN)
        strategy = TrendVolumeConfirmationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1


class TestVolumeGate:
    def test_no_volume_spike_rejects(self) -> None:
        state = _new_state(
            trend=StructureTrend.BULLISH,
            active_major_low=_swing(0.9, SwingType.LOW),
        )
        strategy = TrendVolumeConfirmationStrategy(volume_lookback=5)
        _feed_flat_volume(state, 5, volume=100.0)
        # Final bar's volume (100) does not exceed 1.5x the flat average (100).
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_VOLUME_SPIKE] == 1

    def test_incomplete_window_rejects(self) -> None:
        """Fewer than volume_lookback bars means the average is not yet reliable."""
        state = _new_state(
            trend=StructureTrend.BULLISH,
            active_major_low=_swing(0.9, SwingType.LOW),
        )
        strategy = TrendVolumeConfirmationStrategy(volume_lookback=20)
        state.append_bar(_bar(0, 1.0, 1.001, 0.999, 1.0, volume=1000.0))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_VOLUME_SPIKE] == 1


class TestMajorSwingGate:
    def test_missing_opposite_major_swing_rejects(self) -> None:
        state = _new_state(trend=StructureTrend.BULLISH, active_major_low=None)
        strategy = TrendVolumeConfirmationStrategy(volume_lookback=5)
        _feed_flat_volume(state, 4, volume=100.0)
        state.append_bar(_bar(4, 1.0, 1.01, 0.99, 1.005, volume=200.0))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MAJOR_SWING_FOR_SL] == 1

    def test_missing_major_high_rejects_bearish(self) -> None:
        state = _new_state(trend=StructureTrend.BEARISH, active_major_high=None)
        strategy = TrendVolumeConfirmationStrategy(volume_lookback=5)
        _feed_flat_volume(state, 4, volume=100.0)
        state.append_bar(_bar(4, 1.0, 1.01, 0.99, 0.995, volume=200.0))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MAJOR_SWING_FOR_SL] == 1


class TestSetupCalculation:
    def test_buy_setup_calculated_correctly(self) -> None:
        state = _new_state(
            trend=StructureTrend.BULLISH,
            active_major_low=_swing(1.0000, SwingType.LOW),
        )
        strategy = TrendVolumeConfirmationStrategy(
            volume_lookback=5, volume_multiplier=1.5, stop_buffer_pct=0.001, risk_reward=2.0
        )
        _feed_flat_volume(state, 4, volume=100.0)
        state.append_bar(_bar(4, 1.05, 1.06, 1.04, 1.0500, volume=200.0))

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        entry = 1.0500
        sl = 1.0000 - (1.0000 * 0.001)
        risk = entry - sl
        tp = entry + risk * 2.0
        assert setup.entry_zone == (round(entry, 5), round(entry, 5))
        assert setup.stop_zone == (round(sl, 5), round(sl, 5))
        assert setup.target_zone == (round(tp, 5), round(tp, 5))
        assert setup.strategy_name == "TrendVolumeConfirmationStrategy"
        assert strategy.diagnostics.setups_generated == 1

    def test_sell_setup_calculated_correctly(self) -> None:
        state = _new_state(
            trend=StructureTrend.BEARISH,
            active_major_high=_swing(1.1000, SwingType.HIGH),
        )
        strategy = TrendVolumeConfirmationStrategy(
            volume_lookback=5, volume_multiplier=1.5, stop_buffer_pct=0.001, risk_reward=2.0
        )
        _feed_flat_volume(state, 4, volume=100.0)
        state.append_bar(_bar(4, 1.05, 1.06, 1.04, 1.0400, volume=200.0))

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        entry = 1.0400
        sl = 1.1000 + (1.1000 * 0.001)
        risk = sl - entry
        tp = entry - risk * 2.0
        assert setup.entry_zone == (round(entry, 5), round(entry, 5))
        assert setup.stop_zone == (round(sl, 5), round(sl, 5))
        assert setup.target_zone == (round(tp, 5), round(tp, 5))
        assert strategy.diagnostics.setups_generated == 1

    def test_non_positive_risk_rejects(self) -> None:
        """Major low above/at entry price yields non-positive risk distance."""
        state = _new_state(
            trend=StructureTrend.BULLISH,
            active_major_low=_swing(1.0600, SwingType.LOW),
        )
        strategy = TrendVolumeConfirmationStrategy(volume_lookback=5, stop_buffer_pct=0.001)
        _feed_flat_volume(state, 4, volume=100.0)
        # Entry (close=1.05) is below the major-low-derived stop (~1.0611).
        state.append_bar(_bar(4, 1.05, 1.06, 1.04, 1.0500, volume=200.0))

        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NON_POSITIVE_RISK] == 1


class TestConfigValidation:
    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            TrendVolumeConfirmationStrategy(config="not-a-config")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"volume_lookback": 0},
            {"volume_lookback": -1},
            {"volume_multiplier": 0.0},
            {"volume_multiplier": -1.5},
            {"stop_buffer_pct": 0.0},
            {"stop_buffer_pct": -0.001},
            {"risk_reward": 0.0},
            {"risk_reward": -2.0},
        ],
    )
    def test_invalid_params_raise_value_error(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            TrendVolumeConfirmationConfig(**kwargs)

    def test_valid_config_constructs(self) -> None:
        config = TrendVolumeConfirmationConfig(
            volume_lookback=10, volume_multiplier=2.0, stop_buffer_pct=0.002, risk_reward=3.0
        )
        strategy = TrendVolumeConfirmationStrategy(config=config)
        assert strategy.volume_lookback == 10
        assert strategy.volume_multiplier == 2.0
        assert strategy.stop_buffer_pct == 0.002
        assert strategy.risk_reward == 3.0


class TestResetAndDiagnostics:
    def test_reset_clears_diagnostics(self) -> None:
        state = _new_state(trend=StructureTrend.RANGE)
        strategy = TrendVolumeConfirmationStrategy()
        strategy.evaluate(state)
        assert strategy.diagnostics.evaluations == 1
        strategy.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 0
