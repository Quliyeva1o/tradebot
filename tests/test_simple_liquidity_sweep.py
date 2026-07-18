"""Unit tests for SimpleLiquiditySweepStrategy.

Uses a bare MarketState (append_bar/get_latest_bar/bars_view only) --
this strategy reads only raw bars, no structure_state/SMC dependency.
"""

from datetime import UTC, datetime

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.simple_liquidity_sweep import (
    SimpleLiquiditySweepConfig,
    SimpleLiquiditySweepStrategy,
)


def _bar(i: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 5, 0, i, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="EURUSD", timeframe=Timeframe.M15)


class TestInsufficientHistory:
    def test_no_bars_rejects_no_latest_bar(self) -> None:
        state = _new_state()
        strategy = SimpleLiquiditySweepStrategy()
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_LATEST_BAR] == 1

    def test_single_bar_rejects_no_prev_bar(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0, 1.001, 0.999, 1.0))
        strategy = SimpleLiquiditySweepStrategy()
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_PREV_BAR] == 1


class TestSweepGate:
    def test_no_sweep_rejects(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # Latest bar stays entirely inside prev bar's range -- no sweep either side.
        state.append_bar(_bar(1, 1.0505, 1.0515, 1.0495, 1.0505))
        strategy = SimpleLiquiditySweepStrategy()
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_SWEEP] == 1

    def test_low_sweep_without_reclaim_rejects(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # Dips below prev low but closes below it too -- no reclaim.
        state.append_bar(_bar(1, 1.0500, 1.0505, 1.0470, 1.0480))
        strategy = SimpleLiquiditySweepStrategy()
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_SWEEP] == 1


class TestBullishSweepSetup:
    def test_bullish_sweep_calculated_correctly(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # low (1.0470) < prev.low (1.0490); close (1.0500) > prev.low (1.0490).
        state.append_bar(_bar(1, 1.0495, 1.0505, 1.0470, 1.0500))
        strategy = SimpleLiquiditySweepStrategy(stop_buffer_pct=0.001, tp_wick_multiplier=2.0)

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        entry = 1.0500
        sl = 1.0470 * (1 - 0.001)
        wick_length = 1.0490 - 1.0470
        tp = entry + wick_length * 2.0
        assert setup.entry_zone == (round(entry, 5), round(entry, 5))
        assert setup.stop_zone == (round(sl, 5), round(sl, 5))
        assert setup.target_zone == (round(tp, 5), round(tp, 5))
        assert setup.strategy_name == "SimpleLiquiditySweepStrategy"
        assert strategy.diagnostics.setups_generated == 1


class TestBearishSweepSetup:
    def test_bearish_sweep_calculated_correctly(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # high (1.0540) > prev.high (1.0520); close (1.0505) < prev.high (1.0520).
        state.append_bar(_bar(1, 1.0510, 1.0540, 1.0500, 1.0505))
        strategy = SimpleLiquiditySweepStrategy(stop_buffer_pct=0.001, tp_wick_multiplier=2.0)

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        entry = 1.0505
        sl = 1.0540 * (1 + 0.001)
        wick_length = 1.0540 - 1.0520
        tp = entry - wick_length * 2.0
        assert setup.entry_zone == (round(entry, 5), round(entry, 5))
        assert setup.stop_zone == (round(sl, 5), round(sl, 5))
        assert setup.target_zone == (round(tp, 5), round(tp, 5))
        assert strategy.diagnostics.setups_generated == 1


class TestOutsideBarTieBreak:
    def test_outside_bar_bullish_body_resolves_to_buy(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # Sweeps both sides (low<prev.low, high>prev.high) but closes above
        # open -- bullish body should win the tie.
        state.append_bar(_bar(1, 1.0495, 1.0550, 1.0470, 1.0515))
        strategy = SimpleLiquiditySweepStrategy()

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY

    def test_outside_bar_bearish_body_resolves_to_sell(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # Same outside bar, but closes below open -- bearish body should win.
        state.append_bar(_bar(1, 1.0515, 1.0550, 1.0470, 1.0495))
        strategy = SimpleLiquiditySweepStrategy()

        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL

    def test_outside_bar_doji_rejects_no_sweep(self) -> None:
        state = _new_state()
        state.append_bar(_bar(0, 1.0500, 1.0520, 1.0490, 1.0510))
        # Outside bar, close == open -- no directional signal, no trade.
        state.append_bar(_bar(1, 1.0500, 1.0550, 1.0470, 1.0500))
        strategy = SimpleLiquiditySweepStrategy()

        result = strategy.evaluate(state)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_SWEEP] == 1


class TestConfigValidation:
    def test_wrong_config_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            SimpleLiquiditySweepStrategy(config="not-a-config")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"stop_buffer_pct": 0.0},
            {"stop_buffer_pct": -0.001},
            {"tp_wick_multiplier": 0.0},
            {"tp_wick_multiplier": -2.0},
        ],
    )
    def test_invalid_params_raise_value_error(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            SimpleLiquiditySweepConfig(**kwargs)

    def test_valid_config_constructs(self) -> None:
        config = SimpleLiquiditySweepConfig(stop_buffer_pct=0.002, tp_wick_multiplier=3.0)
        strategy = SimpleLiquiditySweepStrategy(config=config)
        assert strategy.stop_buffer_pct == 0.002
        assert strategy.tp_wick_multiplier == 3.0


class TestResetAndDiagnostics:
    def test_reset_clears_diagnostics(self) -> None:
        state = _new_state()
        strategy = SimpleLiquiditySweepStrategy()
        strategy.evaluate(state)
        assert strategy.diagnostics.evaluations == 1
        strategy.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections[RejectionReason.NO_LATEST_BAR] == 0
