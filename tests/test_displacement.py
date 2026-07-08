"""Unit tests for the Displacement Detector."""

from datetime import datetime, timedelta

import pytest

from core.models import Bar
from smc.displacement import DisplacementDetector


def _create_bars(
    ranges: list[float], directions: list[int], default_price: float = 1.1000
) -> list[Bar]:
    """Helper to generate dummy bars with specified ranges and directions (1=bullish, -1=bearish)."""
    start = datetime(2026, 1, 1)
    bars = []
    curr_close = default_price
    for i, (rng, dir_val) in enumerate(zip(ranges, directions, strict=True)):
        o = curr_close
        if dir_val > 0:
            c = o + rng * 0.8  # Large body
            h = c + rng * 0.1
            low_val = o - rng * 0.1
        elif dir_val < 0:
            c = o - rng * 0.8  # Large body
            h = o + rng * 0.1
            low_val = c - rng * 0.1
        else:
            c = o  # Doji
            h = o + rng * 0.5
            low_val = o - rng * 0.5
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=15 * i),
                open=o,
                high=h,
                low=low_val,
                close=c,
                volume=100.0,
            )
        )
        curr_close = c
    return bars


def test_empty_displacement() -> None:
    """Verifies that empty input returns no displacement bars."""
    detector = DisplacementDetector()
    assert detector.find_displacements([]) == []


def test_displacement_detection() -> None:
    """Verifies that unusually large expansion candles are flagged as displacements."""
    # Let's generate 15 bars:
    # First 14 bars have small range (around 0.0005 pips)
    # The 15th bar has a massive range (0.0030 pips)
    ranges = [0.0005] * 14 + [0.0030]
    directions = [1] * 14 + [-1]  # Bullish followed by a Bearish expansion

    bars = _create_bars(ranges, directions)
    detector = DisplacementDetector(atr_multiplier=2.0, atr_period=14)
    displacements = detector.find_displacements(bars)

    # We expect the 15th bar (index 14) to be flagged as a bearish displacement
    assert len(displacements) == 1
    db = displacements[0]
    assert db.bar_index == 14
    assert db.direction == "BEARISH"
    assert db.range == pytest.approx(0.0030 * 1.0)  # total high-low range is 0.0030


def test_doji_not_displacement() -> None:
    """Verifies that a high volatility doji candle is not flagged as displacement."""
    # Doji has a large high-low range but a tiny close-open body.
    # We create 14 small candles and then 1 large range doji.
    ranges = [0.0005] * 14 + [0.0030]
    directions = [1] * 14 + [0]  # The last candle is a Doji (direction=0)

    bars = _create_bars(ranges, directions)
    detector = DisplacementDetector(atr_multiplier=2.0, atr_period=14)
    displacements = detector.find_displacements(bars)

    # Even though the 15th bar range is large, body is 0, so body >= 50% range check fails.
    assert len(displacements) == 0
