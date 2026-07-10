"""Unit tests for strategy rejection-reason diagnostics (FAZA 3.5)."""

from datetime import datetime

from core.models import Bar, Timeframe
from market_structure.structure_models import (
    BreakType,
    MarketState,
    StructureBreak,
    StructureState,
    StructureTrend,
    SwingGraph,
)
from market_structure.swing_models import Swing, SwingClassification, SwingType
from smc.fvg import FairValueGap, FVGDirection
from smc.liquidity import LiquidityLevel, LiquidityType
from smc.order_block import OBDirection, OrderBlock
from smc.premium_discount import PremiumDiscountZone, ZoneType
from strategy.continuation import (
    BearishContinuationStrategy,
    BullishContinuationStrategy,
    StrategyConfig,
)
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.strategy_engine import StrategyEngine
from tests.test_strategy_engine import (
    create_valid_bearish_market_state,
    create_valid_bullish_market_state,
)


def _minimal_state_without_bars() -> MarketState:
    """Builds a state that passes trend/zone/break gates but has no bars.

    Used to reach the NO_LATEST_BAR gate, which requires breaks_history to be
    populated but get_latest_bar() to return None.
    """
    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.swing_graph = SwingGraph()
    state.structure_state = StructureState(trend=StructureTrend.BULLISH, confidence=0.5)
    broken_swing = Swing(
        id="swing_high",
        timestamp=datetime(2026, 1, 1),
        index=1,
        price=1.1000,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    breaking_bar = Bar(
        timestamp=datetime(2026, 1, 1),
        open=1.1000,
        high=1.1010,
        low=1.0990,
        close=1.1000,
        volume=100.0,
    )
    state.structure_state.breaks_history.append(
        StructureBreak(
            break_id="break_1",
            break_type=BreakType.BOS,
            broken_swing=broken_swing,
            breaking_bar=breaking_bar,
            timestamp=datetime(2026, 1, 1),
        )
    )
    state.premium_discount_zone = PremiumDiscountZone(
        high=1.2, low=1.0, equilibrium=1.1, current_price=1.05, zone=ZoneType.DISCOUNT
    )
    return state


class TestStrategyDiagnosticsUnit:
    """Directly exercises StrategyDiagnostics accounting."""

    def test_initial_state_is_empty(self) -> None:
        diag = StrategyDiagnostics()
        assert diag.evaluations == 0
        assert diag.setups_generated == 0
        assert diag.summary()["rejections"] == {}

    def test_record_evaluation_and_setup(self) -> None:
        diag = StrategyDiagnostics()
        diag.record_evaluation()
        diag.record_setup_generated()
        summary = diag.summary()
        assert summary["evaluations"] == 1
        assert summary["setups_generated"] == 1

    def test_record_rejection_counts_by_reason(self) -> None:
        diag = StrategyDiagnostics()
        diag.record_rejection(RejectionReason.NO_TREND)
        diag.record_rejection(RejectionReason.NO_TREND)
        diag.record_rejection(RejectionReason.WRONG_ZONE)
        summary = diag.summary()
        assert summary["rejections"] == {"no_trend": 2, "wrong_zone": 1}

    def test_reset_clears_counters(self) -> None:
        diag = StrategyDiagnostics()
        diag.record_evaluation()
        diag.record_rejection(RejectionReason.NO_TREND)
        diag.record_setup_generated()
        diag.reset()
        assert diag.evaluations == 0
        assert diag.setups_generated == 0
        assert diag.summary()["rejections"] == {}


class TestBullishContinuationDiagnostics:
    """Verifies each gate in BullishContinuationStrategy records the right reason."""

    def test_successful_setup_records_no_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert strategy.diagnostics.evaluations == 1
        assert strategy.diagnostics.setups_generated == 1
        assert strategy.diagnostics.rejections == {}

    def test_no_trend_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.structure_state.trend = StructureTrend.BEARISH
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_no_premium_discount_zone_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.premium_discount_zone = None
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_PREMIUM_DISCOUNT_ZONE] == 1

    def test_wrong_zone_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        zone = state.premium_discount_zone
        state.premium_discount_zone = PremiumDiscountZone(
            high=zone.high,
            low=zone.low,
            equilibrium=zone.equilibrium,
            current_price=zone.current_price,
            zone=ZoneType.PREMIUM,
        )
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.WRONG_ZONE] == 1

    def test_no_break_history_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.structure_state.breaks_history.clear()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_BREAK_HISTORY] == 1

    def test_last_break_not_bos_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        old_break = state.structure_state.breaks_history[-1]
        state.structure_state.breaks_history[-1] = StructureBreak(
            break_id=old_break.break_id,
            break_type=BreakType.CHoCH,
            broken_swing=old_break.broken_swing,
            breaking_bar=old_break.breaking_bar,
            timestamp=old_break.timestamp,
        )
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.LAST_BREAK_NOT_BOS] == 1

    def test_break_wrong_swing_type_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        old_break = state.structure_state.breaks_history[-1]
        low_swing = Swing(
            id="swing_low_temp",
            timestamp=old_break.broken_swing.timestamp,
            index=old_break.broken_swing.index,
            price=old_break.broken_swing.price,
            type=SwingType.LOW,
        )
        state.structure_state.breaks_history[-1] = StructureBreak(
            break_id=old_break.break_id,
            break_type=old_break.break_type,
            broken_swing=low_swing,
            breaking_bar=old_break.breaking_bar,
            timestamp=old_break.timestamp,
        )
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.BREAK_WRONG_SWING_TYPE] == 1

    def test_no_latest_bar_rejection(self) -> None:
        state = _minimal_state_without_bars()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_LATEST_BAR] == 1

    def test_no_matching_order_block_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.smc_state.order_blocks.clear()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MATCHING_ORDER_BLOCK] == 1

    def test_no_matching_fvg_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.smc_state.fair_value_gaps.clear()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MATCHING_FVG] == 1

    def test_liquidity_not_swept_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.smc_state.liquidity_levels.clear()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.LIQUIDITY_NOT_SWEPT] == 1

    def test_no_displacement_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        state.smc_state.displacements.clear()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_DISPLACEMENT] == 1

    def test_non_positive_risk_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(stop_buffer_pips=-50.0)
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NON_POSITIVE_RISK] == 1

    def test_rr_gate_failed_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(min_risk_reward_ratio=10.0)
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.RR_GATE_FAILED] == 1

    def test_default_max_break_age_bars_disables_stale_break_gating(self) -> None:
        # create_valid_bullish_market_state(): broken_swing.index=10, 30 bars ->
        # swing_age = 29 - 10 = 19. Default (None) must never reject on this.
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy()
        assert strategy.max_break_age_bars is None
        assert strategy.evaluate(state) is not None
        assert strategy.diagnostics.rejections == {}

    def test_stale_break_rejection(self) -> None:
        # swing_age = 19 > 10 -> rejected.
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(max_break_age_bars=10)
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.STALE_BREAK] == 1

    def test_break_age_exactly_at_limit_is_allowed(self) -> None:
        # swing_age = 19, limit = 19 -> not > limit, so it passes through.
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(max_break_age_bars=19)
        setup = strategy.evaluate(state)
        assert setup is not None
        assert strategy.diagnostics.rejections == {}

    def test_break_age_one_over_limit_is_rejected(self) -> None:
        # swing_age = 19, limit = 18 -> 19 > 18, rejected.
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(max_break_age_bars=18)
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.STALE_BREAK] == 1

    def test_stale_break_gating_via_strategy_config_overlay(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy(config=StrategyConfig(max_break_age_bars=10))
        assert strategy.max_break_age_bars == 10
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.STALE_BREAK] == 1

    def test_duplicate_setup_rejection(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is not None
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.DUPLICATE_SETUP] == 1
        assert strategy.diagnostics.evaluations == 2
        assert strategy.diagnostics.setups_generated == 1

    def test_reset_clears_diagnostics_alongside_duplicate_guard(self) -> None:
        state = create_valid_bullish_market_state()
        strategy = BullishContinuationStrategy()
        strategy.evaluate(state)
        strategy.evaluate(state)  # duplicate rejection
        assert strategy.diagnostics.rejections[RejectionReason.DUPLICATE_SETUP] == 1

        strategy.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.setups_generated == 0
        assert strategy.diagnostics.rejections == {}

        # After reset, the duplicate guard is also clear so a fresh setup is emitted.
        assert strategy.evaluate(state) is not None
        assert strategy.diagnostics.setups_generated == 1


class TestBearishContinuationDiagnostics:
    """Verifies the bearish mirror of the same gates."""

    def test_successful_setup_records_no_rejection(self) -> None:
        state = create_valid_bearish_market_state()
        strategy = BearishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert strategy.diagnostics.evaluations == 1
        assert strategy.diagnostics.setups_generated == 1
        assert strategy.diagnostics.rejections == {}

    def test_no_trend_rejection(self) -> None:
        state = create_valid_bearish_market_state()
        state.structure_state.trend = StructureTrend.BULLISH
        strategy = BearishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_TREND] == 1

    def test_liquidity_not_swept_rejection(self) -> None:
        state = create_valid_bearish_market_state()
        state.smc_state.liquidity_levels.clear()
        strategy = BearishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.LIQUIDITY_NOT_SWEPT] == 1

    def test_break_wrong_swing_type_rejection(self) -> None:
        state = create_valid_bearish_market_state()
        old_break = state.structure_state.breaks_history[-1]
        high_swing = Swing(
            id="swing_temp",
            timestamp=old_break.broken_swing.timestamp,
            index=old_break.broken_swing.index,
            price=old_break.broken_swing.price,
            type=SwingType.HIGH,
        )
        state.structure_state.breaks_history[-1] = StructureBreak(
            break_id=old_break.break_id,
            break_type=old_break.break_type,
            broken_swing=high_swing,
            breaking_bar=old_break.breaking_bar,
            timestamp=old_break.timestamp,
        )
        strategy = BearishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.BREAK_WRONG_SWING_TYPE] == 1

    def test_stale_break_rejection(self) -> None:
        # create_valid_bearish_market_state(): broken_swing.index=10, 30 bars ->
        # swing_age = 29 - 10 = 19 > 10 -> rejected.
        state = create_valid_bearish_market_state()
        strategy = BearishContinuationStrategy(max_break_age_bars=10)
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.STALE_BREAK] == 1

    def test_break_age_exactly_at_limit_is_allowed(self) -> None:
        state = create_valid_bearish_market_state()
        strategy = BearishContinuationStrategy(max_break_age_bars=19)
        assert strategy.evaluate(state) is not None
        assert strategy.diagnostics.rejections == {}


class TestBug23TrendBreakAsynchrony:
    """Differential tests for Bug #23 (trend/break asynchrony).

    Reproduces the scenario recorded in walkthrough.md: `structure_engine.
    check_structural_break()` is bar-driven and fires on a raw bar close
    crossing a MAJOR level, independent of `update()`'s swing-driven trend
    machine. A transient bar can cross a MAJOR low and get permanently
    recorded as a CHoCH while `current_trend` happens to be BULLISH, then
    the trend keeps reconfirming BULLISH off newer swings for many bars
    without any new break event -- leaving that stale CHoCH as
    `breaks_history[-1]` long after it stopped being structurally relevant.

    Old behavior (`breaks[-1]` taken unconditionally) rejected these bars
    with LAST_BREAK_NOT_BOS even when a valid, trend-confirming BOS was
    sitting earlier in the same history. The fix (`_find_latest_matching_bos`)
    scans backward for the latest BOS matching the trend direction instead.
    """

    def test_skips_stale_choch_to_find_earlier_matching_bos_bullish(self) -> None:
        """Bar 41 CHoCH (stale) is last; a real bullish BOS sits earlier -> found."""
        state = create_valid_bullish_market_state()
        valid_bos = state.structure_state.breaks_history[-1]
        assert valid_bos.break_id == "break_bos_bullish"
        assert valid_bos.break_type == BreakType.BOS
        assert valid_bos.broken_swing.type == SwingType.HIGH

        # Simulate the "Bar 41" stale CHoCH: a raw bar-close crossing a MAJOR
        # low while trend was already BULLISH, recorded chronologically AFTER
        # the real BOS, with no new swing/BOS event since.
        stale_low_swing = Swing(
            id="swing_27_low",
            timestamp=state.bars[27].timestamp,
            index=27,
            price=1.0950,
            type=SwingType.LOW,
            classification=SwingClassification.MAJOR,
        )
        stale_choch = StructureBreak(
            break_id="break_choch_stale",
            break_type=BreakType.CHoCH,
            broken_swing=stale_low_swing,
            breaking_bar=state.bars[28],
            timestamp=state.bars[28].timestamp,
        )
        state.structure_state.breaks_history.append(stale_choch)
        assert state.structure_state.breaks_history[-1] is stale_choch

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        # Old code (breaks[-1] == stale_choch, type CHoCH) would have
        # rejected this as LAST_BREAK_NOT_BOS. The fix finds the earlier
        # matching BOS instead, so the setup is generated.
        assert setup is not None
        assert strategy.diagnostics.rejections == {}
        assert setup.related_structure_break.break_id == "break_bos_bullish"

    def test_skips_stale_choch_to_find_earlier_matching_bos_bearish(self) -> None:
        """Bearish mirror: stale CHoCH-HIGH is last, real bearish BOS sits earlier."""
        state = create_valid_bearish_market_state()
        valid_bos = state.structure_state.breaks_history[-1]
        assert valid_bos.break_id == "break_bos_bearish"

        stale_high_swing = Swing(
            id="swing_27_high",
            timestamp=state.bars[27].timestamp,
            index=27,
            price=1.1050,
            type=SwingType.HIGH,
            classification=SwingClassification.MAJOR,
        )
        stale_choch = StructureBreak(
            break_id="break_choch_stale",
            break_type=BreakType.CHoCH,
            broken_swing=stale_high_swing,
            breaking_bar=state.bars[28],
            timestamp=state.bars[28].timestamp,
        )
        state.structure_state.breaks_history.append(stale_choch)

        strategy = BearishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert strategy.diagnostics.rejections == {}
        assert setup.related_structure_break.break_id == "break_bos_bearish"

    def test_no_matching_bos_anywhere_in_history_still_rejected(self) -> None:
        """No real BOS exists in either entry -> must still reject (no false accept)."""
        state = create_valid_bullish_market_state()
        # Downgrade the one valid BOS to a CHoCH, then append the stale
        # CHoCH exactly as in the "found" test above: neither entry is a
        # matching BOS anywhere in history, so the fix must not invent one.
        old_bos = state.structure_state.breaks_history[-1]
        state.structure_state.breaks_history[-1] = StructureBreak(
            break_id=old_bos.break_id,
            break_type=BreakType.CHoCH,
            broken_swing=old_bos.broken_swing,
            breaking_bar=old_bos.breaking_bar,
            timestamp=old_bos.timestamp,
        )
        stale_low_swing = Swing(
            id="swing_27_low",
            timestamp=state.bars[27].timestamp,
            index=27,
            price=1.0950,
            type=SwingType.LOW,
            classification=SwingClassification.MAJOR,
        )
        state.structure_state.breaks_history.append(
            StructureBreak(
                break_id="break_choch_stale",
                break_type=BreakType.CHoCH,
                broken_swing=stale_low_swing,
                breaking_bar=state.bars[28],
                timestamp=state.bars[28].timestamp,
            )
        )

        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.LAST_BREAK_NOT_BOS] == 1

    def test_multiple_breaks_wrong_direction_bos_rejected_as_wrong_swing_type(self) -> None:
        """A BOS exists in history, but breaking a LOW (wrong direction for bullish);
        must reject as BREAK_WRONG_SWING_TYPE, not silently match it."""
        state = create_valid_bullish_market_state()
        old_bos = state.structure_state.breaks_history[-1]
        wrong_direction_low = Swing(
            id="swing_10_low_wrong",
            timestamp=old_bos.broken_swing.timestamp,
            index=old_bos.broken_swing.index,
            price=1.0900,
            type=SwingType.LOW,
            classification=SwingClassification.MAJOR,
        )
        state.structure_state.breaks_history[-1] = StructureBreak(
            break_id=old_bos.break_id,
            break_type=BreakType.BOS,
            broken_swing=wrong_direction_low,
            breaking_bar=old_bos.breaking_bar,
            timestamp=old_bos.timestamp,
        )
        stale_low_swing = Swing(
            id="swing_27_low",
            timestamp=state.bars[27].timestamp,
            index=27,
            price=1.0950,
            type=SwingType.LOW,
            classification=SwingClassification.MAJOR,
        )
        state.structure_state.breaks_history.append(
            StructureBreak(
                break_id="break_choch_stale",
                break_type=BreakType.CHoCH,
                broken_swing=stale_low_swing,
                breaking_bar=state.bars[28],
                timestamp=state.bars[28].timestamp,
            )
        )

        strategy = BullishContinuationStrategy()
        assert strategy.evaluate(state) is None
        assert strategy.diagnostics.rejections[RejectionReason.BREAK_WRONG_SWING_TYPE] == 1


class TestStrategyEngineDiagnosticsAggregation:
    """Verifies StrategyEngine.get_diagnostics() aggregates per-strategy summaries."""

    def test_aggregates_across_strategies_with_unique_keys(self) -> None:
        engine = StrategyEngine()
        bullish = BullishContinuationStrategy()
        bearish = BearishContinuationStrategy()
        engine.register_strategy(bullish)
        engine.register_strategy(bearish)

        bullish_state = create_valid_bullish_market_state()
        engine.run(bullish_state)

        diagnostics = engine.get_diagnostics()
        assert set(diagnostics.keys()) == {
            "0_BullishContinuationStrategy",
            "1_BearishContinuationStrategy",
        }
        assert diagnostics["0_BullishContinuationStrategy"]["setups_generated"] == 1
        assert diagnostics["1_BearishContinuationStrategy"]["setups_generated"] == 0
        assert diagnostics["1_BearishContinuationStrategy"]["rejections"] == {"no_trend": 1}

    def test_aggregates_duplicate_strategy_classes_with_distinct_keys(self) -> None:
        engine = StrategyEngine()
        engine.register_strategy(BullishContinuationStrategy())
        engine.register_strategy(BullishContinuationStrategy())

        state = create_valid_bullish_market_state()
        engine.run(state)

        diagnostics = engine.get_diagnostics()
        assert set(diagnostics.keys()) == {
            "0_BullishContinuationStrategy",
            "1_BullishContinuationStrategy",
        }
        assert diagnostics["0_BullishContinuationStrategy"]["setups_generated"] == 1
        assert diagnostics["1_BullishContinuationStrategy"]["setups_generated"] == 1

    def test_engine_reset_propagates_to_diagnostics(self) -> None:
        engine = StrategyEngine()
        strategy = BullishContinuationStrategy()
        engine.register_strategy(strategy)

        state = create_valid_bullish_market_state()
        engine.run(state)
        engine.run(state)  # second call rejected as duplicate

        assert strategy.diagnostics.rejections[RejectionReason.DUPLICATE_SETUP] == 1

        engine.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}
