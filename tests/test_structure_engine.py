"""Unit tests for the Market Structure Engine."""

import pandas as pd
import pytest

from market_structure.swing_models import Swing, SwingClassification, SwingStrength, SwingType
from market_structure.structure_models import (
    StructureConfig,
    StructureTrend,
    SwingRelationship,
)
from market_structure.structure_engine import (
    BrokenGraphError,
    DuplicateSwingIDError,
    InvalidSwingSequenceError,
    MarketStructureEngine,
)


def _make_swing(
    swing_id: str,
    index: int,
    price: float,
    swing_type: SwingType,
    classification: SwingClassification = SwingClassification.MAJOR,
) -> Swing:
    """Helper to create a synthetic Swing object."""
    return Swing(
        id=swing_id,
        timestamp=pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=15 * index),
        index=index,
        price=price,
        type=swing_type,
        classification=classification,
        strength=1.5,
        strength_category=SwingStrength.STRONG,
    )


def _link_swings(swings: list[Swing]) -> list[Swing]:
    """Helper to stitch swings together chronologically as a double linked list."""
    for i, s in enumerate(swings):
        s.previous_id = swings[i - 1].id if i > 0 else None
        s.next_id = swings[i + 1].id if i < len(swings) - 1 else None
        s.bar_distance = s.index - swings[i - 1].index if i > 0 else None
        s.price_distance = float(s.price - swings[i - 1].price) if i > 0 else None
    return swings


def test_bullish_trend_progression() -> None:
    """Verifies that sequential Higher Highs and Higher Lows trigger a BULLISH trend state."""
    swings = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW),
        _make_swing("s3_high", 12, 110.0, SwingType.HIGH),  # HH
        _make_swing("s4_low", 16, 95.0, SwingType.LOW),   # HL
    ]
    _link_swings(swings)

    engine = MarketStructureEngine()
    history = engine.analyze(swings)

    assert len(history) == 4
    # The last state should be BULLISH
    last_state = history[-1]
    assert last_state.trend == StructureTrend.BULLISH
    assert last_state.current_hh is not None
    assert last_state.current_hh.id == "s3_high"
    assert last_state.current_hl is not None
    assert last_state.current_hl.id == "s4_low"
    assert last_state.confidence > 0.0


def test_bearish_trend_progression() -> None:
    """Verifies that sequential Lower Highs and Lower Lows trigger a BEARISH trend state."""
    swings = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW),
        _make_swing("s3_high", 12, 95.0, SwingType.HIGH),  # LH
        _make_swing("s4_low", 16, 85.0, SwingType.LOW),   # LL
    ]
    _link_swings(swings)

    engine = MarketStructureEngine()
    history = engine.analyze(swings)

    last_state = history[-1]
    assert last_state.trend == StructureTrend.BEARISH
    assert last_state.current_lh is not None
    assert last_state.current_lh.id == "s3_high"
    assert last_state.current_ll is not None
    assert last_state.current_ll.id == "s4_low"


def test_range_state_compressing_expanding() -> None:
    """Verifies that compressing or equal swings trigger a RANGE state."""
    # Scenario A: Equal Highs / Lows (Sideways range)
    swings_eq = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW),
        _make_swing("s3_high", 12, 100.0, SwingType.HIGH),  # Equal High (within 0.0001 tolerance)
        _make_swing("s4_low", 16, 90.0, SwingType.LOW),   # Equal Low
    ]
    _link_swings(swings_eq)

    engine_eq = MarketStructureEngine(config=StructureConfig(equal_high_tolerance=0.01))
    history_eq = engine_eq.analyze(swings_eq)
    assert history_eq[-1].trend == StructureTrend.RANGE

    # Scenario B: Compressing range (LH + HL)
    swings_comp = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW),
        _make_swing("s3_high", 12, 95.0, SwingType.HIGH),  # LH
        _make_swing("s4_low", 16, 92.0, SwingType.LOW),   # HL
    ]
    _link_swings(swings_comp)

    engine_comp = MarketStructureEngine()
    history_comp = engine_comp.analyze(swings_comp)
    assert history_comp[-1].trend == StructureTrend.RANGE


def test_transition_states() -> None:
    """Verifies transition state when a bullish trend begins to break down before turning bearish."""
    swings = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW),
        _make_swing("s3_high", 12, 110.0, SwingType.HIGH),  # HH -> Trend = UNKNOWN (need HL)
        _make_swing("s4_low", 16, 95.0, SwingType.LOW),   # HL -> Trend = BULLISH
        _make_swing("s5_high", 20, 105.0, SwingType.HIGH),  # LH -> Trend = TRANSITION (from BULLISH)
        _make_swing("s6_low", 24, 88.0, SwingType.LOW),   # LL -> Trend = BEARISH (reversal confirmed)
    ]
    _link_swings(swings)

    engine = MarketStructureEngine()
    history = engine.analyze(swings)

    assert history[3].trend == StructureTrend.BULLISH
    assert history[4].trend == StructureTrend.TRANSITION
    assert history[5].trend == StructureTrend.BEARISH


def test_major_only_mode() -> None:
    """Verifies that minor swings are completely ignored in major_only configuration mode."""
    swings = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH, SwingClassification.MAJOR),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW, SwingClassification.MINOR),  # MINOR
        _make_swing("s3_high", 12, 110.0, SwingType.HIGH, SwingClassification.MAJOR),
    ]
    _link_swings(swings)

    engine = MarketStructureEngine(config=StructureConfig(major_only=True))
    history = engine.analyze(swings)

    # State update count should only count major entries. 
    # Index 8 (s2_low) was minor and bypassed, so it shouldn't produce a new unique structural history block
    assert len(history) == 2
    assert history[0].last_major_high.id == "s1_high"
    assert history[1].last_major_high.id == "s3_high"
    assert history[1].last_major_low is None  # Since s2_low was ignored


def test_minor_enabled_mode() -> None:
    """Verifies that minor swings update minor trackers and internal structure ranges when major_only is False."""
    swings = [
        _make_swing("s1_high", 4, 100.0, SwingType.HIGH, SwingClassification.MAJOR),
        _make_swing("s2_low", 8, 90.0, SwingType.LOW, SwingClassification.MAJOR),
        _make_swing("s3_high", 12, 95.0, SwingType.HIGH, SwingClassification.MINOR),  # MINOR
    ]
    _link_swings(swings)

    engine = MarketStructureEngine(config=StructureConfig(major_only=False))
    history = engine.analyze(swings)

    assert len(history) == 3
    assert history[-1].last_minor_high is not None
    assert history[-1].last_minor_high.id == "s3_high"
    assert history[-1].internal_structure.get("is_inside") is True


def test_invalid_swing_sequences() -> None:
    """Verifies sequence validations raise appropriate custom exceptions."""
    engine = MarketStructureEngine()

    # Case 1: Swings out of chronological index order
    swings_order = [
        _make_swing("s1", 10, 100.0, SwingType.HIGH),
        _make_swing("s2", 5, 90.0, SwingType.LOW),  # index 5 <= 10
    ]
    _link_swings(swings_order)
    with pytest.raises(InvalidSwingSequenceError, match="violations chronological order|out of chronological order"):
        engine.analyze(swings_order)

    # Case 2: Duplicate Swing IDs
    engine.reset()
    swings_dup = [
        _make_swing("dup_id", 5, 100.0, SwingType.HIGH),
        _make_swing("dup_id", 10, 90.0, SwingType.LOW),
    ]
    _link_swings(swings_dup)
    with pytest.raises(DuplicateSwingIDError, match="Duplicate swing ID detected"):
        engine.analyze(swings_dup)

    # Case 3: Broken graph relationships
    engine.reset()
    s1 = _make_swing("s1", 5, 100.0, SwingType.HIGH)
    s2 = _make_swing("s2", 10, 90.0, SwingType.LOW)
    # Don't link s1 and s2 -> broken previous/next pointers
    with pytest.raises(BrokenGraphError, match="Swing graph link broken"):
        engine.analyze([s1, s2])
