"""Unit and integration tests for incremental swing detection and invariants.

Validates Pandas-free calculations, batch vs. incremental parity, no-repaint invariants,
and performance targets.
"""

import math
import time
from datetime import datetime, timedelta

import pytest

from core.exceptions import DataValidationError
from core.models import Bar
from market_structure.structure_models import SwingGraph
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import (
    Swing,
    SwingClassification,
    SwingConfig,
)


def _generate_synthetic_bars(length: int, base_price: float = 1.1000) -> list[Bar]:
    """Generates standard candlestick bars for synthetic testing."""
    start = datetime(2026, 1, 1)
    bars = []
    for i in range(length):
        timestamp = start + timedelta(minutes=15 * i)
        bars.append(
            Bar(
                timestamp=timestamp,
                open=base_price,
                high=base_price + 0.0005,
                low=base_price - 0.0005,
                close=base_price,
                volume=100.0,
                spread=0.0,
            )
        )
    return bars


def test_incremental_cosine_wave_oscillations() -> None:
    """Verifies that mathematical peaks/troughs are detected correctly in batch and incremental."""
    length = 50
    bars = _generate_synthetic_bars(length, base_price=1.1000)

    # Inject cosine wave highs and lows
    # Cosine peaks at 0, 2pi, 4pi... Troughs at pi, 3pi...
    # Let's map high and low values using a sine/cosine cycle of period 12 bars
    for i in range(length):
        angle = (2.0 * math.pi * i) / 12.0
        wave = math.cos(angle) * 0.0100  # amplitude = 100 pips
        # High oscillates, low follows
        high_val = 1.1000 + wave
        low_val = 1.0900 + wave
        bars[i] = Bar(
            timestamp=bars[i].timestamp,
            open=1.0950 + wave,
            high=high_val,
            low=low_val,
            close=1.0950 + wave,
            volume=100.0,
            spread=0.0,
        )

    config = SwingConfig(left_bars=3, right_bars=3, filter_enabled=False)
    detector = SwingDetector(config=config)

    # 1. Batch Swings
    batch_swings = detector.detect_batch(bars)
    assert len(batch_swings) > 0

    # 2. Incremental Swings
    graph = SwingGraph()
    incremental_swings = []

    for t in range(1, length + 1):
        window = bars[:t]
        res = detector.detect_incremental(window, graph)
        for new_swing in res.new_swings:
            graph.add_swing(new_swing)
            incremental_swings.append(new_swing)

    # Compare batch vs incremental counts and properties
    assert len(batch_swings) == len(incremental_swings)
    for b_swing, i_swing in zip(batch_swings, incremental_swings, strict=True):
        assert b_swing.id == i_swing.id
        assert b_swing.index == i_swing.index
        assert b_swing.price == i_swing.price
        assert b_swing.type == i_swing.type
        assert b_swing.timestamp == i_swing.timestamp


def test_monotonic_trends_no_swings() -> None:
    """Ensures that consistently rising or falling series yield zero swings."""
    length = 20
    bars = _generate_synthetic_bars(length)

    # Create monotonic upward trend
    for i in range(length):
        bars[i] = Bar(
            timestamp=bars[i].timestamp,
            open=1.1000 + i * 0.0010,
            high=1.1005 + i * 0.0010,
            low=1.0995 + i * 0.0010,
            close=1.1000 + i * 0.0010,
            volume=100.0,
        )

    detector = SwingDetector(SwingConfig(left_bars=3, right_bars=3))
    batch_swings = detector.detect_batch(bars)
    assert len(batch_swings) == 0

    graph = SwingGraph()
    for t in range(1, length + 1):
        assert detector.detect_incremental(bars[:t], graph).new_swings == ()


def test_insufficient_history_fails() -> None:
    """Verifies that batch and incremental validation fails correctly on too short sequences."""
    bars = _generate_synthetic_bars(5)
    detector = SwingDetector(SwingConfig(left_bars=3, right_bars=3))

    with pytest.raises(DataValidationError, match="Dataset too small"):
        detector.detect_batch(bars)

    # Incremental should just return None when candidate index is out of left bounds
    graph = SwingGraph()
    assert detector.detect_incremental(bars, graph).new_swings == ()


def test_no_repaint_and_immutability_invariants() -> None:
    """Verifies the strict immutability of Swing attributes after creation."""
    length = 30
    bars = _generate_synthetic_bars(length)
    # Inject a clear swing high at index 10 -- low stays at the baseline
    # (1.0995, same as every neighboring bar) so this bar is a swing high
    # ONLY, not also a swing low (a "spike" bar), keeping this test's focus
    # on a single swing's immutability. See test_spike_bar_confirms_both_a_
    # swing_high_and_a_swing_low below for the dual-candidate case.
    bars[10] = Bar(
        timestamp=bars[10].timestamp,
        open=1.1000,
        high=1.1200,
        low=1.1000 - 0.0005,  # bit-for-bit == every neighbor's low (see _generate_synthetic_bars)
        close=1.1000,
        volume=100.0,
    )

    config = SwingConfig(left_bars=3, right_bars=3)
    detector = SwingDetector(config=config)
    graph = SwingGraph()
    confirmed_swings: list[Swing] = []

    # Stream bars
    for t in range(1, length + 1):
        res = detector.detect_incremental(bars[:t], graph)
        for new_swing in res.new_swings:
            graph.add_swing(new_swing)
            confirmed_swings.append(new_swing)

    assert len(confirmed_swings) == 1
    detected_swing = confirmed_swings[0]
    assert detected_swing.index == 10
    assert detected_swing.price == 1.1200

    # Attempting to mutate key properties must raise AttributeError
    with pytest.raises(AttributeError, match="is immutable"):
        detected_swing.price = 1.1500

    with pytest.raises(AttributeError, match="is immutable"):
        detected_swing.index = 12

    with pytest.raises(AttributeError, match="is immutable"):
        detected_swing.timestamp = datetime.now()

    # Classification change should be allowed
    detected_swing.classification = SwingClassification.MAJOR
    assert detected_swing.classification == SwingClassification.MAJOR


def test_spike_bar_confirms_both_a_swing_high_and_a_swing_low() -> None:
    """Regression test: a single "spike" bar whose high clears its
    left/right neighbors AND whose low also clears them (a real, if
    unusual, price pattern -- e.g. a stop-hunt wick immediately reversed)
    must be confirmed as BOTH a swing high and a swing low, exactly like
    detect_batch has always done (see its raw_candidates loop, which
    appends both independently). detect_incremental previously built the
    candidate via `if is_high: ... elif is_low: ...`, silently dropping the
    swing low whenever a bar was also a swing high -- a real divergence
    between what a live/incremental run would see and what a batch
    backtest over the exact same bars would find.
    """
    length = 20
    bars = _generate_synthetic_bars(length)
    bars[10] = Bar(
        timestamp=bars[10].timestamp,
        open=1.1000,
        high=1.1200,  # clears every neighbor's high (1.1005) -- swing high
        low=1.0800,  # clears every neighbor's low (1.0995) -- swing low too
        close=1.1000,
        volume=100.0,
    )

    config = SwingConfig(left_bars=3, right_bars=3)

    batch_swings = SwingDetector(config=config).detect_batch(bars)
    batch_types_at_10 = sorted(s.type.value for s in batch_swings if s.index == 10)
    assert batch_types_at_10 == ["HIGH", "LOW"]

    detector = SwingDetector(config=config)
    graph = SwingGraph()
    incremental_swings: list[Swing] = []
    for t in range(1, length + 1):
        res = detector.detect_incremental(bars[:t], graph)
        for new_swing in res.new_swings:
            graph.add_swing(new_swing)
            incremental_swings.append(new_swing)

    incremental_types_at_10 = sorted(s.type.value for s in incremental_swings if s.index == 10)
    assert incremental_types_at_10 == ["HIGH", "LOW"]
    assert len(batch_swings) == len(incremental_swings)


def test_minor_to_major_label_upgrade() -> None:
    """Tests the MINOR -> MAJOR classification check mechanism."""
    # We need enough bars to cover left_major (6) and right_major (6) centered at 10.
    # Total bars = 25
    length = 25
    bars = _generate_synthetic_bars(length)

    # Pivot at index 10
    bars[10] = Bar(
        timestamp=bars[10].timestamp,
        open=1.1000,
        high=1.1500,  # highest high
        low=1.0900,
        close=1.1000,
        volume=100.0,
    )

    config = SwingConfig(left_bars=3, right_bars=3, classification_enabled=True)
    detector = SwingDetector(config=config)
    graph = SwingGraph()

    # At T = 13 (index 13 closes), the swing at 10 is confirmed as MINOR.
    # (Because left=3 and right=3 bars are closed, i.e. indices 7..13)
    # The right_major requires 6 bars (up to index 16).
    for t in range(1, 15):
        res = detector.detect_incremental(bars[:t], graph)
        for new_swing in res.new_swings:
            graph.add_swing(new_swing)

    latest_high = graph.get_latest_high()
    assert latest_high is not None
    assert latest_high.index == 10
    # Initially confirmed as MINOR because right_major (6 bars) is not yet completed at index 13
    assert latest_high.classification == SwingClassification.MINOR

    # Now verify upgrade eligibility dynamically as more candles close
    # At T = 16 (index 16 closes), the swing at 10 becomes eligible for MAJOR
    assert not detector.check_upgrade(latest_high, bars[:15])
    assert detector.check_upgrade(latest_high, bars[:17])


def test_incremental_performance() -> None:
    """Ensures that incremental processing is extremely fast and follows O(L + R) complexity."""
    length = 10000
    bars = _generate_synthetic_bars(length)

    config = SwingConfig(left_bars=3, right_bars=3)
    detector = SwingDetector(config=config)
    graph = SwingGraph()

    # Dynamic updates profiling
    start = time.perf_counter()
    for t in range(1, length + 1):
        detector.detect_incremental(bars[:t], graph)
    elapsed = time.perf_counter() - start

    # Average execution time per bar should be well under 0.1ms (100 microseconds)
    avg_duration_ms = (elapsed / length) * 1000.0
    print(f"Incremental execution profile: {avg_duration_ms:.6f} ms/bar")
    assert avg_duration_ms < 0.1, f"Incremental performance too slow: {avg_duration_ms:.6f} ms"
