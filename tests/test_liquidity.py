"""Unit tests for the Liquidity Detector."""

from datetime import datetime, timedelta

import pytest

from market_structure.structure_models import SwingGraph
from market_structure.swing_models import Swing, SwingClassification, SwingStrength, SwingType
from smc.liquidity import LiquidityDetector, LiquidityType


def _create_swing(idx: int, price: float, s_type: SwingType) -> Swing:
    """Helper to generate standard Swing point."""
    return Swing(
        id=f"swing_{idx}_{s_type.value.lower()}",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * idx),
        index=idx,
        price=price,
        type=s_type,
        classification=SwingClassification.MAJOR,
        strength=1.0,
        strength_category=SwingStrength.NORMAL,
    )


def test_empty_liquidity() -> None:
    """Verifies that empty SwingGraph returns no liquidity pools."""
    detector = LiquidityDetector()
    graph = SwingGraph()
    assert detector.find_liquidity_pools(graph) == []


def test_equal_highs_lows_pooling() -> None:
    """Verifies grouping of close swing high/low points and sweep status detection."""
    graph = SwingGraph()
    # Highs: index 2 (1.1000), index 5 (1.1001) -> Equal Highs
    # Lows: index 3 (1.0900), index 6 (1.0901) -> Equal Lows
    s1 = _create_swing(2, 1.1000, SwingType.HIGH)
    s2 = _create_swing(3, 1.0900, SwingType.LOW)
    s3 = _create_swing(5, 1.1001, SwingType.HIGH)
    s4 = _create_swing(6, 1.0901, SwingType.LOW)

    graph.add_swing(s1)
    graph.add_swing(s2)
    graph.add_swing(s3)
    graph.add_swing(s4)

    # 1. Test basic detection without sweeps
    detector = LiquidityDetector(tolerance=0.0002)
    pools = detector.find_liquidity_pools(graph)

    # We expect 2 pools (one BUY_SIDE, one SELL_SIDE)
    assert len(pools) == 2
    buy_side = next(p for p in pools if p.type == LiquidityType.BUY_SIDE)
    sell_side = next(p for p in pools if p.type == LiquidityType.SELL_SIDE)

    # Verify BUY_SIDE pool (average price of 1.1000 and 1.1001 is 1.10005)
    assert buy_side.price == pytest.approx(1.10005)
    assert set(buy_side.source_swing_ids) == {s1.id, s3.id}
    assert buy_side.is_swept is False

    # Verify SELL_SIDE pool (average price of 1.0900 and 1.0901 is 1.09005)
    assert sell_side.price == pytest.approx(1.09005)
    assert set(sell_side.source_swing_ids) == {s2.id, s4.id}
    assert sell_side.is_swept is False

    # 2. Test sweep detection by adding a subsequent high that breaches the pool level (1.10005)
    s5 = _create_swing(10, 1.1020, SwingType.HIGH)  # 1.1020 > 1.10005
    graph.add_swing(s5)

    pools_swept = detector.find_liquidity_pools(graph)
    buy_side_swept = next(p for p in pools_swept if p.type == LiquidityType.BUY_SIDE)
    sell_side_swept = next(p for p in pools_swept if p.type == LiquidityType.SELL_SIDE)

    assert buy_side_swept.is_swept is True
    assert sell_side_swept.is_swept is False


def test_configurable_tolerance() -> None:
    """Verifies that configurable tolerance excludes groups outside tolerance range."""
    graph = SwingGraph()
    # Highs: index 2 (1.1000), index 5 (1.1003) -> Difference = 0.0003
    s1 = _create_swing(2, 1.1000, SwingType.HIGH)
    s2 = _create_swing(5, 1.1003, SwingType.HIGH)
    graph.add_swing(s1)
    graph.add_swing(s2)

    # Tolerance = 0.0001 (should NOT group them)
    detector_tight = LiquidityDetector(tolerance=0.0001)
    assert len(detector_tight.find_liquidity_pools(graph)) == 0

    # Tolerance = 0.0005 (should group them)
    detector_wide = LiquidityDetector(tolerance=0.0005)
    pools = detector_wide.find_liquidity_pools(graph)
    assert len(pools) == 1
    assert pools[0].price == pytest.approx(1.10015)
