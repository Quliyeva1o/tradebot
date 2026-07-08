"""Audit requirements verification tests.

This test file verifies the specific requirements of the architecture audit
for both MarketState Builder and StrategyEvaluator.
"""

from datetime import datetime, timedelta

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
from smc.premium_discount import PremiumDiscountZone, ZoneType
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.strategy_engine import StrategyEngine


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


# --- MARKETSTATE AUDIT TESTS ---


def test_market_state_empty_candle_data() -> None:
    """Verifies that empty candle data is handled gracefully."""
    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)
    builder.initialize([])

    assert len(builder.market_state.bars) == 0
    assert builder.market_state.get_latest_bar() is None


def test_market_state_insufficient_candles() -> None:
    """Verifies that insufficient candle data behaves gracefully without crashes and detects nothing."""
    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)

    # 1 candle
    builder.initialize([_create_bar(0, 1.1000, 1.1050, 1.0950, 1.1000)])
    assert len(builder.market_state.bars) == 1
    assert len(builder.market_state.smc_state.fair_value_gaps) == 0
    assert len(builder.market_state.smc_state.order_blocks) == 0
    assert len(builder.market_state.smc_state.displacements) == 0


def test_market_state_normal_bullish_and_bearish_structure() -> None:
    """Verifies normal bullish and bearish trend and structure identification."""
    builder = MarketStateBuilder(symbol="EURUSD", timeframe=Timeframe.M15)

    state = builder.market_state

    # Bullish trend setup
    state.structure_state = StructureState(trend=StructureTrend.BULLISH, confidence=0.8)
    assert state.structure_state.trend == StructureTrend.BULLISH

    # Bearish trend setup
    state.structure_state = StructureState(trend=StructureTrend.BEARISH, confidence=0.9)
    assert state.structure_state.trend == StructureTrend.BEARISH


def test_market_state_bos_and_choch() -> None:
    """Verifies BOS and CHoCH detection and transition."""
    broken_swing = Swing(
        id="swing_high",
        timestamp=datetime(2026, 1, 1),
        index=5,
        price=1.1100,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
    )
    breaking_bar = _create_bar(10, 1.1050, 1.1200, 1.1050, 1.1150)

    bos = StructureBreak(
        break_id="bos_1",
        break_type=BreakType.BOS,
        broken_swing=broken_swing,
        breaking_bar=breaking_bar,
        timestamp=breaking_bar.timestamp,
    )

    assert bos.break_type == BreakType.BOS
    assert bos.broken_swing.price == 1.1100
    assert bos.breaking_bar.close == 1.1150

    choch = StructureBreak(
        break_id="choch_1",
        break_type=BreakType.CHoCH,
        broken_swing=broken_swing,
        breaking_bar=breaking_bar,
        timestamp=breaking_bar.timestamp,
    )
    assert choch.break_type == BreakType.CHoCH


def test_market_state_missing_detector_data() -> None:
    """Verifies MarketState works correctly even if some detector data is missing/empty."""
    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)

    # Default factories must initialize empty lists
    assert state.smc_state.fair_value_gaps == []
    assert state.smc_state.order_blocks == []
    assert state.smc_state.liquidity_levels == []
    assert state.smc_state.displacements == []

    # Strategy evaluation shouldn't crash on this, should just return None
    strat = BullishContinuationStrategy()
    setup = strat.evaluate(state)
    assert setup is None


# --- STRATEGYEVALUATOR AUDIT TESTS ---


def _create_base_market_state(
    symbol: str = "EURUSD", timeframe: Timeframe = Timeframe.M15
) -> MarketState:
    state = MarketState(symbol=symbol, timeframe=timeframe)
    state.swing_graph = SwingGraph()
    return state


def _create_valid_bullish_state() -> MarketState:
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

    state.premium_discount_zone = PremiumDiscountZone(
        high=1.1200,
        low=1.0800,
        equilibrium=1.1000,
        current_price=1.0900,
        zone=ZoneType.DISCOUNT,
    )

    return state


def _create_valid_bearish_state() -> MarketState:
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

    state.premium_discount_zone = PremiumDiscountZone(
        high=1.1200,
        low=1.0800,
        equilibrium=1.1000,
        current_price=1.1100,
        zone=ZoneType.PREMIUM,
    )

    return state


def test_strategy_valid_buy_setup() -> None:
    """Verifies that a valid BUY setup is generated under bullish conditions."""
    market_state = _create_valid_bullish_state()
    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(market_state)

    assert setup is not None
    assert setup.direction == SignalDirection.BUY
    assert setup.symbol == "EURUSD"


def test_strategy_valid_sell_setup() -> None:
    """Verifies that a valid SELL setup is generated under bearish conditions."""
    market_state = _create_valid_bearish_state()
    strategy = BearishContinuationStrategy()
    setup = strategy.evaluate(market_state)

    assert setup is not None
    assert setup.direction == SignalDirection.SELL
    assert setup.symbol == "EURUSD"


def test_strategy_invalid_setup_rejection() -> None:
    """Verifies that setups are rejected if any continuation rule is violated."""
    # Case: FVG mitigated
    market_state = _create_valid_bullish_state()
    market_state.smc_state.fair_value_gaps[0] = FairValueGap(
        id="fvg_12_bullish",
        start_index=11,
        end_index=13,
        upper_price=1.0980,
        lower_price=1.0960,
        direction=FVGDirection.BULLISH,
        timestamp=market_state.bars[12].timestamp,
        is_mitigated=True,
    )
    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(market_state)
    assert setup is None


def test_strategy_missing_market_state_fields() -> None:
    """Verifies that strategy evaluation handles empty SMC state fields gracefully."""
    market_state = _create_valid_bullish_state()
    market_state.smc_state.order_blocks.clear()

    strategy = BullishContinuationStrategy()
    setup = strategy.evaluate(market_state)
    assert setup is None


def test_strategy_neutral_market_condition() -> None:
    """Verifies that neutral trends (RANGE/UNKNOWN) reject setup generation."""
    market_state = _create_valid_bullish_state()

    strategy = BullishContinuationStrategy()

    market_state.structure_state.trend = StructureTrend.RANGE
    assert strategy.evaluate(market_state) is None

    market_state.structure_state.trend = StructureTrend.UNKNOWN
    assert strategy.evaluate(market_state) is None


def test_strategy_duplicate_signals_prevention() -> None:
    """Verifies that duplicate signals are prevented for the same bar close."""
    engine = StrategyEngine()
    engine.register_strategy(BullishContinuationStrategy())

    market_state = _create_valid_bullish_state()

    setups = engine.run(market_state)
    assert len(setups) == 1

    unique_keys = {s.setup_id for s in setups}
    assert len(unique_keys) == len(setups)
