"""Unit tests for the Breaker Block Detector."""

from datetime import datetime, timedelta

from core.models import Bar
from smc.breaker import BreakerDetector
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


def test_empty_breakers() -> None:
    """Verifies that empty inputs return no breaker blocks."""
    detector = BreakerDetector()
    assert detector.detect_breakers([], []) == []


def test_breaker_detection() -> None:
    """Verifies that failed order blocks are correctly converted into breaker blocks."""
    # i=0: OB candle (high=1.1020, low=1.0990)
    # i=1: high=1.1030, low=1.1010 (no break below low 1.0990)
    # i=2: high=1.1000, low=1.0980 (close=1.0985 < low 1.0990 -> broken!)
    prices = [
        (1.1000, 1.1020, 1.0990, 1.1010),  # i=0
        (1.1010, 1.1030, 1.1010, 1.1020),  # i=1
        (1.1020, 1.1000, 1.0980, 1.0985),  # i=2 (breaking candle)
    ]
    bars = _create_bars(prices)

    ob = OrderBlock(
        id="ob_0_bullish",
        bar_index=0,
        high=1.1020,
        low=1.0990,
        direction=OBDirection.BULLISH,
        timestamp=bars[0].timestamp,
        is_mitigated=True,
    )

    detector = BreakerDetector()

    # 1. Test up to bar 1 (no break yet)
    res1 = detector.detect_breakers(bars[:2], [ob])
    assert len(res1) == 0

    # 2. Test full sequence (breaker detected at bar 2)
    res2 = detector.detect_breakers(bars, [ob])
    assert len(res2) == 1
    breaker = res2[0]
    assert breaker.id == "breaker_ob_0_bullish"
    assert breaker.direction == "BEARISH"
    assert breaker.high == 1.1020
    assert breaker.low == 1.0990
    assert breaker.timestamp == bars[2].timestamp
    assert breaker.source_ob_id == ob.id
