"""Unit tests for the Mitigation Monitor."""

from datetime import datetime, timedelta

from core.models import Bar
from smc.fvg import FairValueGap, FVGDirection
from smc.mitigation import MitigationMonitor
from smc.order_block import OBDirection, OrderBlock


def _create_bars(prices: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Helper to generate dummy bars."""
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


def test_empty_mitigation() -> None:
    """Verifies that empty input returns empty list."""
    monitor = MitigationMonitor()
    assert monitor.check_mitigation([], []) == []


def test_order_block_mitigation() -> None:
    """Verifies mitigation detection for bullish and bearish Order Blocks."""
    # i=0: OB candle (high=1.1020, low=1.0990)
    # i=1: high=1.1050, low=1.1030 (no touch of OB)
    # i=2: high=1.1030, low=1.1010 (touch high of OB 1.1020 -> mitigated!)
    prices = [
        (1.1000, 1.1020, 1.0990, 1.1010),  # i=0 (OB candle)
        (1.1010, 1.1050, 1.1030, 1.1040),  # i=1 (upward expansion)
        (1.1040, 1.1030, 1.1010, 1.1020),  # i=2 (re-test/touch)
    ]
    bars = _create_bars(prices)

    ob = OrderBlock(
        id="ob_0_bullish",
        bar_index=0,
        high=1.1020,
        low=1.0990,
        direction=OBDirection.BULLISH,
        timestamp=bars[0].timestamp,
        is_mitigated=False,
    )

    monitor = MitigationMonitor()

    # 1. Check with only bars[0] and bars[1] (should not be mitigated)
    res1 = monitor.check_mitigation(bars[:2], [ob])
    assert res1[0].is_mitigated is False

    # 2. Check with full sequence (should be mitigated)
    res2 = monitor.check_mitigation(bars, [ob])
    assert res2[0].is_mitigated is True


def test_fvg_mitigation() -> None:
    """Verifies mitigation detection for bullish and bearish Fair Value Gaps."""
    # i=0: high=1.1010
    # i=1: impulse candle
    # i=2: FVG confirmed (low=1.1030). FVG range is 1.1010 to 1.1030.
    # i=3: high=1.1050, low=1.1040 (above gap, no touch)
    # i=4: high=1.1040, low=1.1025 (enters gap [low <= 1.1030] -> mitigated!)
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1000),  # i=0
        (1.1010, 1.1050, 1.1010, 1.1040),  # i=1
        (1.1040, 1.1050, 1.1030, 1.1045),  # i=2
        (1.1045, 1.1050, 1.1040, 1.1045),  # i=3
        (1.1045, 1.1040, 1.1025, 1.1030),  # i=4
    ]
    bars = _create_bars(prices)

    fvg = FairValueGap(
        id="fvg_0_bullish",
        start_index=0,
        end_index=2,
        upper_price=1.1030,
        lower_price=1.1010,
        direction=FVGDirection.BULLISH,
        timestamp=bars[1].timestamp,
        is_mitigated=False,
    )

    monitor = MitigationMonitor()

    # 1. Check up to bar 3 (should not be mitigated)
    res1 = monitor.check_mitigation(bars[:4], [fvg])
    assert res1[0].is_mitigated is False

    # 2. Check up to bar 4 (should be mitigated)
    res2 = monitor.check_mitigation(bars, [fvg])
    assert res2[0].is_mitigated is True
