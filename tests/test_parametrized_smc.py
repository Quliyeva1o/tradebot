"""Unit tests for ParametrizedSMCStrategy and PatternCandidateConfig.

Uses a bare MarketState with directly-populated smc_state/structure_state
(mirrors test_order_block_retest.py) -- this strategy reuses whatever the
SMC pipeline has already computed, so tests supply fixture OrderBlock/
FairValueGap/LiquidityLevel/StructureState objects directly.
"""

from datetime import UTC, datetime

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState, StructureState, StructureTrend
from smc.fvg import FairValueGap, FVGDirection
from smc.liquidity import LiquidityLevel, LiquidityType
from smc.order_block import OBDirection, OrderBlock
from strategy.diagnostics import RejectionReason
from strategy.parametrized_smc import (
    EntryPoint,
    ParametrizedSMCStrategy,
    PatternCandidateConfig,
    TrendFilterMode,
)


def _bar(minute: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 5, 10, minute, tzinfo=UTC), open=o, high=h, low=l, close=c, volume=100.0
    )


def _ob(ob_id: str, bar_index: int, high: float, low: float, direction: OBDirection) -> OrderBlock:
    return OrderBlock(
        id=ob_id,
        bar_index=bar_index,
        high=high,
        low=low,
        direction=direction,
        timestamp=datetime(2026, 1, 5, 9, bar_index, tzinfo=UTC),
    )


def _fvg(
    fvg_id: str, end_index: int, upper: float, lower: float, direction: FVGDirection
) -> FairValueGap:
    return FairValueGap(
        id=fvg_id,
        start_index=end_index - 2,
        end_index=end_index,
        upper_price=upper,
        lower_price=lower,
        direction=direction,
        timestamp=datetime(2026, 1, 5, 9, end_index, tzinfo=UTC),
    )


def _liq(liq_id: str, price: float, liq_type: LiquidityType, swept: bool) -> LiquidityLevel:
    return LiquidityLevel(id=liq_id, price=price, type=liq_type, source_swing_ids=[], is_swept=swept)


def _config(**overrides) -> PatternCandidateConfig:
    defaults = dict(
        candidate_id="test_candidate",
        ob_direction=OBDirection.BULLISH,
        require_fvg=False,
        require_liquidity_sweep=False,
        entry_point=EntryPoint.OB_EDGE,
        take_profit_r=2.0,
        trend_filter=TrendFilterMode.NONE,
    )
    defaults.update(overrides)
    return PatternCandidateConfig(**defaults)


def _new_state(
    order_blocks: list[OrderBlock] | None = None,
    fair_value_gaps: list[FairValueGap] | None = None,
    liquidity_levels: list[LiquidityLevel] | None = None,
    trend: StructureTrend = StructureTrend.UNKNOWN,
) -> MarketState:
    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.smc_state.order_blocks = order_blocks or []
    state.smc_state.fair_value_gaps = fair_value_gaps or []
    state.smc_state.liquidity_levels = liquidity_levels or []
    state.structure_state = StructureState(trend=trend)
    return state


BULLISH_OB = _ob("ob_10_bullish", 10, high=1.1010, low=1.0990, direction=OBDirection.BULLISH)
BEARISH_OB = _ob("ob_15_bearish", 15, high=1.2010, low=1.1990, direction=OBDirection.BEARISH)


class TestPatternCandidateConfigValidation:
    def test_take_profit_r_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            _config(take_profit_r=0.0)
        with pytest.raises(ValueError):
            _config(take_profit_r=-1.0)

    def test_fvg_edge_requires_fvg_true(self) -> None:
        with pytest.raises(ValueError):
            _config(entry_point=EntryPoint.FVG_EDGE, require_fvg=False)

    def test_fvg_edge_with_require_fvg_true_is_valid(self) -> None:
        cfg = _config(entry_point=EntryPoint.FVG_EDGE, require_fvg=True)
        assert cfg.entry_point == EntryPoint.FVG_EDGE


class TestOBDirectionGating:
    def test_bullish_candidate_ignores_bearish_ob(self) -> None:
        state = _new_state(order_blocks=[BEARISH_OB])
        strategy = ParametrizedSMCStrategy(_config(ob_direction=OBDirection.BULLISH))
        # Touches the bearish OB's edge, but candidate only trades bullish OBs.
        touch_bar = _bar(0, 1.1985, 1.1995, 1.1980, 1.1988)
        state.append_bar(touch_bar)
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_ORDER_BLOCKS] == 1

    def test_bearish_candidate_ignores_bullish_ob(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config(ob_direction=OBDirection.BEARISH))
        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_ORDER_BLOCKS] == 1


class TestEntryPointCalculation:
    def test_ob_edge_bullish(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config(entry_point=EntryPoint.OB_EDGE))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        setup = strategy.evaluate(state)
        assert setup is not None
        assert setup.entry_zone == (1.1010, 1.1010)  # ob.high
        assert setup.stop_zone == (1.0990, 1.0990)  # ob.low

    def test_ob_mid_bullish(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config(entry_point=EntryPoint.OB_MID))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        setup = strategy.evaluate(state)
        assert setup is not None
        expected_mid = round((1.1010 + 1.0990) / 2, 5)
        assert setup.entry_zone == (expected_mid, expected_mid)

    def test_fvg_edge_bullish(self) -> None:
        fvg = _fvg("fvg_1", 12, upper=1.1005, lower=1.0998, direction=FVGDirection.BULLISH)
        state = _new_state(order_blocks=[BULLISH_OB], fair_value_gaps=[fvg])
        strategy = ParametrizedSMCStrategy(
            _config(entry_point=EntryPoint.FVG_EDGE, require_fvg=True)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        setup = strategy.evaluate(state)
        assert setup is not None
        assert setup.entry_zone == (1.1005, 1.1005)  # fvg.upper_price
        assert setup.related_fvg is fvg

    def test_take_profit_r_multiple_applied(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config(entry_point=EntryPoint.OB_EDGE, take_profit_r=3.0))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        setup = strategy.evaluate(state)
        assert setup is not None
        risk = 1.1010 - 1.0990
        expected_tp = round(1.1010 + risk * 3.0, 5)
        assert setup.target_zone == (expected_tp, expected_tp)


class TestFVGRequirement:
    def test_require_fvg_true_without_matching_fvg_rejects(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], fair_value_gaps=[])
        strategy = ParametrizedSMCStrategy(_config(require_fvg=True))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MATCHING_FVG] == 1

    def test_require_fvg_false_ignores_missing_fvg(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], fair_value_gaps=[])
        strategy = ParametrizedSMCStrategy(_config(require_fvg=False))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is not None


class TestLiquiditySweepRequirement:
    def test_require_sweep_true_without_sweep_rejects(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], liquidity_levels=[])
        strategy = ParametrizedSMCStrategy(_config(require_liquidity_sweep=True))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.LIQUIDITY_NOT_SWEPT] == 1

    def test_require_sweep_true_with_sweep_passes(self) -> None:
        swept_liq = _liq("liq_1", 1.0950, LiquidityType.SELL_SIDE, swept=True)
        state = _new_state(order_blocks=[BULLISH_OB], liquidity_levels=[swept_liq])
        strategy = ParametrizedSMCStrategy(_config(require_liquidity_sweep=True))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is not None

    def test_bullish_requires_sell_side_sweep_not_buy_side(self) -> None:
        # BUY_SIDE swept but candidate is bullish (needs SELL_SIDE swept).
        wrong_side = _liq("liq_1", 1.1500, LiquidityType.BUY_SIDE, swept=True)
        state = _new_state(order_blocks=[BULLISH_OB], liquidity_levels=[wrong_side])
        strategy = ParametrizedSMCStrategy(_config(require_liquidity_sweep=True))
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.LIQUIDITY_NOT_SWEPT] == 1


class TestTrendFilter:
    def test_aligned_requires_matching_trend(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], trend=StructureTrend.BEARISH)
        strategy = ParametrizedSMCStrategy(
            _config(ob_direction=OBDirection.BULLISH, trend_filter=TrendFilterMode.ALIGNED)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_aligned_passes_with_matching_trend(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], trend=StructureTrend.BULLISH)
        strategy = ParametrizedSMCStrategy(
            _config(ob_direction=OBDirection.BULLISH, trend_filter=TrendFilterMode.ALIGNED)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is not None

    def test_counter_requires_opposite_trend(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], trend=StructureTrend.BULLISH)
        strategy = ParametrizedSMCStrategy(
            _config(ob_direction=OBDirection.BULLISH, trend_filter=TrendFilterMode.COUNTER)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_counter_passes_with_opposite_trend(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], trend=StructureTrend.BEARISH)
        strategy = ParametrizedSMCStrategy(
            _config(ob_direction=OBDirection.BULLISH, trend_filter=TrendFilterMode.COUNTER)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is not None

    def test_none_ignores_trend(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB], trend=StructureTrend.RANGE)
        strategy = ParametrizedSMCStrategy(
            _config(ob_direction=OBDirection.BULLISH, trend_filter=TrendFilterMode.NONE)
        )
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        result = strategy.evaluate(state)
        assert result is not None


class TestOnceUsedGuardAndTouch:
    def test_ob_used_only_once(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config())
        touch_bar = _bar(0, 1.1015, 1.1020, 1.1005, 1.1012)
        state.append_bar(touch_bar)
        first = strategy.evaluate(state)
        assert first is not None

        state.append_bar(_bar(1, 1.1015, 1.1020, 1.1005, 1.1012))
        second = strategy.evaluate(state)
        assert second is None
        assert strategy.diagnostics.rejections[RejectionReason.OB_ALREADY_USED] == 1

    def test_no_touch_rejection(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config())
        far_bar = _bar(0, 1.5000, 1.5010, 1.4990, 1.5005)
        state.append_bar(far_bar)
        result = strategy.evaluate(state)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TOUCH_DETECTED] == 1

    def test_reset_clears_used_ob_ids_and_diagnostics(self) -> None:
        state = _new_state(order_blocks=[BULLISH_OB])
        strategy = ParametrizedSMCStrategy(_config())
        state.append_bar(_bar(0, 1.1015, 1.1020, 1.1005, 1.1012))
        strategy.evaluate(state)
        assert strategy.diagnostics.setups_generated == 1

        strategy.reset()
        assert strategy.diagnostics.setups_generated == 0
        assert strategy._used_ob_ids == set()
