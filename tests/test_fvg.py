"""Unit tests for the Fair Value Gap (FVG) Detector."""

from datetime import datetime, timedelta

from core.models import Bar
from smc.fvg import FVGDetector, FVGDirection


def _create_bars(prices: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Helper to generate dummy Bar sequence."""
    start = datetime(2026, 1, 1)
    bars = []
    for i, (o, h, low_val, c) in enumerate(prices):
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
    return bars


def test_empty_bars() -> None:
    """Verifies that empty bar sequence returns no FVGs."""
    detector = FVGDetector()
    assert detector.detect_fvgs([]) == []


def test_too_few_bars() -> None:
    """Verifies that sequences shorter than 3 bars return no FVGs."""
    detector = FVGDetector()
    bars = _create_bars([(1.1000, 1.1010, 1.0990, 1.1000), (1.1000, 1.1010, 1.0990, 1.1000)])
    assert detector.detect_fvgs(bars) == []


def test_bullish_and_bearish_fvg() -> None:
    """Verifies detection of bullish and bearish FVGs with gap size check."""
    # Bullish FVG at i=1:
    # bar0: high = 1.1010
    # bar1: o=1.1010, h=1.1050, l=1.1010, c=1.1040 (upward impulse)
    # bar2: low = 1.1030
    # Gap = 1.1030 - 1.1010 = 0.0020 (20 pips, > 1.0 pip default)
    #
    # Bearish FVG at i=4:
    # bar3: low = 1.1020
    # bar4: o=1.1020, h=1.1020, l=1.0960, c=1.0970 (downward impulse)
    # bar5: high = 1.0990
    # Gap = 1.1020 - 1.0990 = 0.0030 (30 pips, > 1.0 pip default)
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1000),  # bar 0
        (1.1010, 1.1050, 1.1010, 1.1040),  # bar 1 (imbalance)
        (1.1040, 1.1050, 1.1030, 1.1045),  # bar 2
        (1.1045, 1.1050, 1.1020, 1.1030),  # bar 3
        (1.1020, 1.1020, 1.0960, 1.0970),  # bar 4 (imbalance)
        (1.0970, 1.0990, 1.0960, 1.0980),  # bar 5
    ]
    bars = _create_bars(prices)
    detector = FVGDetector(min_gap_pips=15.0)
    fvgs = detector.detect_fvgs(bars)

    assert len(fvgs) == 2

    # Check Bullish FVG
    fvg_bull = fvgs[0]
    assert fvg_bull.direction == FVGDirection.BULLISH
    assert fvg_bull.start_index == 0
    assert fvg_bull.end_index == 2
    assert fvg_bull.lower_price == 1.1010
    assert fvg_bull.upper_price == 1.1030
    assert fvg_bull.is_mitigated is False

    # Check Bearish FVG
    fvg_bear = fvgs[1]
    assert fvg_bear.direction == FVGDirection.BEARISH
    assert fvg_bear.start_index == 3
    assert fvg_bear.end_index == 5
    assert fvg_bear.lower_price == 1.0990
    assert fvg_bear.upper_price == 1.1020
    assert fvg_bear.is_mitigated is False


def test_fvg_min_gap_filtering() -> None:
    """Verifies that gaps smaller than min_gap_pips are filtered out."""
    # Gap = 1.1015 - 1.1010 = 0.0005 (5 pips)
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1000),
        (1.1010, 1.1030, 1.1010, 1.1025),
        (1.1025, 1.1030, 1.1015, 1.1020),
    ]
    bars = _create_bars(prices)

    # 1. min_gap_pips = 10.0 (10 pips = 0.0010, so gap of 5 pips is filtered)
    detector_large = FVGDetector(min_gap_pips=10.0)
    assert len(detector_large.detect_fvgs(bars)) == 0

    # 2. min_gap_pips = 2.0 (2 pips = 0.0002, so gap of 5 pips is captured)
    detector_small = FVGDetector(min_gap_pips=2.0)
    assert len(detector_small.detect_fvgs(bars)) == 1


def test_fvg_custom_pip_size() -> None:
    """Verifies FVG detection with custom pip size (e.g. for JPY pairs)."""
    # For USDJPY, 1 pip = 0.01
    # Gap = 120.20 - 120.00 = 0.20 (20 pips)
    prices = [
        (120.00, 120.00, 119.80, 119.90),
        (119.90, 120.50, 119.90, 120.40),
        (120.40, 120.60, 120.20, 120.30),
    ]
    bars = _create_bars(prices)

    detector = FVGDetector(min_gap_pips=5.0, pip_size=0.01)
    fvgs = detector.detect_fvgs(bars)
    assert len(fvgs) == 1
    assert fvgs[0].lower_price == 120.00
    assert fvgs[0].upper_price == 120.20
