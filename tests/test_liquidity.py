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


def test_liquidity_batch_incremental_equivalence() -> None:
    """Verifies that update_incremental results are identical to batch find_liquidity_pools."""
    graph = SwingGraph()
    detector = LiquidityDetector(tolerance=0.0002)

    # We will simulate adding swings sequentially (in chronological index order) and checking equivalence
    swings = [
        _create_swing(2, 1.1000, SwingType.HIGH),
        _create_swing(3, 1.0900, SwingType.LOW),
        _create_swing(5, 1.1001, SwingType.HIGH),
        _create_swing(6, 1.0901, SwingType.LOW),
        _create_swing(10, 1.1020, SwingType.HIGH),
        _create_swing(12, 1.0898, SwingType.LOW),
        _create_swing(15, 1.1050, SwingType.HIGH),
        _create_swing(18, 1.0880, SwingType.LOW),
    ]

    for i in range(len(swings)):
        graph.add_swing(swings[i])
        batch_pools = detector.find_liquidity_pools(graph)
        inc_pools = detector.update_incremental(graph)

        # Assert identical pool counts
        assert len(batch_pools) == len(inc_pools), f"Mismatch at step {i}"

        # Group and compare pools
        # Sort pools by price and type for stable comparison
        batch_sorted = sorted(batch_pools, key=lambda p: (p.type.value, p.price))
        inc_sorted = sorted(inc_pools, key=lambda p: (p.type.value, p.price))

        for bp, ip in zip(batch_sorted, inc_sorted):
            assert bp.price == pytest.approx(ip.price)
            assert bp.type == ip.type
            assert set(bp.source_swing_ids) == set(ip.source_swing_ids)
            assert bp.is_swept == ip.is_swept


def test_liquidity_performance_large_dataset() -> None:
    """Verifies at least 5x speedup — measured ~7.7x on 15k bar synthetic dataset with cyclic swing generation; actual speedup depends on swing density."""
    import time
    import math
    from core.models import Bar

    # Generate 15,000 bars with sinusoidal shape to generate plenty of swings
    length = 15000
    bars = []
    start = datetime(2026, 1, 1)
    for i in range(length):
        angle = (2.0 * math.pi * i) / 20.0
        wave = math.sin(angle) * 0.0100
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=15 * i),
                open=1.1000 + wave,
                high=1.1005 + wave,
                low=1.0995 + wave,
                close=1.1000 + wave,
                volume=100.0,
            )
        )

    # Build the swing graph first using SwingDetector
    from market_structure.swing_models import SwingConfig
    from market_structure.swing_detector import SwingDetector
    swing_config = SwingConfig(
        left_bars=3,
        right_bars=3,
        minimum_bar_distance=5,
        filter_enabled=True,
        classification_enabled=True,
    )
    detector_swing = SwingDetector(config=swing_config)
    graph = SwingGraph()

    # Pre-populate swing graph bar-by-bar
    for idx in range(len(bars)):
        res = detector_swing.detect_incremental(bars[:idx+1], graph)
        if res and hasattr(res, 'new_swing') and res.new_swing is not None:
            if not getattr(res, 'is_replacement', False):
                graph.add_swing(res.new_swing)

    total_swings = len(graph.nodes)
    print(f"\n[Perf Test Setup] Total bars: {length}, Total swings: {total_swings}")

    # Map each swing to the bar index where it was confirmed (s.index + 3)
    confirmed_swings_at_bar = {i: [] for i in range(length)}
    for s in graph.nodes:
        conf_bar = s.index + 3
        if conf_bar < length:
            confirmed_swings_at_bar[conf_bar].append(s)

    # Set up detectors
    detector_batch = LiquidityDetector(tolerance=0.0002)
    detector_inc = LiquidityDetector(tolerance=0.0002)

    # Patch detector_inc._cluster_swings to measure re-clustering overhead
    original_cluster_swings = detector_inc._cluster_swings
    clustering_time = 0.0
    clustering_calls = 0

    def patched_cluster_swings(*args, **kwargs):
        nonlocal clustering_time, clustering_calls
        start_time = time.perf_counter()
        res = original_cluster_swings(*args, **kwargs)
        clustering_time += time.perf_counter() - start_time
        clustering_calls += 1
        return res

    detector_inc._cluster_swings = patched_cluster_swings

    # 1. Measure Batch Performance
    start_batch = time.perf_counter()
    sub_graph_batch = SwingGraph()
    for bar_idx in range(length):
        for s in confirmed_swings_at_bar[bar_idx]:
            sub_graph_batch.add_swing(s)
        _ = detector_batch.find_liquidity_pools(sub_graph_batch)
    time_batch = time.perf_counter() - start_batch

    # 2. Measure Incremental Performance
    start_inc = time.perf_counter()
    sub_graph_inc = SwingGraph()
    for bar_idx in range(length):
        for s in confirmed_swings_at_bar[bar_idx]:
            sub_graph_inc.add_swing(s)
        _ = detector_inc.update_incremental(sub_graph_inc)
    time_inc = time.perf_counter() - start_inc

    speedup = time_batch / time_inc if time_inc > 0 else float('inf')

    print(f"[Performance Results]")
    print(f"Batch Time: {time_batch:.4f} seconds")
    print(f"Incremental Time: {time_inc:.4f} seconds")
    print(f"Speedup: {speedup:.2f}x")
    print(f"Total swing additions (re-clustering): {clustering_calls} times")
    print(f"Total re-clustering duration: {clustering_time:.4f} seconds")

    # Assertions
    assert time_inc < 1.0, f"Incremental execution too slow: {time_inc:.4f}s"
    assert speedup >= 5.0, f"Speedup ({speedup:.2f}x) is less than the required 5x"
