"""Unit tests for the SMC analysis pipeline zone pruning."""

from datetime import datetime, timedelta
import pytest

from core.models import Bar, Timeframe
from market_structure.structure_models import MarketState, SMCState
from smc.pipeline import SMCPipeline
from smc.order_block import OrderBlock, OBDirection
from smc.fvg import FairValueGap, FVGDirection


def _create_bar(idx: int, close: float) -> Bar:
    """Helper to generate a Bar."""
    return Bar(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * idx),
        open=close,
        high=close + 0.0005,
        low=close - 0.0005,
        close=close,
        volume=100.0,
    )


def test_zone_pruning_removes_old_mitigated_zones() -> None:
    """Verifies that mitigated zones older than max_zone_age_bars are pruned, while active ones remain."""
    # Initialize pipeline with max_zone_age_bars = 10
    pipeline = SMCPipeline(max_zone_age_bars=10)

    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.smc_state = SMCState()

    ob_mitigated = OrderBlock(
        id="ob_5_bullish_mit",
        bar_index=5,
        high=1.1010,
        low=1.1000,
        direction=OBDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=True,
    )
    ob_active = OrderBlock(
        id="ob_5_bullish_act",
        bar_index=5,
        high=1.1010,
        low=1.1000,
        direction=OBDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=False,
    )
    fvg_mitigated = FairValueGap(
        id="fvg_6_bullish_mit",
        start_index=4,
        end_index=6,
        upper_price=1.1020,
        lower_price=1.1010,
        direction=FVGDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=True,
    )
    fvg_active = FairValueGap(
        id="fvg_6_bullish_act",
        start_index=4,
        end_index=6,
        upper_price=1.1020,
        lower_price=1.1010,
        direction=FVGDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=False,
    )

    state.smc_state.order_blocks = [ob_mitigated, ob_active]
    state.smc_state.fair_value_gaps = [fvg_mitigated, fvg_active]

    # Pre-populate bars up to index 15 (current_idx = 15)
    # Age of OB = 15 - 5 = 10 bars (equal to limit, so NOT pruned yet)
    # Age of FVG = 15 - 6 = 9 bars (NOT pruned yet)
    for idx in range(16):
        state.append_bar(_create_bar(idx, 1.2000))  # high price to avoid mitigation of ob_active

    new_bar = _create_bar(16, 1.2000)
    state.append_bar(new_bar)
    pipeline.update(state, new_bar, None)

    # At current_idx = 16:
    # Age of OB = 16 - 5 = 11 bars (> 10) -> ob_mitigated should be pruned!
    # Age of FVG = 16 - 6 = 10 bars (<= 10) -> fvg_mitigated should NOT be pruned yet!
    assert len(state.smc_state.order_blocks) == 1
    assert state.smc_state.order_blocks[0].id == "ob_5_bullish_act"

    assert len(state.smc_state.fair_value_gaps) == 2
    assert "fvg_6_bullish_mit" in [x.id for x in state.smc_state.fair_value_gaps]
    assert "fvg_6_bullish_act" in [x.id for x in state.smc_state.fair_value_gaps]

    # Advance to index 17 (current_idx = 17)
    # Age of FVG = 17 - 6 = 11 bars (> 10) -> fvg_mitigated should now be pruned!
    new_bar_2 = _create_bar(17, 1.2000)
    state.append_bar(new_bar_2)
    pipeline.update(state, new_bar_2, None)

    assert len(state.smc_state.fair_value_gaps) == 1
    assert state.smc_state.fair_value_gaps[0].id == "fvg_6_bullish_act"


def test_zone_pruning_disabled_by_default() -> None:
    """Verifies that when max_zone_age_bars is None (default), mitigated zones are never pruned."""
    pipeline = SMCPipeline()  # max_zone_age_bars = None by default

    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.smc_state = SMCState()

    ob_mitigated = OrderBlock(
        id="ob_5_bullish_mit",
        bar_index=5,
        high=1.1010,
        low=1.1000,
        direction=OBDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=True,
    )
    fvg_mitigated = FairValueGap(
        id="fvg_6_bullish_mit",
        start_index=4,
        end_index=6,
        upper_price=1.1020,
        lower_price=1.1010,
        direction=FVGDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=True,
    )

    state.smc_state.order_blocks = [ob_mitigated]
    state.smc_state.fair_value_gaps = [fvg_mitigated]

    # Run for 30 bars (far exceeding any reasonable age limit)
    for idx in range(30):
        new_bar = _create_bar(idx, 1.2000)
        state.append_bar(new_bar)
        pipeline.update(state, new_bar, None)

    # Mitigated zones should still be present
    assert len(state.smc_state.order_blocks) == 1
    assert state.smc_state.order_blocks[0].id == "ob_5_bullish_mit"
    assert len(state.smc_state.fair_value_gaps) == 1
    assert state.smc_state.fair_value_gaps[0].id == "fvg_6_bullish_mit"


def test_active_zones_never_pruned() -> None:
    """Verifies that unmitigated (active) zones are never pruned, regardless of age."""
    pipeline = SMCPipeline(max_zone_age_bars=1)

    state = MarketState(symbol="EURUSD", timeframe=Timeframe.M15)
    state.smc_state = SMCState()

    ob_active = OrderBlock(
        id="ob_5_bullish_act",
        bar_index=5,
        high=1.1010,
        low=1.1000,
        direction=OBDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=False,
    )
    fvg_active = FairValueGap(
        id="fvg_6_bullish_act",
        start_index=4,
        end_index=6,
        upper_price=1.1020,
        lower_price=1.1010,
        direction=FVGDirection.BULLISH,
        timestamp=datetime(2026, 1, 1),
        is_mitigated=False,
    )

    state.smc_state.order_blocks = [ob_active]
    state.smc_state.fair_value_gaps = [fvg_active]

    # Pre-populate bars to index 30 (age is 25, limit is 1)
    # Keep price at 1.2000 (well above zone highs) so they don't get mitigated
    for idx in range(31):
        state.append_bar(_create_bar(idx, 1.2000))

    new_bar = _create_bar(31, 1.2000)
    state.append_bar(new_bar)
    pipeline.update(state, new_bar, None)

    # Active zones must remain untouched
    assert len(state.smc_state.order_blocks) == 1
    assert state.smc_state.order_blocks[0].id == "ob_5_bullish_act"
    assert len(state.smc_state.fair_value_gaps) == 1
    assert state.smc_state.fair_value_gaps[0].id == "fvg_6_bullish_act"
