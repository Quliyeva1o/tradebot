"""Differential tests for Swing Detector and Structure Engine.

Verifies that incremental swing detection and structural calculations match batch processing
exactly, especially under swing upgrades (MINOR -> MAJOR) and duplicate swing replacements.
"""

import math
from datetime import datetime, timedelta

from core.models import Bar
from market_structure.structure_engine import MarketStructureEngine
from market_structure.structure_models import StructureConfig, SwingGraph
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import SwingConfig


def _generate_bars(length: int, base_price: float = 1.1000) -> list[Bar]:
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


def test_batch_incremental_swing_detection_equivalence() -> None:
    """Differential test verifying batch vs incremental equivalence.

    Ensures that when swing upgrades and duplicate swing replacements occur:
    1. The final incremental SwingGraph nodes are structurally identical to the batch swings.
    2. The resulting MarketStructure states are completely equivalent.
    """
    length = 250
    bars = _generate_bars(length, base_price=1.1000)

    # Inject cosine wave to generate regular swings
    # Cycle length = 16 bars
    for i in range(length):
        angle = (2.0 * math.pi * i) / 16.0
        wave = math.sin(angle) * 0.0100
        bars[i] = Bar(
            timestamp=bars[i].timestamp,
            open=1.1000 + wave,
            high=1.1005 + wave,
            low=1.0995 + wave,
            close=1.1000 + wave,
            volume=100.0,
            spread=0.0,
        )

    # Inject duplicate highs to trigger replacements (Bug #2 scenario)
    # Peak at 52 (initial candidate)
    bars[52] = Bar(
        timestamp=bars[52].timestamp,
        open=1.1000,
        high=1.1400, # Initial high
        low=1.1200, # Kept high so it is not a swing low
        close=1.1000,
        volume=100.0,
        spread=0.0,
    )
    # Peak at 56 (better candidate)
    bars[56] = Bar(
        timestamp=bars[56].timestamp,
        open=1.1000,
        high=1.1500, # Better high
        low=1.1200, # Kept high so it is not a swing low
        close=1.1000,
        volume=100.0,
        spread=0.0,
    )

    # Inject upgrade + replacement interaction (Bug #1 + Bug #2 interaction)
    # Trough at 124 (initial candidate)
    bars[124] = Bar(
        timestamp=bars[124].timestamp,
        open=1.1000,
        high=1.0700, # Kept low so it is not a swing high
        low=1.0500, # Deep low
        close=1.1000,
        volume=100.0,
        spread=0.0,
    )
    # Trough at 128 (better candidate)
    bars[128] = Bar(
        timestamp=bars[128].timestamp,
        open=1.1000,
        high=1.0700, # Kept low so it is not a swing high
        low=1.0400, # Deeper low (replaces 124)
        close=1.1000,
        volume=100.0,
        spread=0.0,
    )
    # Allow this low at 104 to be classified and eventually upgraded to MAJOR.
    # To do that, the surrounding bars [92 to 116] (12 bars left/right) must not exceed it.
    # Since low is 1.0400, it is well below the wave low (~1.0895). So it will qualify for MAJOR upgrade.

    swing_config = SwingConfig(
        left_bars=3,
        right_bars=3,
        minimum_bar_distance=5,
        filter_enabled=True,
        classification_enabled=True,
    )
    struct_config = StructureConfig(
        minimum_confirmations=2,
        major_only=False,
    )

    # 1. Batch Processing
    detector_batch = SwingDetector(config=swing_config)
    batch_swings = detector_batch.detect_batch(bars)

    engine_batch = MarketStructureEngine(config=struct_config)
    batch_structures = engine_batch.analyze(batch_swings)

    # 2. Incremental Processing Simulation
    detector_inc = SwingDetector(config=swing_config)
    graph_inc = SwingGraph()
    engine_inc = MarketStructureEngine(config=struct_config)
    inc_bars = []

    replacement_count = 0
    upgraded_count = 0

    for bar in bars:
        inc_bars.append(bar)
        res = detector_inc.detect_incremental(inc_bars, graph_inc)
        if res:
            if res.upgraded_swing:
                upgraded_count += 1
                engine_inc.handle_upgrade(res.upgraded_swing)

            if res.replaced_swing is not None:
                replacement_count += 1
                # Rebuild structure engine state since a past swing was replaced
                engine_inc.reset()
                for s in graph_inc.nodes:
                    engine_inc.update(s)

            for new_swing in res.new_swings:
                graph_inc.add_swing(new_swing)
                engine_inc.update(new_swing)

    incremental_swings = graph_inc.nodes
    incremental_structure = engine_inc.get_structure_state()
    batch_final_structure = engine_batch.get_structure_state()

    # --- Instrumentation Printout ---
    print("\n[Differential Test Stats]")
    print(f"Total bars: {length}")
    print(f"Replacement count: {replacement_count}")
    print(f"Upgrade count: {upgraded_count}")
    print(f"Batch swings count: {len(batch_swings)}")
    print(f"Incremental swings count: {len(incremental_swings)}")

    print("\nBATCH SWINGS:")
    for s in batch_swings:
        print(f"  {s.id}: idx={s.index}, price={s.price:.4f}, type={s.type.name}, class={s.classification.name}, prev={s.previous_id}, next={s.next_id}")

    print("\nINCREMENTAL SWINGS:")
    for s in incremental_swings:
        print(f"  {s.id}: idx={s.index}, price={s.price:.4f}, type={s.type.name}, class={s.classification.name}, prev={s.previous_id}, next={s.next_id}")

    # --- Assertions ---
    # 1. Parity of total swing count
    assert len(batch_swings) == len(incremental_swings), (
        f"Total swings mismatch: Batch={len(batch_swings)}, Incremental={len(incremental_swings)}"
    )

    # 2. Invariant properties of every swing node
    for idx, (b_swing, i_swing) in enumerate(zip(batch_swings, incremental_swings)):
        assert b_swing.id == i_swing.id, f"Swing index {idx} ID mismatch: {b_swing.id} vs {i_swing.id}"
        assert b_swing.index == i_swing.index, f"Swing index {idx} bar index mismatch: {b_swing.index} vs {i_swing.index}"
        assert b_swing.price == i_swing.price, f"Swing index {idx} price mismatch: {b_swing.price} vs {i_swing.price}"
        assert b_swing.type == i_swing.type, f"Swing index {idx} type mismatch: {b_swing.type} vs {i_swing.type}"
        assert b_swing.classification == i_swing.classification, (
            f"Swing index {idx} classification mismatch: {b_swing.classification} vs {i_swing.classification}"
        )
        assert b_swing.previous_id == i_swing.previous_id, (
            f"Swing index {idx} previous_id mismatch: {b_swing.previous_id} vs {i_swing.previous_id}"
        )
        assert b_swing.next_id == i_swing.next_id, (
            f"Swing index {idx} next_id mismatch: {b_swing.next_id} vs {i_swing.next_id}"
        )

    # 3. Structure Engine state parity
    assert incremental_structure.trend == batch_final_structure.trend, (
        f"Trend mismatch: {incremental_structure.trend} vs {batch_final_structure.trend}"
    )
    if batch_final_structure.active_major_high:
        assert incremental_structure.active_major_high.id == batch_final_structure.active_major_high.id
    if batch_final_structure.active_major_low:
        assert incremental_structure.active_major_low.id == batch_final_structure.active_major_low.id

    assert len(incremental_structure.breaks_history) == len(batch_final_structure.breaks_history)
