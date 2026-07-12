"""Unit tests for the Strategy Engine and SMC Continuation strategies."""

from datetime import datetime, timedelta

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import (
    BreakType,
    MarketState,
    StructureBreak,
    StructureState,
    StructureTrend,
    SwingGraph,
)
from market_structure.swing_models import Swing, SwingClassification, SwingType
from smc.displacement import DisplacementBar
from smc.fvg import FairValueGap, FVGDirection
from smc.liquidity import LiquidityLevel, LiquidityType
from smc.order_block import OBDirection, OrderBlock
from smc.premium_discount import PremiumDiscountZone, ZoneType
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.models import TradeSetup
from strategy.strategy_engine import StrategyEngine


def _create_base_market_state(
    symbol: str = "EURUSD", timeframe: Timeframe = Timeframe.M15
) -> MarketState:
    """Helper to create a minimal base MarketState."""
    state = MarketState(symbol=symbol, timeframe=timeframe)
    # Populate swing graph swings lookup dict via post-init if necessary
    state.swing_graph = SwingGraph()
    return state


def _create_bar(index: int, close_p: float) -> Bar:
    """Helper to generate a simple Bar object."""
    return Bar(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * index),
        open=close_p,
        high=close_p + 0.0005,
        low=close_p - 0.0005,
        close=close_p,
        volume=100.0,
    )


def create_valid_bullish_market_state() -> MarketState:
    """Creates a MarketState that satisfies all bullish continuation rules."""
    state = _create_base_market_state()

    # 1. Bars
    bars = [_create_bar(i, 1.1000) for i in range(30)]
    for bar in bars:
        state.append_bar(bar)

    # 2. Trend = BULLISH
    state.structure_state = StructureState(
        trend=StructureTrend.BULLISH,
        confidence=0.85,
    )

    # 3. Last break is Bullish BOS breaking a HIGH swing
    broken_swing = Swing(
        id="swing_10_high",
        timestamp=datetime(2026, 1, 1),
        index=10,
        price=1.1100,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    # Add swing to swing graph
    state.swing_graph.add_swing(broken_swing)

    last_break = StructureBreak(
        break_id="break_bos_bullish",
        break_type=BreakType.BOS,
        broken_swing=broken_swing,
        breaking_bar=bars[25],
        timestamp=bars[25].timestamp,
    )
    state.structure_state.breaks_history.append(last_break)

    # Also add a swing high as recent target
    recent_high = Swing(
        id="swing_20_high",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * 20),
        index=20,
        price=1.1200,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(recent_high)

    # 4. Price inside ACTIVE bullish order block
    # Latest bar close is 1.1000. OB covers 1.0990 to 1.1010.
    active_ob = OrderBlock(
        id="ob_15_bullish",
        bar_index=15,
        high=1.1010,
        low=1.0990,
        direction=OBDirection.BULLISH,
        timestamp=bars[15].timestamp,
        is_mitigated=False,
    )
    state.smc_state.order_blocks.append(active_ob)

    # 5. Nearby ACTIVE bullish FVG
    # Upper price = 1.0980. Close = 1.1000. Distance is 0.0020 (20 pips <= 50.0 pips threshold).
    active_fvg = FairValueGap(
        id="fvg_12_bullish",
        start_index=11,
        end_index=13,
        upper_price=1.0980,
        lower_price=1.0960,
        direction=FVGDirection.BULLISH,
        timestamp=bars[12].timestamp,
        is_mitigated=False,
    )
    state.smc_state.fair_value_gaps.append(active_fvg)

    # 6. Sell-side liquidity already swept
    swept_liq = LiquidityLevel(
        id="liq_sellside_1",
        price=1.0920,
        type=LiquidityType.SELL_SIDE,
        source_swing_ids=["swing_5_low"],
        is_swept=True,
    )
    state.smc_state.liquidity_levels.append(swept_liq)

    # 7. Recent bullish displacement exists
    # Current latest index is 29. Displacement at 28 is recent (lookback 20)
    displacement = DisplacementBar(
        bar_index=28,
        timestamp=bars[28].timestamp,
        direction="BULLISH",
        range=0.0030,
        atr=0.0010,
    )
    state.smc_state.displacements.append(displacement)

    state.premium_discount_zone = PremiumDiscountZone(
        high=1.1200,
        low=1.0800,
        equilibrium=1.1000,
        current_price=1.0900,
        zone=ZoneType.DISCOUNT,
    )

    return state


def create_valid_bearish_market_state() -> MarketState:
    """Creates a MarketState that satisfies all bearish continuation rules."""
    state = _create_base_market_state()

    # 1. Bars
    bars = [_create_bar(i, 1.1000) for i in range(30)]
    for bar in bars:
        state.append_bar(bar)

    # 2. Trend = BEARISH
    state.structure_state = StructureState(
        trend=StructureTrend.BEARISH,
        confidence=0.90,
    )

    # 3. Last break is Bearish BOS breaking a LOW swing
    broken_swing = Swing(
        id="swing_10_low",
        timestamp=datetime(2026, 1, 1),
        index=10,
        price=1.0900,
        type=SwingType.LOW,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(broken_swing)

    last_break = StructureBreak(
        break_id="break_bos_bearish",
        break_type=BreakType.BOS,
        broken_swing=broken_swing,
        breaking_bar=bars[25],
        timestamp=bars[25].timestamp,
    )
    state.structure_state.breaks_history.append(last_break)

    # Also add a swing low as recent target
    recent_low = Swing(
        id="swing_20_low",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * 20),
        index=20,
        price=1.0800,
        type=SwingType.LOW,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(recent_low)

    # 4. Price inside ACTIVE bearish order block
    # Latest bar close is 1.1000. OB covers 1.0990 to 1.1010.
    active_ob = OrderBlock(
        id="ob_15_bearish",
        bar_index=15,
        high=1.1010,
        low=1.0990,
        direction=OBDirection.BEARISH,
        timestamp=bars[15].timestamp,
        is_mitigated=False,
    )
    state.smc_state.order_blocks.append(active_ob)

    # 5. Nearby ACTIVE bearish FVG
    # Lower price = 1.1020. Close = 1.1000. Distance is 0.0020 (20 pips <= 50.0 pips threshold).
    active_fvg = FairValueGap(
        id="fvg_12_bearish",
        start_index=11,
        end_index=13,
        upper_price=1.1040,
        lower_price=1.1020,
        direction=FVGDirection.BEARISH,
        timestamp=bars[12].timestamp,
        is_mitigated=False,
    )
    state.smc_state.fair_value_gaps.append(active_fvg)

    # 6. Buy-side liquidity already swept
    swept_liq = LiquidityLevel(
        id="liq_buyside_1",
        price=1.1080,
        type=LiquidityType.BUY_SIDE,
        source_swing_ids=["swing_5_high"],
        is_swept=True,
    )
    state.smc_state.liquidity_levels.append(swept_liq)

    # 7. Recent bearish displacement exists
    # Current latest index is 29. Displacement at 28 is recent (lookback 20)
    displacement = DisplacementBar(
        bar_index=28,
        timestamp=bars[28].timestamp,
        direction="BEARISH",
        range=0.0030,
        atr=0.0010,
    )
    state.smc_state.displacements.append(displacement)

    state.premium_discount_zone = PremiumDiscountZone(
        high=1.1200,
        low=1.0800,
        equilibrium=1.1000,
        current_price=1.1100,
        zone=ZoneType.PREMIUM,
    )

    return state


# ==============================================================================
# TESTS
# ==============================================================================


def test_bullish_continuation_success() -> None:
    """Verifies a successful bullish continuation setup detection."""
    market_state = create_valid_bullish_market_state()
    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(market_state)

    assert setup is not None
    assert setup.symbol == "EURUSD"
    assert setup.timeframe == Timeframe.M15
    assert setup.direction == SignalDirection.BUY
    assert setup.entry_zone == (1.0990, 1.1010)
    # Stop zone: OB Low (1.0990) minus buffer (5 pips = 0.0005) -> (1.0985, 1.0990)
    assert setup.stop_zone == (1.0985, 1.0990)
    # Target zone: Recent high (1.1200) -> (1.1200, 1.1205)
    assert setup.target_zone == (1.1200, 1.1205)
    assert setup.confidence_score == 0.85
    assert "Bullish Trend" in setup.confluence
    assert "Inside Bullish OB" in setup.confluence
    assert setup.related_structure_break is not None
    assert setup.related_order_block is not None
    assert setup.related_fvg is not None


def test_bearish_continuation_success() -> None:
    """Verifies a successful bearish continuation setup detection."""
    market_state = create_valid_bearish_market_state()
    strategy = BearishContinuationStrategy()
    setup = strategy.evaluate(market_state)

    assert setup is not None
    assert setup.symbol == "EURUSD"
    assert setup.timeframe == Timeframe.M15
    assert setup.direction == SignalDirection.SELL
    assert setup.entry_zone == (1.0990, 1.1010)
    # Stop zone: OB High (1.1010) plus buffer (5 pips = 0.0005) -> (1.1010, 1.1015)
    assert setup.stop_zone == (1.1010, 1.1015)
    # Target zone: Recent low (1.0800) -> (1.0795, 1.0800)
    assert setup.target_zone == (1.0795, 1.0800)
    assert setup.confidence_score == 0.90
    assert "Bearish Trend" in setup.confluence
    assert "Inside Bearish OB" in setup.confluence
    assert setup.related_structure_break is not None
    assert setup.related_order_block is not None
    assert setup.related_fvg is not None


def test_bullish_trend_rejection() -> None:
    """Rejects if trend is not bullish."""
    market_state = create_valid_bullish_market_state()
    market_state.structure_state.trend = StructureTrend.BEARISH

    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None


def test_bullish_missing_bos_rejection() -> None:
    """Rejects if there are no structural breaks or the last break is not BOS."""
    # Case A: No breaks
    market_state = create_valid_bullish_market_state()
    market_state.structure_state.breaks_history.clear()
    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None

    # Case B: Last break is CHoCH
    market_state = create_valid_bullish_market_state()
    old_break = market_state.structure_state.breaks_history[-1]
    choch_break = StructureBreak(
        break_id=old_break.break_id,
        break_type=BreakType.CHoCH,
        broken_swing=old_break.broken_swing,
        breaking_bar=old_break.breaking_bar,
        timestamp=old_break.timestamp,
    )
    market_state.structure_state.breaks_history[-1] = choch_break
    assert strategy.evaluate(market_state) is None


def test_bullish_wrong_break_type_rejection() -> None:
    """Rejects if the BOS break broke a LOW swing instead of HIGH for bullish."""
    market_state = create_valid_bullish_market_state()
    old_break = market_state.structure_state.breaks_history[-1]
    low_swing = Swing(
        id="swing_low_temp",
        timestamp=old_break.broken_swing.timestamp,
        index=old_break.broken_swing.index,
        price=old_break.broken_swing.price,
        type=SwingType.LOW,
    )
    bad_break = StructureBreak(
        break_id=old_break.break_id,
        break_type=old_break.break_type,
        broken_swing=low_swing,
        breaking_bar=old_break.breaking_bar,
        timestamp=old_break.timestamp,
    )
    market_state.structure_state.breaks_history[-1] = bad_break

    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None


def test_bullish_missing_ob_rejection() -> None:
    """Rejects if price is not inside an active unmitigated bullish OB."""
    # Case A: OB mitigated
    market_state = create_valid_bullish_market_state()
    ob = market_state.smc_state.order_blocks[0]
    market_state.smc_state.order_blocks[0] = OrderBlock(
        id=ob.id,
        bar_index=ob.bar_index,
        high=ob.high,
        low=ob.low,
        direction=ob.direction,
        timestamp=ob.timestamp,
        is_mitigated=True,
    )
    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None

    # Case B: Price outside OB bounds
    market_state = create_valid_bullish_market_state()
    # OB bounds do not cover close price (1.1000)
    market_state.smc_state.order_blocks[0] = OrderBlock(
        id=ob.id,
        bar_index=ob.bar_index,
        high=1.0950,
        low=1.0900,
        direction=OBDirection.BULLISH,
        timestamp=ob.timestamp,
        is_mitigated=False,
    )
    assert strategy.evaluate(market_state) is None


def test_bullish_missing_fvg_rejection() -> None:
    """Rejects if there is no bullish FVG or it is too far away."""
    # Case A: FVG mitigated
    market_state = create_valid_bullish_market_state()
    fvg = market_state.smc_state.fair_value_gaps[0]
    market_state.smc_state.fair_value_gaps[0] = FairValueGap(
        id=fvg.id,
        start_index=fvg.start_index,
        end_index=fvg.end_index,
        upper_price=fvg.upper_price,
        lower_price=fvg.lower_price,
        direction=fvg.direction,
        timestamp=fvg.timestamp,
        is_mitigated=True,
    )
    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None

    # Case B: FVG too far (upper edge 1.0900 vs close 1.1000 is 100 pips > 50 pips threshold)
    market_state = create_valid_bullish_market_state()
    market_state.smc_state.fair_value_gaps[0] = FairValueGap(
        id=fvg.id,
        start_index=fvg.start_index,
        end_index=fvg.end_index,
        upper_price=1.0900,
        lower_price=1.0880,
        direction=FVGDirection.BULLISH,
        timestamp=fvg.timestamp,
        is_mitigated=False,
    )
    assert strategy.evaluate(market_state) is None


def test_bullish_missing_liquidity_rejection() -> None:
    """Rejects if sell-side liquidity has not been swept."""
    # Case A: No swept liquidity
    market_state = create_valid_bullish_market_state()
    market_state.smc_state.liquidity_levels.clear()
    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None

    # Case B: Liquidity is BUY_SIDE
    market_state = create_valid_bullish_market_state()
    market_state.smc_state.liquidity_levels[0] = LiquidityLevel(
        id="liq_buyside",
        price=1.1200,
        type=LiquidityType.BUY_SIDE,
        source_swing_ids=["s1"],
        is_swept=True,
    )
    assert strategy.evaluate(market_state) is None


def test_bullish_missing_displacement_rejection() -> None:
    """Rejects if there is no recent bullish displacement."""
    # Case A: No displacement
    market_state = create_valid_bullish_market_state()
    market_state.smc_state.displacements.clear()
    strategy = BullishContinuationStrategy()
    assert strategy.evaluate(market_state) is None

    # Case B: Displacement too old (outside lookback)
    market_state = create_valid_bullish_market_state()
    # Displacement at bar_index 5 (latest is 29, lookback is 20 -> 24 is max age)
    market_state.smc_state.displacements[0] = DisplacementBar(
        bar_index=5,
        timestamp=market_state.bars[5].timestamp,
        direction="BULLISH",
        range=0.0030,
        atr=0.0010,
    )
    assert strategy.evaluate(market_state) is None


def test_bearish_rejections() -> None:
    """Verifies that bearish strategy rejects when conditions fail."""
    strategy = BearishContinuationStrategy()

    # Case 1: Trend is bullish
    market_state = create_valid_bearish_market_state()
    market_state.structure_state.trend = StructureTrend.BULLISH
    assert strategy.evaluate(market_state) is None

    # Case 2: Break broke a HIGH
    market_state = create_valid_bearish_market_state()
    old_break = market_state.structure_state.breaks_history[-1]
    high_swing = Swing(
        id="swing_temp",
        timestamp=old_break.broken_swing.timestamp,
        index=old_break.broken_swing.index,
        price=old_break.broken_swing.price,
        type=SwingType.HIGH,
    )
    market_state.structure_state.breaks_history[-1] = StructureBreak(
        break_id=old_break.break_id,
        break_type=old_break.break_type,
        broken_swing=high_swing,
        breaking_bar=old_break.breaking_bar,
        timestamp=old_break.timestamp,
    )
    assert strategy.evaluate(market_state) is None

    # Case 3: No buy-side liquidity swept
    market_state = create_valid_bearish_market_state()
    market_state.smc_state.liquidity_levels.clear()
    assert strategy.evaluate(market_state) is None


def test_strategy_engine_orchestration() -> None:
    """Verifies registration, execution, and list collection in StrategyEngine."""
    engine = StrategyEngine()
    assert len(engine.strategies) == 0

    bullish_strat = BullishContinuationStrategy()
    bearish_strat = BearishContinuationStrategy()

    engine.register_strategy(bullish_strat)
    engine.register_strategy(bearish_strat)
    assert len(engine.strategies) == 2

    # Run on bullish state -> returns exactly 1 bullish TradeSetup, ignores bearish None
    bullish_state = create_valid_bullish_market_state()
    setups = engine.run(bullish_state)
    assert len(setups) == 1
    assert setups[0].direction == SignalDirection.BUY

    # Run on bearish state -> returns exactly 1 bearish TradeSetup, ignores bullish None
    bearish_state = create_valid_bearish_market_state()
    setups = engine.run(bearish_state)
    assert len(setups) == 1
    assert setups[0].direction == SignalDirection.SELL


def test_strategy_engine_empty_or_multiple_setups() -> None:
    """Verifies empty and multiple setup cases."""
    engine = StrategyEngine()
    # Case A: Empty registration
    state = create_valid_bullish_market_state()
    assert len(engine.run(state)) == 0

    # Case B: Multiple registered strategies generating setups
    # Register two identical strategies to simulate multiple candidates
    engine.register_strategy(BullishContinuationStrategy())
    engine.register_strategy(BullishContinuationStrategy())
    setups = engine.run(state)
    assert len(setups) == 2
    assert setups[0].setup_id != setups[1].setup_id  # UUIDs / timestamps create uniqueness


def test_trade_setup_immutability() -> None:
    """Verifies TradeSetup is a frozen dataclass and cannot be modified."""
    setup = TradeSetup(
        setup_id="id",
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        direction=SignalDirection.BUY,
        entry_zone=(1.10, 1.11),
        stop_zone=(1.09, 1.10),
        target_zone=(1.12, 1.13),
        confidence_score=0.8,
        confluence=[],
        trigger_reason="reason",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime.now(),
    )

    with pytest.raises(AttributeError):
        setup.symbol = "GBPUSD"  # type: ignore


def test_no_mutation_of_market_state() -> None:
    """Verifies that strategy evaluation is strictly read-only and does not mutate state."""
    market_state = create_valid_bullish_market_state()

    # Take structural copies of list lengths
    num_bars = len(market_state.bars)
    num_breaks = len(market_state.structure_state.breaks_history)
    num_obs = len(market_state.smc_state.order_blocks)
    num_fvgs = len(market_state.smc_state.fair_value_gaps)
    num_liq = len(market_state.smc_state.liquidity_levels)
    num_disp = len(market_state.smc_state.displacements)

    strategy = BullishContinuationStrategy()
    strategy.evaluate(market_state)

    # Assert structural integrity remains identical
    assert len(market_state.bars) == num_bars
    assert len(market_state.structure_state.breaks_history) == num_breaks
    assert len(market_state.smc_state.order_blocks) == num_obs
    assert len(market_state.smc_state.fair_value_gaps) == num_fvgs
    assert len(market_state.smc_state.liquidity_levels) == num_liq
    assert len(market_state.smc_state.displacements) == num_disp


def test_strategy_duplicate_setup_guard() -> None:
    """Verifies that duplicate setups are guarded and reset() clears the guard memory."""
    market_state = create_valid_bullish_market_state()
    strategy = BullishContinuationStrategy()

    # 1. Call evaluate 3 times
    setup1 = strategy.evaluate(market_state)
    assert setup1 is not None

    setup2 = strategy.evaluate(market_state)
    assert setup2 is None

    setup3 = strategy.evaluate(market_state)
    assert setup3 is None

    # 2. Call reset
    strategy.reset()

    # 3. Call evaluate again -> should emit setup again
    setup4 = strategy.evaluate(market_state)
    assert setup4 is not None
    assert setup4.setup_id != setup1.setup_id  # Should have a fresh UUID/timestamp


def test_strategy_risk_reward_gate() -> None:
    """Verifies that setups with insufficient R:R or invalid risk distances are filtered out."""
    market_state = create_valid_bullish_market_state()

    # 1. R:R is ~7.6 (reward 0.0190 / risk 0.0025 = 7.6)
    # min_risk_reward_ratio = 5.0 (should pass)
    strategy_pass = BullishContinuationStrategy(min_risk_reward_ratio=5.0)
    setup_pass = strategy_pass.evaluate(market_state)
    assert setup_pass is not None

    # 2. min_risk_reward_ratio = 10.0 (should be filtered)
    strategy_fail = BullishContinuationStrategy(min_risk_reward_ratio=10.0)
    setup_fail = strategy_fail.evaluate(market_state)
    assert setup_fail is None

    # 3. Invalid/negative risk distance: stop buffer pips is extremely negative so stop loss is above entry price.
    # stop_buffer_pips = -50.0 -> stop_zone[0] = 1.0990 - (-0.0050) = 1.1040
    # entry_zone[1] = 1.1010. Risk distance = 1.1010 - 1.1040 = -0.0030 <= 0
    strategy_invalid_risk = BullishContinuationStrategy(stop_buffer_pips=-50.0)
    setup_invalid = strategy_invalid_risk.evaluate(market_state)
    assert setup_invalid is None


def test_strategy_timestamp_determinism_and_bar_time() -> None:
    """Verifies that strategy evaluation is deterministic and uses historical bar timestamps."""
    market_state = create_valid_bullish_market_state()

    strategy1 = BullishContinuationStrategy()
    strategy2 = BullishContinuationStrategy()

    # Reset strategy memory
    strategy1.reset()
    strategy2.reset()

    # Assert get_latest_bar() is not None during the normal flow
    latest_bar = market_state.get_latest_bar()
    assert latest_bar is not None, "get_latest_bar() must not be None in a valid market state"

    # Evaluate sequentially on the same market state
    setup1 = strategy1.evaluate(market_state)
    assert setup1 is not None

    # Reset memory of proposed keys for the second strategy to prevent it from filtering out the same OB
    strategy2.reset()
    setup2 = strategy2.evaluate(market_state)
    assert setup2 is not None

    # Verify TradeSetup timestamp is deterministic
    assert setup1.timestamp == setup2.timestamp, (
        "TradeSetup timestamps must be identical for identical inputs"
    )

    # Verify TradeSetup timestamp matches the latest bar timestamp exactly
    assert setup1.timestamp == latest_bar.timestamp, (
        "TradeSetup timestamp must match the latest bar's timestamp"
    )


def test_continuation_strategies_recommended_max_holding_bars_defaults_to_none():
    """BullishContinuationStrategy/BearishContinuationStrategy aren't fixed-
    time-of-day session strategies and don't override
    recommended_max_holding_bars() -- must inherit the TradeSetupStrategy
    Protocol's default of None (unlimited holding).
    """
    bullish = BullishContinuationStrategy()
    bearish = BearishContinuationStrategy()

    assert bullish.recommended_max_holding_bars(Timeframe.M15) is None
    assert bearish.recommended_max_holding_bars(Timeframe.M15) is None
