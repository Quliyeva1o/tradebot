"""Unit tests for OrderBlockRetestStrategy (Strategy #4, plain-text spec).

Uses a bare MarketState with directly-populated smc_state.order_blocks
(this strategy reuses order blocks already computed by the SMC pipeline,
so tests supply fixture OrderBlock objects rather than running full OB
detection).
"""

from datetime import UTC, datetime

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from smc.order_block import OBDirection, OrderBlock
from strategy.diagnostics import RejectionReason
from strategy.order_block_retest import OrderBlockRetestStrategy


def _bar(minute: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 5, 10, minute, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def _ob(
    ob_id: str,
    bar_index: int,
    high: float,
    low: float,
    direction: OBDirection,
) -> OrderBlock:
    return OrderBlock(
        id=ob_id,
        bar_index=bar_index,
        high=high,
        low=low,
        direction=direction,
        timestamp=datetime(2026, 1, 5, 9, bar_index, tzinfo=UTC),
    )


def _new_state(order_blocks: list[OrderBlock]) -> MarketState:
    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.smc_state.order_blocks = order_blocks
    return state


BULLISH_OB = _ob("ob_10_bullish", 10, high=1.1010, low=1.0990, direction=OBDirection.BULLISH)
BEARISH_OB = _ob("ob_15_bearish", 15, high=1.2010, low=1.1990, direction=OBDirection.BEARISH)


class TestOrderBlockTouch:
    """Bullish/bearish OB edge touch -> correct entry/SL/TP."""

    def test_bullish_ob_touch_generates_correct_entry_sl_tp(self) -> None:
        state = _new_state([BULLISH_OB])
        strategy = OrderBlockRetestStrategy()

        # low=1.1005 <= ob.high=1.1010 <= high=1.1020 -> touch.
        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (1.1010, 1.1010)  # ob.high
        assert setup.stop_zone == (1.0990, 1.0990)  # ob.low
        # risk = 1.1010 - 1.0990 = 0.0020; reward = 0.0020 * 2.0 (default rr)
        assert setup.target_zone == (round(1.1010 + 0.0020 * 2.0, 5), round(1.1010 + 0.0020 * 2.0, 5))
        assert setup.related_order_block is BULLISH_OB
        assert setup.strategy_name == "OrderBlockRetestStrategy"

    def test_bearish_ob_touch_generates_correct_entry_sl_tp(self) -> None:
        state = _new_state([BEARISH_OB])
        strategy = OrderBlockRetestStrategy()

        # low=1.1980 <= ob.low=1.1990 <= high=1.1995 -> touch.
        touch_bar = _bar(0, 1.1985, 1.1995, 1.1980, 1.1988)
        state.append_bar(touch_bar)
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        assert setup.entry_zone == (1.1990, 1.1990)  # ob.low
        assert setup.stop_zone == (1.2010, 1.2010)  # ob.high
        # risk = 1.2010 - 1.1990 = 0.0020; reward = 0.0020 * 2.0
        assert setup.target_zone == (round(1.1990 - 0.0040, 5), round(1.1990 - 0.0040, 5))

    def test_no_touch_detected_rejection(self) -> None:
        state = _new_state([BULLISH_OB])
        strategy = OrderBlockRetestStrategy()

        far_bar = _bar(0, 1.5000, 1.5010, 1.4990, 1.5005)
        state.append_bar(far_bar)
        result = strategy.evaluate(state)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TOUCH_DETECTED] == 1

    def test_no_order_blocks_rejection(self) -> None:
        state = _new_state([])
        strategy = OrderBlockRetestStrategy()

        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_ORDER_BLOCKS] == 1


class TestOncePerOrderBlock:
    """Each Order Block is traded at most once, independent of is_mitigated."""

    def test_second_touch_of_same_ob_is_rejected(self) -> None:
        state = _new_state([BULLISH_OB])
        strategy = OrderBlockRetestStrategy()

        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        setup = strategy.evaluate(state)
        assert setup is not None

        # Price dips back into the same OB edge again later -- must be
        # rejected as already-used, even though OB.is_mitigated is still
        # False (this strategy's own memory is independent of it).
        second_touch_bar = _bar(1, 1.1012, 1.1018, 1.1004, 1.1008)
        state.append_bar(second_touch_bar)
        result = strategy.evaluate(state)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.OB_ALREADY_USED] == 1
        assert not BULLISH_OB.is_mitigated  # confirms independence from mitigation

    def test_other_unused_ob_still_tradeable_after_one_is_used(self) -> None:
        other_bullish_ob = _ob(
            "ob_20_bullish", 20, high=1.1110, low=1.1090, direction=OBDirection.BULLISH
        )
        state = _new_state([BULLISH_OB, other_bullish_ob])
        strategy = OrderBlockRetestStrategy()

        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        assert strategy.evaluate(state) is not None

        touch_other_bar = _bar(1, 1.1095, 1.1115, 1.1085, 1.1100)
        state.append_bar(touch_other_bar)
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_order_block is other_bullish_ob


class TestDiagnosticsAndConfig:
    def test_reset_clears_diagnostics_and_usage_memory(self) -> None:
        state = _new_state([BULLISH_OB])
        strategy = OrderBlockRetestStrategy()

        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        assert strategy.evaluate(state) is not None
        assert "ob_10_bullish" in strategy._used_ob_ids

        strategy.reset()
        assert strategy._used_ob_ids == set()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}

    def test_config_overlay_applies_risk_reward(self) -> None:
        from strategy.order_block_retest import OrderBlockRetestConfig

        config = OrderBlockRetestConfig(risk_reward=3.0)
        strategy = OrderBlockRetestStrategy(config=config)
        assert strategy.risk_reward == 3.0

    def test_non_positive_risk_rejection(self) -> None:
        # An OB with high <= low (degenerate) yields non-positive risk.
        degenerate_ob = _ob(
            "ob_5_bullish", 5, high=1.1000, low=1.1000, direction=OBDirection.BULLISH
        )
        state = _new_state([degenerate_ob])
        strategy = OrderBlockRetestStrategy()

        touch_bar = _bar(0, 1.1000, 1.1005, 1.0995, 1.1002)
        state.append_bar(touch_bar)
        result = strategy.evaluate(state)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NON_POSITIVE_RISK] == 1
