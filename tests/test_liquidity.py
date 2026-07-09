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


def test_update_incremental_skips_full_nodes_copy_when_no_new_swing() -> None:
    """Bug #15: on a bar with no new swing, update_incremental must return the
    cached pool list without touching the copying `.nodes` property at all.
    """
    graph = SwingGraph()
    detector = LiquidityDetector(tolerance=0.0002)

    graph.add_swing(_create_swing(2, 1.1000, SwingType.HIGH))
    graph.add_swing(_create_swing(3, 1.0900, SwingType.LOW))
    first_result = detector.update_incremental(graph)

    # Make `.nodes` explode if accessed -- proves the no-op path never copies.
    original_nodes_getter = type(graph).nodes.fget

    def _forbidden_nodes(self):  # noqa: ANN001
        raise AssertionError("`.nodes` (full copy) must not be accessed when no new swing was added")

    type(graph).nodes = property(_forbidden_nodes)
    try:
        second_result = detector.update_incremental(graph)
        third_result = detector.update_incremental(graph)
    finally:
        type(graph).nodes = property(original_nodes_getter)

    assert second_result is first_result
    assert third_result is first_result


def test_update_incremental_bug15_differential_matches_reference() -> None:
    """Bug #15 differential test: the reordered (node_count-first) update_incremental
    must produce identical results, bar-by-bar, to a verbatim copy of the pre-fix
    logic (which always copies swing_graph.nodes before the cache check), across
    a realistic multi-thousand-bar swing sequence including replacement events.
    """
    import math
    from datetime import datetime, timedelta

    from core.models import Bar
    from market_structure.swing_detector import SwingDetector
    from smc.liquidity import LiquidityLevel, LiquidityType

    class _ReferenceOldLiquidityDetector(LiquidityDetector):
        """Verbatim pre-Bug#15 behavior: unconditionally copies swing_graph.nodes."""

        def update_incremental(self, swing_graph):  # noqa: ANN001
            nodes = swing_graph.nodes
            if not nodes:
                self._last_processed_swing_index = -1
                self._last_processed_swing_id = None
                self._swept_keys = set()
                self._pools_cache = {}
                self._cached_pools = []
                return []

            if self._last_processed_swing_index >= len(nodes) or (
                self._last_processed_swing_index >= 0
                and self._last_processed_swing_index < len(nodes)
                and nodes[self._last_processed_swing_index].id != self._last_processed_swing_id
            ):
                self._last_processed_swing_index = -1
                self._last_processed_swing_id = None
                self._swept_keys = set()
                self._pools_cache = {}
                self._cached_pools = []

            if len(nodes) - 1 == self._last_processed_swing_index:
                return self._cached_pools

            pools = []
            for type_, key_attr in ((SwingType.HIGH, "buyside"), (SwingType.LOW, "sellside")):
                group = [s for s in nodes if s.type == type_]
                for idx, cluster in enumerate(self._cluster_swings(group)):
                    if len(cluster) < self.min_swings:
                        continue
                    avg_price = sum(s.price for s in cluster) / len(cluster)
                    latest_idx = max(s.index for s in cluster)
                    source_ids = [s.id for s in cluster]
                    key = frozenset(source_ids)
                    if key in self._swept_keys:
                        is_swept = True
                    else:
                        cached = self._pools_cache.get(key)
                        is_swept = cached["is_swept"] if cached else False
                        last_checked_idx = cached["last_checked_idx"] if cached else latest_idx
                        if not is_swept:
                            for s in reversed(nodes):
                                if s.index <= last_checked_idx:
                                    break
                                breached = s.price >= avg_price if type_ == SwingType.HIGH else s.price <= avg_price
                                if breached:
                                    is_swept = True
                                    break
                            last_checked_idx = nodes[-1].index
                        self._pools_cache[key] = {"is_swept": is_swept, "last_checked_idx": last_checked_idx}
                        if is_swept:
                            self._swept_keys.add(key)
                            self._pools_cache.pop(key, None)
                    ltype = LiquidityType.BUY_SIDE if type_ == SwingType.HIGH else LiquidityType.SELL_SIDE
                    pools.append(LiquidityLevel(f"liq_{key_attr}_{idx}", avg_price, ltype, source_ids, is_swept))

            self._last_processed_swing_index = len(nodes) - 1
            self._last_processed_swing_id = nodes[-1].id
            self._cached_pools = pools
            return pools

    length = 3000
    start = datetime(2026, 1, 1)
    bars = []
    for i in range(length):
        wave = math.sin((2.0 * math.pi * i) / 20.0) * 0.0100
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

    graph_old, graph_new = SwingGraph(), SwingGraph()
    sd_old, sd_new = SwingDetector(), SwingDetector()
    det_old = _ReferenceOldLiquidityDetector(tolerance=0.0002)
    det_new = LiquidityDetector(tolerance=0.0002)

    for i in range(length):
        res_old = sd_old.detect_incremental(bars[: i + 1], graph_old)
        if res_old.new_swing and not res_old.is_replacement:
            graph_old.add_swing(res_old.new_swing)
        res_new = sd_new.detect_incremental(bars[: i + 1], graph_new)
        if res_new.new_swing and not res_new.is_replacement:
            graph_new.add_swing(res_new.new_swing)

        pools_old = det_old.update_incremental(graph_old)
        pools_new = det_new.update_incremental(graph_new)

        assert len(pools_old) == len(pools_new), f"Pool count mismatch at bar {i}"
        old_sorted = sorted(pools_old, key=lambda p: (p.type.value, p.price))
        new_sorted = sorted(pools_new, key=lambda p: (p.type.value, p.price))
        for po, pn in zip(old_sorted, new_sorted):
            assert po.price == pytest.approx(pn.price)
            assert po.type == pn.type
            assert po.is_swept == pn.is_swept
            assert set(po.source_swing_ids) == set(pn.source_swing_ids)


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
