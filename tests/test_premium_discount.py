"""Tests for Premium / Discount Zone calculation and integration."""

from datetime import datetime, timedelta

import pytest

from application.services.market_state_builder import MarketStateBuilder
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
from smc.premium_discount import (
    PremiumDiscountCalculator,
    PremiumDiscountZone,
    ZoneType,
)
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy


def _create_bar(index: int, open_p: float, high_p: float, low_p: float, close_p: float) -> Bar:
    """Helper to generate a Bar object."""
    return Bar(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * index),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=100.0,
    )


# --- 1. PremiumDiscountCalculator Tests ---


def test_calculator_normal_bullish_range() -> None:
    """Verify classification in normal bullish range: price below equilibrium is DISCOUNT."""
    zone_info = PremiumDiscountCalculator.calculate(high=110.0, low=90.0, current_price=95.0)
    assert zone_info.high == 110.0
    assert zone_info.low == 90.0
    assert zone_info.equilibrium == 100.0
    assert zone_info.current_price == 95.0
    assert zone_info.zone == ZoneType.DISCOUNT


def test_calculator_normal_bearish_range() -> None:
    """Verify classification in normal bearish range: price above equilibrium is PREMIUM."""
    zone_info = PremiumDiscountCalculator.calculate(high=110.0, low=90.0, current_price=105.0)
    assert zone_info.high == 110.0
    assert zone_info.low == 90.0
    assert zone_info.equilibrium == 100.0
    assert zone_info.current_price == 105.0
    assert zone_info.zone == ZoneType.PREMIUM


def test_calculator_equilibrium_price() -> None:
    """Verify classification at exact equilibrium price."""
    zone_info = PremiumDiscountCalculator.calculate(high=110.0, low=90.0, current_price=100.0)
    assert zone_info.zone == ZoneType.EQUILIBRIUM


def test_calculator_invalid_high_low_values() -> None:
    """Verify that calculator raises ValueError when high <= low."""
    with pytest.raises(ValueError, match="Invalid high/low values"):
        PremiumDiscountCalculator.calculate(high=90.0, low=110.0, current_price=100.0)

    with pytest.raises(ValueError, match="Invalid high/low values"):
        PremiumDiscountCalculator.calculate(high=100.0, low=100.0, current_price=100.0)


# --- 2. MarketStateBuilder Tests ---


def test_builder_premium_discount_exists_after_build() -> None:
    """Verifies that Premium/Discount zone is calculated after a bar closed when swings exist."""
    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)

    # Setup some bars to create major swings
    state = builder.market_state

    sh = Swing(
        id="major_high",
        timestamp=datetime(2026, 1, 1),
        index=5,
        price=1.1200,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    sl = Swing(
        id="major_low",
        timestamp=datetime(2026, 1, 1),
        index=10,
        price=1.0800,
        type=SwingType.LOW,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(sh)
    state.swing_graph.add_swing(sl)

    # Append a bar
    bar = _create_bar(15, 1.1000, 1.1050, 1.0950, 1.0900)  # Close = 1.0900
    builder.append_bar(bar)

    zone = state.premium_discount_zone
    assert zone is not None
    assert zone.high == 1.1200
    assert zone.low == 1.0800
    assert zone.equilibrium == 1.1000
    assert zone.current_price == 1.0900
    assert zone.zone == ZoneType.DISCOUNT


def test_builder_missing_data_handled_safely() -> None:
    """Verifies that missing swings or data does not crash builder and sets zone to None."""
    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)

    # Initially no swings
    bar = _create_bar(0, 1.1000, 1.1050, 1.0950, 1.1000)
    builder.append_bar(bar)

    assert builder.market_state.premium_discount_zone is None


# --- 3. Strategy Tests ---


def _create_base_market_state(
    symbol: str = "EURUSD", timeframe: Timeframe = Timeframe.M15
) -> MarketState:
    state = MarketState(symbol=symbol, timeframe=timeframe)
    state.swing_graph = SwingGraph()
    return state


def _create_valid_bullish_state_with_zone(zone_type: ZoneType) -> MarketState:
    state = _create_base_market_state()
    bars = [_create_bar(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(30)]
    for bar in bars:
        state.append_bar(bar)

    state.structure_state = StructureState(
        trend=StructureTrend.BULLISH,
        confidence=0.85,
    )

    broken_swing = Swing(
        id="swing_10_high",
        timestamp=datetime(2026, 1, 1),
        index=10,
        price=1.0950,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(broken_swing)

    last_break = StructureBreak(
        break_id="break_bos_bullish",
        break_type=BreakType.BOS,
        broken_swing=broken_swing,
        breaking_bar=bars[25],
        timestamp=bars[25].timestamp,
    )
    state.structure_state.breaks_history.append(last_break)

    recent_high = Swing(
        id="swing_20_high",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * 20),
        index=20,
        price=1.1200,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(recent_high)

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

    swept_liq = LiquidityLevel(
        id="liq_sellside_1",
        price=1.0920,
        type=LiquidityType.SELL_SIDE,
        source_swing_ids=["swing_5_low"],
        is_swept=True,
    )
    state.smc_state.liquidity_levels.append(swept_liq)

    displacement = DisplacementBar(
        bar_index=28,
        timestamp=bars[28].timestamp,
        direction="BULLISH",
        range=0.0030,
        atr=0.0010,
    )
    state.smc_state.displacements.append(displacement)

    # Calculate price to match zone_type
    high_price = 1.1200
    low_price = 1.0800
    equilibrium = 1.1000

    if zone_type == ZoneType.DISCOUNT:
        close_price = 1.0900
    elif zone_type == ZoneType.PREMIUM:
        close_price = 1.1100
    else:
        close_price = 1.1000

    state.premium_discount_zone = PremiumDiscountZone(
        high=high_price,
        low=low_price,
        equilibrium=equilibrium,
        current_price=close_price,
        zone=zone_type,
    )

    return state


def _create_valid_bearish_state_with_zone(zone_type: ZoneType) -> MarketState:
    state = _create_base_market_state()
    bars = [_create_bar(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(30)]
    for bar in bars:
        state.append_bar(bar)

    state.structure_state = StructureState(
        trend=StructureTrend.BEARISH,
        confidence=0.90,
    )

    broken_swing = Swing(
        id="swing_10_low",
        timestamp=datetime(2026, 1, 1),
        index=10,
        price=1.1050,
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

    recent_low = Swing(
        id="swing_20_low",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * 20),
        index=20,
        price=1.0800,
        type=SwingType.LOW,
        classification=SwingClassification.MAJOR,
    )
    state.swing_graph.add_swing(recent_low)

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

    swept_liq = LiquidityLevel(
        id="liq_buyside_1",
        price=1.1080,
        type=LiquidityType.BUY_SIDE,
        source_swing_ids=["swing_5_high"],
        is_swept=True,
    )
    state.smc_state.liquidity_levels.append(swept_liq)

    displacement = DisplacementBar(
        bar_index=28,
        timestamp=bars[28].timestamp,
        direction="BEARISH",
        range=0.0030,
        atr=0.0010,
    )
    state.smc_state.displacements.append(displacement)

    high_price = 1.1200
    low_price = 1.0800
    equilibrium = 1.1000

    if zone_type == ZoneType.DISCOUNT:
        close_price = 1.0900
    elif zone_type == ZoneType.PREMIUM:
        close_price = 1.1100
    else:
        close_price = 1.1000

    state.premium_discount_zone = PremiumDiscountZone(
        high=high_price,
        low=low_price,
        equilibrium=equilibrium,
        current_price=close_price,
        zone=zone_type,
    )

    return state


def test_bullish_continuation_allowed_in_discount() -> None:
    """Verify BullishContinuationStrategy generates BUY setup in DISCOUNT zone."""
    state = _create_valid_bullish_state_with_zone(ZoneType.DISCOUNT)
    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(state)

    assert setup is not None
    assert setup.direction == SignalDirection.BUY


def test_bullish_continuation_rejected_in_premium() -> None:
    """Verify BullishContinuationStrategy rejects setups in PREMIUM zone."""
    state = _create_valid_bullish_state_with_zone(ZoneType.PREMIUM)
    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(state)

    assert setup is None


def test_bearish_continuation_allowed_in_premium() -> None:
    """Verify BearishContinuationStrategy generates SELL setup in PREMIUM zone."""
    state = _create_valid_bearish_state_with_zone(ZoneType.PREMIUM)
    strategy = BearishContinuationStrategy()
    setup = strategy.evaluate(state)

    assert setup is not None
    assert setup.direction == SignalDirection.SELL


def test_bearish_continuation_rejected_in_discount() -> None:
    """Verify BearishContinuationStrategy rejects setups in DISCOUNT zone."""
    state = _create_valid_bearish_state_with_zone(ZoneType.DISCOUNT)
    strategy = BearishContinuationStrategy()
    setup = strategy.evaluate(state)

    assert setup is None
