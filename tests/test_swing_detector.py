"""Unit tests for the Swing Detection Engine.

Validates normal patterns, ranges, equal boundary limits, configuration shifts,
and performance stress tests.
"""

import time
from datetime import datetime, timedelta

import pandas as pd
import pytest

from core.exceptions import DataValidationError, InvalidTimestampError, MissingColumnError
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import SwingClassification, SwingConfig, SwingStrength, SwingType


def _create_base_df(length: int) -> pd.DataFrame:
    """Helper to generate a baseline standard DataFrame structure."""
    start = datetime(2026, 1, 1)
    timestamps = [start + timedelta(minutes=15 * i) for i in range(length)]

    df = pd.DataFrame(
        {
            "time": timestamps,
            "open": [1.1000] * length,
            "high": [1.1005] * length,
            "low": [1.0995] * length,
            "close": [1.1000] * length,
            "volume": [100.0] * length,
        }
    )
    return df


def test_normal_market_swings() -> None:
    """Verifies pivot detection under standard fluctuating market conditions."""
    df = _create_base_df(10)

    # Inject a clear swing high at index 4
    # Surrounding highs (left=3, right=3): indices 1,2,3 and 5,6,7 are lower
    df.loc[4, "high"] = 1.1020

    # Inject a clear swing low at index 5
    # Surrounding lows (left=3, right=3): indices 2,3,4 and 6,7,8 are higher
    df.loc[5, "low"] = 1.0980

    config = SwingConfig(left_bars=3, right_bars=3)
    detector = SwingDetector(config=config)
    res = detector.detect(df)

    assert res.loc[4, "is_swing_high"]
    assert res.loc[4, "swing_type"] == SwingType.HIGH.value
    assert res.loc[4, "swing_price"] == 1.1020
    assert res.loc[4, "swing_strength"] == SwingStrength.STRONG.value

    assert res.loc[5, "is_swing_low"]
    assert res.loc[5, "swing_type"] == SwingType.LOW.value
    assert res.loc[5, "swing_price"] == 1.0980
    assert res.loc[5, "swing_strength"] == SwingStrength.STRONG.value

    # Index 0 and 9 cannot be swings due to window limits
    assert not res.loc[0, "is_swing_high"]
    assert not res.loc[9, "is_swing_low"]


def test_trending_market() -> None:
    """Verifies that a consistently upward trend doesn't trigger false swings."""
    df = _create_base_df(15)
    # Monotonically increasing highs and lows (upward trend)
    for i in range(15):
        df.loc[i, "high"] = 1.1000 + (i * 0.0010)
        df.loc[i, "low"] = 1.0990 + (i * 0.0010)

    detector = SwingDetector(config=SwingConfig(left_bars=3, right_bars=3))
    res = detector.detect(df)

    # Since prices are rising monotonically, no candle can be higher than its right neighbors
    assert not res["is_swing_high"].any()
    assert not res["is_swing_low"].any()


def test_equal_highs_lows() -> None:
    """Verifies behavior for equal prices based on allow_equal configurations."""
    df = _create_base_df(10)

    # Inject equal adjacent highs at indices 4 and 5
    df.loc[4, "high"] = 1.1050
    df.loc[5, "high"] = 1.1050

    # Scenario A: Equal highs disallowed (default)
    detector_no_eq = SwingDetector(config=SwingConfig(allow_equal_highs=False))
    res_no_eq = detector_no_eq.detect(df)
    assert not res_no_eq["is_swing_high"].any()

    # Scenario B: Equal highs allowed
    detector_eq = SwingDetector(config=SwingConfig(allow_equal_highs=True))
    res_eq = detector_eq.detect(df)
    assert res_eq.loc[4, "is_swing_high"]
    assert res_eq.loc[5, "is_swing_high"]


def test_small_datasets() -> None:
    """Verifies that datasets smaller than the configuration window raise exceptions."""
    df = _create_base_df(5)  # size 5

    # Window of left=3, right=3 requires at least 7 bars
    detector = SwingDetector(config=SwingConfig(left_bars=3, right_bars=3))

    with pytest.raises(DataValidationError, match="Dataset too small"):
        detector.detect(df)


def test_missing_column() -> None:
    """Verifies that MissingColumnError is raised for invalid inputs."""
    df = _create_base_df(10)
    df.drop(columns=["high"], inplace=True)

    detector = SwingDetector()
    with pytest.raises(MissingColumnError):
        detector.detect(df)


def test_unsorted_timestamps() -> None:
    """Verifies that out-of-order timestamps raise InvalidTimestampError."""
    df = _create_base_df(10)
    # Swap timestamps of index 3 and 4
    t3 = df.loc[3, "time"]
    df.loc[3, "time"] = df.loc[4, "time"]
    df.loc[4, "time"] = t3

    detector = SwingDetector()
    with pytest.raises(InvalidTimestampError):
        detector.detect(df)


def test_duplicate_timestamps() -> None:
    """Verifies that duplicate timestamps raise DuplicateTimestampError."""
    df = _create_base_df(10)
    df.loc[4, "time"] = df.loc[3, "time"]

    detector = SwingDetector()
    from core.exceptions import DuplicateTimestampError

    with pytest.raises(DuplicateTimestampError):
        detector.detect(df)


def test_configuration_changes() -> None:
    """Verifies swing detection adjusts correctly to configuration overrides."""
    df = _create_base_df(15)
    # Swing High tip at index 6
    df.loc[6, "high"] = 1.1200

    # High at index 7 is closer than 3 bars (minimum distance filter test)
    df.loc[7, "high"] = 1.1180

    # With minimum distance = 2, index 6 and 7 would both be swing highs if they satisfy boundaries,
    # but index 7 is closer than minimum distance. Let's verify resolution.
    config = SwingConfig(left_bars=2, right_bars=2, minimum_distance_between_swings=3)
    detector = SwingDetector(config=config)
    res = detector.detect(df)

    # Should flag 6 but drop 7 because 7 - 6 = 1 < 3 (min_distance) and index 6 has a higher high
    assert res.loc[6, "is_swing_high"]
    assert not res.loc[7, "is_swing_high"]


def test_large_dataset_performance() -> None:
    """Stress tests the engine with 100,000 candles to ensure target speed limit (< 1s)."""
    length = 100000
    df = _create_base_df(length)

    # Inject periodic swings to make computation realistic
    for i in range(10, length - 10, 20):
        df.loc[i, "high"] = 1.1100
        df.loc[i + 5, "low"] = 1.0900

    detector = SwingDetector(config=SwingConfig(left_bars=3, right_bars=3))

    start_time = time.perf_counter()
    res = detector.detect(df)
    elapsed = time.perf_counter() - start_time

    print(f"Elapsed performance stress test time: {elapsed:.4f} seconds")
    assert elapsed < 1.0, f"Performance check failed. Execution took {elapsed:.4f}s"
    assert res["is_swing_high"].sum() > 0


def test_swing_identity_and_properties() -> None:
    """Verifies swing models are correctly populated with unique, immutable IDs that survive filtering."""
    df = _create_base_df(15)
    df.loc[4, "high"] = 1.1200
    df.loc[8, "low"] = 1.0800

    detector = SwingDetector(config=SwingConfig(left_bars=3, right_bars=3))
    detector.detect(df)

    raw = detector.get_raw_swings()
    filtered = detector.get_filtered_swings()

    assert len(raw) >= 2
    # Check ID structure
    high_swing = next(s for s in raw if s.type == SwingType.HIGH)
    assert high_swing.id == "swing_4_high"
    assert high_swing.index == 4
    assert high_swing.price == 1.1200
    assert high_swing.timestamp == df.loc[4, "time"]

    low_swing = next(s for s in raw if s.type == SwingType.LOW)
    assert low_swing.id == "swing_8_low"
    assert low_swing.index == 8
    assert low_swing.price == 1.0800

    # Ensure IDs survive filtering
    filtered_ids = {s.id for s in filtered}
    assert "swing_4_high" in filtered_ids
    assert "swing_8_low" in filtered_ids


def test_major_minor_classification() -> None:
    """Verifies that classification successfully divides swings into Major, Minor, or Unknown."""
    df = _create_base_df(20)

    # Minor swing high at 4 (satisfies left=3, right=3 but not left=6, right=6)
    df.loc[4, "high"] = 1.1050
    # Inject low to prevent duplicate high filtering
    df.loc[7, "low"] = 1.0800
    # Major swing high at 10 (satisfies left=6, right=6)
    df.loc[10, "high"] = 1.1200

    config = SwingConfig(left_bars=3, right_bars=3, classification_enabled=True)
    detector = SwingDetector(config=config)
    detector.detect(df)

    majors = detector.get_major_swings()
    minors = detector.get_minor_swings()

    major_indices = [s.index for s in majors]
    minor_indices = [s.index for s in minors]

    # Swing at 10 should be MAJOR because left_bars*2=6 and right_bars*2=6 surrounding highs are lower
    assert 10 in major_indices
    # Swing at 4 should be MINOR because within 6 bars to its right (at index 10), there is a higher high
    assert 4 in minor_indices


def test_swing_filtering_rules() -> None:
    """Verifies modular filters for bar distance, price distance, and duplicates."""
    df = _create_base_df(20)

    # 1. Bar distance test: Two highs close to each other (indices 4 and 6, distance = 2)
    # If minimum_bar_distance = 3, only one should survive (the higher one at 6)
    df.loc[4, "high"] = 1.1100
    df.loc[6, "high"] = 1.1200

    # 2. Duplicate swings check: two consecutive lows (indices 8 and 12) without high in between
    # Duplicate filter keeps only the extreme (lower low at index 12)
    df.loc[8, "low"] = 1.0900
    df.loc[12, "low"] = 1.0800

    config = SwingConfig(
        left_bars=2,
        right_bars=2,
        minimum_bar_distance=3,
        minimum_price_distance=0.0050,
    )
    detector = SwingDetector(config=config)
    detector.detect(df)

    swings = detector.get_swings()
    indices = [s.index for s in swings]

    # Index 4 is filtered out in favor of index 6
    assert 4 not in indices
    assert 6 in indices

    # Index 8 is filtered out in favor of index 12 (both are lows, 12 is lower low)
    assert 8 not in indices
    assert 12 in indices


def test_swing_graph_relationships() -> None:
    """Verifies structural links, distance calculations, and graph traversal logic."""
    df = _create_base_df(30)
    df.loc[4, "high"] = 1.1200
    df.loc[10, "low"] = 1.0800
    df.loc[16, "high"] = 1.1300
    df.loc[22, "low"] = 1.0700

    config = SwingConfig(left_bars=3, right_bars=3, classification_enabled=True)
    detector = SwingDetector(config=config)
    detector.detect(df)

    swings = detector.get_swings()
    assert len(swings) == 4

    graph = detector.get_swing_graph()
    assert graph is not None

    s4 = graph.get_swing("swing_4_high")
    s10 = graph.get_swing("swing_10_low")
    s16 = graph.get_swing("swing_16_high")
    s22 = graph.get_swing("swing_22_low")

    assert s4 is not None
    assert s10 is not None
    assert s16 is not None
    assert s22 is not None

    # Check immediate relationships
    assert s4.next_id == s10.id
    assert s10.previous_id == s4.id
    assert s10.next_id == s16.id
    assert s16.previous_id == s10.id

    # Check distances
    assert s10.bar_distance == 6
    assert s10.price_distance is not None
    assert round(s10.price_distance, 4) == -0.0400

    # Test graph traversal helpers
    prev_high = graph.get_previous_of_type(s16, SwingType.HIGH)
    assert prev_high is not None
    assert prev_high.id == s4.id

    next_low = graph.get_next_of_type(s10, SwingType.LOW)
    assert next_low is not None
    assert next_low.id == s22.id

    # Verify Major traversal
    prev_major = graph.get_previous_major(s16)
    if prev_major:
        assert prev_major.classification == SwingClassification.MAJOR


def test_configuration_changes_impact() -> None:
    """Verifies that disabling filtering or classification changes the output properly."""
    df = _create_base_df(15)
    # Close swings that would normally get filtered (minimum_bar_distance = 3)
    df.loc[4, "high"] = 1.1200
    df.loc[6, "high"] = 1.1300

    # Scenario A: Filtering disabled (use left_bars=1, right_bars=1 so both are raw swings)
    config_no_filter = SwingConfig(
        left_bars=1, right_bars=1, minimum_bar_distance=3, filter_enabled=False
    )
    detector_no_filter = SwingDetector(config=config_no_filter)
    detector_no_filter.detect(df)
    # Both highs should exist in output
    assert len(detector_no_filter.get_swings()) == 2

    # Scenario B: Classification disabled
    config_no_class = SwingConfig(left_bars=2, right_bars=2, classification_enabled=False)
    detector_no_class = SwingDetector(config=config_no_class)
    detector_no_class.detect(df)
    # Swings should all be UNKNOWN classification
    for s in detector_no_class.get_swings():
        assert s.classification == SwingClassification.UNKNOWN
