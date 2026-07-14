"""Unit and integration tests for MarketStateBuilder and SMCPipeline."""

from datetime import datetime, timedelta

import pytest

from application.services.market_state_builder import MarketStateBuilder
from core.models import Bar, Timeframe
from market_structure.structure_engine import MarketStructureEngine
from market_structure.structure_models import (
    BreakType,
    StructureConfig,
    StructureTrend,
)
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import SwingClassification, SwingConfig, SwingType
from smc.displacement import DisplacementDetector
from smc.order_block import OrderBlockDetector
from smc.pipeline import SMCPipeline


def _create_bar(
    index: int, open_p: float, high_p: float, low_p: float, close_p: float, volume: float = 100.0
) -> Bar:
    """Helper to generate a Bar object."""
    start = datetime(2026, 1, 1)
    return Bar(
        timestamp=start + timedelta(minutes=15 * index),
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
    )


def test_market_state_builder_lifecycle() -> None:
    """Verifies standard initialization, append_bar, and state preservation."""
    builder = MarketStateBuilder(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        swing_detector=SwingDetector(SwingConfig(left_bars=2, right_bars=2)),
        structure_engine=MarketStructureEngine(StructureConfig(minimum_confirmations=1)),
    )

    # Initially empty
    assert builder.market_state.symbol == "EURUSD"
    assert builder.market_state.timeframe == Timeframe.M15
    assert len(builder.market_state.bars) == 0

    # 1. Initialize from history (first 5 bars)
    # This forms a confirmed swing high at index 2 (High: 1.1200) when bar 4 closes.
    history = [
        _create_bar(0, 1.1000, 1.1000, 1.0900, 1.0950),
        _create_bar(1, 1.0950, 1.1010, 1.0910, 1.0980),
        _create_bar(2, 1.0980, 1.1200, 1.0920, 1.1150),  # Potential High
        _create_bar(3, 1.1150, 1.1010, 1.0910, 1.0960),
        _create_bar(4, 1.0960, 1.1000, 1.0900, 1.0950),  # Confirms High at index 2
    ]

    initial_instance = builder.market_state
    builder.initialize(history)

    # Same MarketState instance preserved
    assert builder.market_state is initial_instance
    assert len(builder.market_state.bars) == 5
    assert len(builder.market_state.swing_graph.nodes) == 1

    # Swing is propagated to engine
    swing = builder.market_state.swing_graph.nodes[0]
    assert swing.index == 2
    assert swing.price == 1.1200
    assert swing.type == SwingType.HIGH

    # 2. Append additional bars to form a Swing Low at index 6
    b5 = _create_bar(5, 1.0950, 1.1010, 1.0850, 1.0920)
    b6 = _create_bar(6, 1.0920, 1.1000, 1.0800, 1.0850)
    b7 = _create_bar(7, 1.0850, 1.1010, 1.0850, 1.0950)
    b8 = _create_bar(8, 1.0950, 1.1020, 1.0900, 1.0980)

    builder.append_bar(b5)
    builder.append_bar(b6)
    builder.append_bar(b7)
    state = builder.append_bar(b8)

    assert len(state.bars) == 9
    assert len(state.swing_graph.nodes) == 2
    assert state.swing_graph.nodes[-1].index == 6
    assert state.swing_graph.nodes[-1].price == 1.0800
    assert state.swing_graph.nodes[-1].type == SwingType.LOW


def test_market_state_builder_bos_and_choch() -> None:
    """Verifies BOS and CHoCH propagation through the builder."""
    # Config setup
    swing_config = SwingConfig(left_bars=1, right_bars=1, classification_enabled=True)
    struct_config = StructureConfig(minimum_confirmations=1, equal_high_tolerance=0.005)

    # Bypass volume filter in tests using volume_multiplier=0
    ob_detector = OrderBlockDetector(volume_multiplier=0.0)
    smc_pipeline = SMCPipeline(ob_detector=ob_detector)

    builder = MarketStateBuilder(
        symbol="GBPUSD",
        timeframe=Timeframe.M15,
        swing_detector=SwingDetector(swing_config),
        structure_engine=MarketStructureEngine(struct_config),
        smc_pipeline=smc_pipeline,
    )

    # 1. Establish Bullish Trend via sequential Higher High (index 10) and Higher Low (index 14)
    bars = [
        # High at 2
        _create_bar(0, 1.1000, 1.1000, 1.0900, 1.0950),
        _create_bar(1, 1.0950, 1.1010, 1.0910, 1.0980),
        _create_bar(2, 1.0980, 1.1200, 1.0920, 1.1150),
        _create_bar(3, 1.1150, 1.1010, 1.0910, 1.0960),
        _create_bar(4, 1.0960, 1.1000, 1.0900, 1.0950),
        # Low at 6
        _create_bar(5, 1.0950, 1.1010, 1.0850, 1.0920),
        _create_bar(6, 1.0920, 1.1000, 1.0800, 1.0850),
        _create_bar(7, 1.0850, 1.1010, 1.0850, 1.0950),
        _create_bar(8, 1.0950, 1.1020, 1.0900, 1.0980),
        # High at 10 (HH)
        _create_bar(9, 1.0980, 1.1030, 1.0910, 1.1000),
        _create_bar(10, 1.1000, 1.1300, 1.0920, 1.1250),
        _create_bar(11, 1.1250, 1.1030, 1.0910, 1.0960),
        _create_bar(12, 1.0960, 1.1020, 1.0900, 1.0950),
        # Low at 14 (HL)
        _create_bar(13, 1.0950, 1.1025, 1.0880, 1.0920),
        _create_bar(14, 1.0920, 1.1020, 1.0840, 1.0880),
        _create_bar(15, 1.0880, 1.1025, 1.0880, 1.0950),
        _create_bar(16, 1.0950, 1.1030, 1.0900, 1.0980),
    ]

    builder.initialize(bars)
    assert builder.market_state.structure_state.trend == StructureTrend.BULLISH

    # 2. Trigger Bullish BOS break: a bar closing above HH level (1.1300)
    b17 = _create_bar(17, 1.1200, 1.1500, 1.1100, 1.1400)
    state = builder.append_bar(b17)

    # Verify BOS break detected
    breaks = state.structure_state.breaks_history
    assert len(breaks) == 2
    assert breaks[1].break_type == BreakType.BOS
    assert breaks[1].broken_swing.index == 10
    assert breaks[1].broken_swing.price == 1.1300

    # Verify Order Block detected for the break
    assert len(state.smc_state.order_blocks) >= 1
    ob = state.smc_state.order_blocks[-1]
    assert ob.direction.value == "BULLISH"
    assert ob.high > ob.low

    # 3. Trigger Bearish CHoCH break: a bar closing below HL level (1.0840)
    b18 = _create_bar(18, 1.1000, 1.1100, 1.0700, 1.0750)
    state_choch = builder.append_bar(b18)

    # Verify CHoCH break detected (trend reversal bullish -> bearish)
    breaks_choch = state_choch.structure_state.breaks_history
    assert len(breaks_choch) == 3
    assert breaks_choch[2].break_type == BreakType.CHoCH
    assert breaks_choch[2].broken_swing.index == 14
    assert breaks_choch[2].broken_swing.price == 1.0840


def test_market_state_builder_swing_upgrades() -> None:
    """Verifies that MINOR -> MAJOR classification upgrades propagate correctly."""
    swing_config = SwingConfig(left_bars=2, right_bars=2, classification_enabled=True)
    builder = MarketStateBuilder(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        swing_detector=SwingDetector(swing_config),
        structure_engine=MarketStructureEngine(StructureConfig(minimum_confirmations=1)),
    )

    bars = [
        _create_bar(0, 1.1000, 1.1000, 1.0900, 1.0950),
        _create_bar(1, 1.0950, 1.1000, 1.0900, 1.0950),
        _create_bar(2, 1.0950, 1.1000, 1.0900, 1.0950),
        _create_bar(3, 1.0950, 1.1000, 1.0900, 1.0950),
        _create_bar(4, 1.0950, 1.1200, 1.0900, 1.1150),  # Potential High
        _create_bar(5, 1.1150, 1.1000, 1.0900, 1.0950),
        _create_bar(6, 1.0950, 1.1000, 1.0900, 1.0950),  # Confirms MINOR swing high at 4 (right=2)
    ]

    builder.initialize(bars)
    swing = builder.market_state.swing_graph.nodes[0]
    assert swing.index == 4
    assert swing.classification == SwingClassification.MINOR

    # Append bar 7 (still not enough for right_major = 4)
    builder.append_bar(_create_bar(7, 1.0950, 1.1000, 1.0900, 1.0950))
    swing = builder.market_state.swing_graph.nodes[0]
    assert swing.classification == SwingClassification.MINOR

    # Append bar 8 (index 8, completes the 4 right major confirmation candles)
    builder.append_bar(_create_bar(8, 1.0950, 1.1000, 1.0900, 1.0950))

    # Should be upgraded to MAJOR
    swing = builder.market_state.swing_graph.nodes[0]
    assert swing.classification == SwingClassification.MAJOR
    # And structure engine's pointer should be updated
    assert builder.structure_engine.last_major_high == swing


def test_market_state_builder_fvg_displacement_mitigation() -> None:
    """Verifies FVG and Displacement detection, and FVG mitigation updates."""
    # We construct the pipeline to run FVG, Mitigation and Displacement (with custom short period 2)
    displacement_detector = DisplacementDetector(atr_multiplier=1.5, atr_period=2)
    smc_pipeline = SMCPipeline(displacement_detector=displacement_detector)

    builder = MarketStateBuilder(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        swing_detector=SwingDetector(SwingConfig(left_bars=2, right_bars=2)),
        structure_engine=MarketStructureEngine(StructureConfig(minimum_confirmations=1)),
        smc_pipeline=smc_pipeline,
    )

    # Let's generate a sequence of 6 bars:
    # 0: High = 1.1010
    # 1: Open=1.1010, High=1.1080, Low=1.1015, Close=1.1070 (large body, FVG imbalance)
    # 2: Low = 1.1030
    # Gap: 1.1030 (low of 2) - 1.1010 (high of 0) = 20 pips
    # 3: small bar
    # 4: large expansion bar to trigger displacement (with atr_period=2)
    bars = [
        _create_bar(0, 1.1000, 1.1010, 1.0990, 1.1000),
        _create_bar(1, 1.1010, 1.1080, 1.1015, 1.1070),
        _create_bar(2, 1.1070, 1.1080, 1.1030, 1.1040),
        _create_bar(3, 1.1040, 1.1050, 1.1040, 1.1045),
        # Displacement candidate at index 4 (ATR period=2)
        _create_bar(4, 1.1045, 1.1200, 1.1040, 1.1190),
    ]

    builder.initialize(bars)
    state = builder.market_state

    # 1. Verify FVG detected
    assert len(state.smc_state.fair_value_gaps) == 1
    fvg = state.smc_state.fair_value_gaps[0]
    assert fvg.start_index == 0
    assert fvg.end_index == 2
    assert fvg.lower_price == 1.1010
    assert fvg.upper_price == 1.1030
    assert fvg.is_mitigated is False

    # 2. Verify Displacement detected
    assert len(state.smc_state.displacements) >= 1
    disps_indices = [d.bar_index for d in state.smc_state.displacements]
    assert 4 in disps_indices

    # 3. Bug #22: a bar that merely enters the gap range [1.1010, 1.1030] is a
    # retest (the intended entry trigger), not invalidation -- it must NOT
    # mitigate the FVG. bar 5: low = 1.1020 (wicks into the gap) but closes
    # at 1.1050, well above lower_price, so it hasn't closed through the far
    # edge. This assertion used to expect is_mitigated=True (pre-fix, "any
    # touch mitigates"); that was the exact bug the strategy diagnostics
    # investigation traced the 0-trade outcome to (see walkthrough.md Bug #22).
    b5 = _create_bar(5, 1.1190, 1.1200, 1.1020, 1.1050)
    state_retested = builder.append_bar(b5)

    assert len(state_retested.smc_state.fair_value_gaps) == 1
    assert state_retested.smc_state.fair_value_gaps[0].is_mitigated is False

    # 4. Mitigation only fires once a bar CLOSES fully through the far edge
    # (below lower_price=1.1010 for a bullish FVG).
    b6 = _create_bar(6, 1.1050, 1.1050, 1.0990, 1.1000)
    state_mitigated = builder.append_bar(b6)

    assert len(state_mitigated.smc_state.fair_value_gaps) == 1
    assert state_mitigated.smc_state.fair_value_gaps[0].is_mitigated is True


@pytest.mark.xfail(
    reason=(
        "Bug #29: an unrelated swing replacement (is_replacement=True) wipes "
        "MarketStructureEngine.breaks_history via reset() and never rebuilds "
        "it (market_state_builder.py:64-72 replays update() only, never "
        "check_structural_break()). A previously-recorded break silently "
        "disappears, and last_broken_high_id/low_id being reset can cause "
        "the SAME swing to be re-detected as a 'new' break on the very next "
        "bar if price still sits beyond it -- a duplicate/phantom break with "
        "a later timestamp than the original. See walkthrough.md Bug #29."
    ),
    strict=True,
)
def test_bug29_swing_replacement_corrupts_breaks_history() -> None:
    """Demonstrates that an unrelated swing replacement erases prior break history.

    Bar-by-bar plan (left_bars=2, right_bars=2, minimum_bar_distance=6):
    - idx 6: MAJOR low swing L0 forms (price=80), upgraded to MAJOR at bar 10.
      Exists only so last_major_low is set (check_structural_break requires
      both last_major_high AND last_major_low to be non-None).
    - idx 20: MAJOR high swing H1 forms (price=110), upgraded to MAJOR at bar 24.
    - idx 25-28: a flat plateau at close=111 (> H1). The FIRST plateau bar (25)
      triggers a legitimate BOS break of H1 -- recorded in breaks_history.
      (Flat/equal-height plateau, not a single spike, so no bar in it becomes
      a new swing-high candidate that would replace H1 itself -- keeps this
      scenario isolated to the *unrelated* swing replacement below.)
    - idx 32: a new MINOR low swing L1 forms (price=85), appended normally
      (becomes the swing graph's last node).
    - idx 40: a MORE EXTREME low L2 forms (price=70) at the same graph slot
      as L1 -> is_replacement=True. L1 is unrelated to H1 or its break.
    - idx 42: the bar that confirms/replaces L1->L2 also closes at 111 (still
      beyond H1). This is where the corruption manifests.

    Expected (correct) behavior: breaks_history should still contain the
    original bar-25 BOS break of H1 after the bar-42 replacement (plus
    possibly a second, distinctly-identifiable entry -- but the original
    must not vanish).

    Actual (buggy) behavior: breaks_history contains exactly ONE break, a
    duplicate of the SAME swing (H1) timestamped at bar 42 -- the original
    bar-25 break is gone.
    """
    swing_config = SwingConfig(
        left_bars=2, right_bars=2, minimum_bar_distance=6, classification_enabled=True
    )
    struct_config = StructureConfig(minimum_confirmations=1, major_only=False)

    builder = MarketStateBuilder(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        swing_detector=SwingDetector(swing_config),
        structure_engine=MarketStructureEngine(struct_config),
    )

    def flat(i: int) -> Bar:
        return _create_bar(i, 100.0, 100.5, 99.0, 100.0)

    bars = [flat(i) for i in range(43)]
    bars[6] = _create_bar(6, 100.0, 100.5, 80.0, 100.0)  # L0 (major low) pivot
    bars[20] = _create_bar(20, 100.0, 110.0, 99.0, 100.0)  # H1 (major high) pivot
    for j in range(25, 29):
        bars[j] = _create_bar(j, 100.0, 111.0, 99.0, 111.0)  # flat break plateau
    bars[32] = _create_bar(32, 100.0, 100.5, 85.0, 100.0)  # L1 (minor low) pivot
    bars[40] = _create_bar(40, 100.0, 100.5, 70.0, 100.0)  # L2 (replaces L1)
    bars[42] = _create_bar(42, 100.0, 112.0, 99.0, 111.0)  # replacement-confirming bar

    # Phase 1: through the original H1 break (bar 25). This part is expected
    # to pass even today -- it establishes the "before" state.
    builder.initialize(bars[:26])
    breaks_before = builder.market_state.structure_state.breaks_history
    assert len(breaks_before) == 1
    original_break = breaks_before[0]
    assert original_break.break_type == BreakType.BOS
    assert original_break.broken_swing.index == 20

    # Phase 2: continue through the unrelated L1->L2 replacement (bar 42).
    for b in bars[26:]:
        state = builder.append_bar(b)

    breaks_after = state.structure_state.breaks_history

    # The original break (bar 25, H1) must still be present -- this is the
    # assertion that fails today: breaks_after ends up as a single *new*
    # break (bar 42) referencing the same swing, not the original one.
    assert any(
        b.break_type == BreakType.BOS
        and b.broken_swing.index == 20
        and b.timestamp == original_break.timestamp
        for b in breaks_after
    ), (
        f"Original break at {original_break.timestamp} for swing index 20 is "
        f"missing from breaks_history after the unrelated swing replacement. "
        f"breaks_history now contains: "
        f"{[(b.break_type, b.broken_swing.index, b.timestamp) for b in breaks_after]}"
    )
