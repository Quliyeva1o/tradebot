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


def test_order_block_touch_does_not_mitigate_bullish() -> None:
    """Bug #22: a bullish OB retest (price re-enters the zone, or even wicks
    below it) must NOT mitigate it -- that touch is the intended entry
    trigger, not invalidation. Old behavior (pre-fix) would have marked
    this mitigated on the mere touch at i=2.
    """
    # OB zone: low=1.0990, high=1.1020
    prices = [
        (1.1000, 1.1020, 1.0990, 1.1010),  # i=0 (OB candle)
        (1.1010, 1.1050, 1.1005, 1.1040),  # i=1 (upward expansion)
        (1.1030, 1.1030, 1.0985, 1.1005),  # i=2 (wicks through zone, closes INSIDE it -- a retest, not invalidation)
        (1.1005, 1.1035, 1.1000, 1.1025),  # i=3 (bounces back up out of the zone)
    ]
    bars = _create_bars(prices)
    ob = OrderBlock(
        id="ob_0_bullish", bar_index=0, high=1.1020, low=1.0990,
        direction=OBDirection.BULLISH, timestamp=bars[0].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()
    result = monitor.check_mitigation(bars, [ob])

    assert result[0].is_mitigated is False


def test_order_block_full_close_through_mitigates_bullish() -> None:
    """Bug #22: a bullish OB IS mitigated once a bar CLOSES below its low
    (fully closed through the far edge), not merely touches it.
    """
    prices = [
        (1.1000, 1.1020, 1.0990, 1.1010),  # i=0 (OB candle, zone low=1.0990 high=1.1020)
        (1.1010, 1.1050, 1.1005, 1.1040),  # i=1 (upward expansion)
        (1.1030, 1.1030, 1.0985, 1.1005),  # i=2 (retest, closes inside -- not mitigated)
        (1.1005, 1.1010, 1.0950, 1.0960),  # i=3 (closes BELOW zone.low=1.0990 -- fully invalidated)
    ]
    bars = _create_bars(prices)
    ob = OrderBlock(
        id="ob_0_bullish", bar_index=0, high=1.1020, low=1.0990,
        direction=OBDirection.BULLISH, timestamp=bars[0].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()

    res_before = monitor.check_mitigation(bars[:3], [ob])
    assert res_before[0].is_mitigated is False

    res_after = monitor.check_mitigation(bars, [ob])
    assert res_after[0].is_mitigated is True


def test_order_block_touch_does_not_mitigate_bearish() -> None:
    """Bug #22 symmetric case: a bearish OB retest must not mitigate it."""
    # OB zone: low=1.0990, high=1.1020
    prices = [
        (1.1010, 1.1020, 1.0990, 1.1000),  # i=0 (OB candle)
        (1.1000, 1.1000, 1.0960, 1.0970),  # i=1 (downward expansion)
        (1.0970, 1.1005, 1.0965, 1.1000),  # i=2 (wicks through zone, closes INSIDE it -- a retest)
        (1.1000, 1.1000, 1.0960, 1.0965),  # i=3 (bounces back down out of the zone)
    ]
    bars = _create_bars(prices)
    ob = OrderBlock(
        id="ob_0_bearish", bar_index=0, high=1.1020, low=1.0990,
        direction=OBDirection.BEARISH, timestamp=bars[0].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()
    result = monitor.check_mitigation(bars, [ob])

    assert result[0].is_mitigated is False


def test_order_block_full_close_through_mitigates_bearish() -> None:
    """Bug #22 symmetric case: a bearish OB IS mitigated once a bar closes
    above its high.
    """
    prices = [
        (1.1010, 1.1020, 1.0990, 1.1000),  # i=0 (OB candle, zone low=1.0990 high=1.1020)
        (1.1000, 1.1000, 1.0960, 1.0970),  # i=1 (downward expansion)
        (1.0970, 1.1005, 1.0965, 1.1000),  # i=2 (retest, closes inside -- not mitigated)
        (1.1000, 1.1050, 1.0995, 1.1035),  # i=3 (closes ABOVE zone.high=1.1020 -- fully invalidated)
    ]
    bars = _create_bars(prices)
    ob = OrderBlock(
        id="ob_0_bearish", bar_index=0, high=1.1020, low=1.0990,
        direction=OBDirection.BEARISH, timestamp=bars[0].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()

    res_before = monitor.check_mitigation(bars[:3], [ob])
    assert res_before[0].is_mitigated is False

    res_after = monitor.check_mitigation(bars, [ob])
    assert res_after[0].is_mitigated is True


def test_fvg_touch_does_not_mitigate_bullish() -> None:
    """Bug #22: a bullish FVG retest (price re-enters the gap) must not
    mitigate it -- only a full close through the far (lower) edge should.
    """
    # FVG range: lower=1.1010, upper=1.1030
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1000),  # i=0
        (1.1010, 1.1050, 1.1010, 1.1040),  # i=1
        (1.1040, 1.1050, 1.1030, 1.1045),  # i=2 (FVG confirmed here)
        (1.1045, 1.1050, 1.1040, 1.1045),  # i=3 (above gap, no touch)
        (1.1045, 1.1045, 1.1005, 1.1015),  # i=4 (wicks through gap, closes INSIDE it -- a retest)
    ]
    bars = _create_bars(prices)
    fvg = FairValueGap(
        id="fvg_0_bullish", start_index=0, end_index=2, upper_price=1.1030, lower_price=1.1010,
        direction=FVGDirection.BULLISH, timestamp=bars[1].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()
    result = monitor.check_mitigation(bars, [fvg])

    assert result[0].is_mitigated is False


def test_fvg_full_close_through_mitigates_bullish() -> None:
    """Bug #22: a bullish FVG IS mitigated once a bar closes below its
    lower_price (fully closed through the far edge).
    """
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1000),  # i=0
        (1.1010, 1.1050, 1.1010, 1.1040),  # i=1
        (1.1040, 1.1050, 1.1030, 1.1045),  # i=2 (FVG confirmed, range 1.1010-1.1030)
        (1.1045, 1.1045, 1.1005, 1.1015),  # i=3 (retest, closes inside -- not mitigated)
        (1.1015, 1.1015, 1.0975, 1.0990),  # i=4 (closes BELOW lower_price=1.1010 -- fully invalidated)
    ]
    bars = _create_bars(prices)
    fvg = FairValueGap(
        id="fvg_0_bullish", start_index=0, end_index=2, upper_price=1.1030, lower_price=1.1010,
        direction=FVGDirection.BULLISH, timestamp=bars[1].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()

    res_before = monitor.check_mitigation(bars[:4], [fvg])
    assert res_before[0].is_mitigated is False

    res_after = monitor.check_mitigation(bars, [fvg])
    assert res_after[0].is_mitigated is True


def test_fvg_touch_does_not_mitigate_bearish() -> None:
    """Bug #22 symmetric case: a bearish FVG retest must not mitigate it."""
    # FVG range: lower=1.1010, upper=1.1030
    prices = [
        (1.1040, 1.1050, 1.1030, 1.1040),  # i=0
        (1.1030, 1.1030, 1.0990, 1.1000),  # i=1
        (1.1000, 1.1010, 1.0990, 1.0995),  # i=2 (FVG confirmed here)
        (1.0995, 1.1000, 1.0985, 1.0990),  # i=3 (below gap, no touch)
        (1.0990, 1.1025, 1.1000, 1.1015),  # i=4 (wicks through gap, closes INSIDE it -- a retest)
    ]
    bars = _create_bars(prices)
    fvg = FairValueGap(
        id="fvg_0_bearish", start_index=0, end_index=2, upper_price=1.1030, lower_price=1.1010,
        direction=FVGDirection.BEARISH, timestamp=bars[1].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()
    result = monitor.check_mitigation(bars, [fvg])

    assert result[0].is_mitigated is False


def test_fvg_full_close_through_mitigates_bearish() -> None:
    """Bug #22 symmetric case: a bearish FVG IS mitigated once a bar closes
    above its upper_price.
    """
    prices = [
        (1.1040, 1.1050, 1.1030, 1.1040),  # i=0
        (1.1030, 1.1030, 1.0990, 1.1000),  # i=1
        (1.1000, 1.1010, 1.0990, 1.0995),  # i=2 (FVG confirmed, range 1.1010-1.1030)
        (1.0990, 1.1025, 1.1000, 1.1015),  # i=3 (retest, closes inside -- not mitigated)
        (1.1015, 1.1050, 1.1010, 1.1040),  # i=4 (closes ABOVE upper_price=1.1030 -- fully invalidated)
    ]
    bars = _create_bars(prices)
    fvg = FairValueGap(
        id="fvg_0_bearish", start_index=0, end_index=2, upper_price=1.1030, lower_price=1.1010,
        direction=FVGDirection.BEARISH, timestamp=bars[1].timestamp, is_mitigated=False,
    )

    monitor = MitigationMonitor()

    res_before = monitor.check_mitigation(bars[:4], [fvg])
    assert res_before[0].is_mitigated is False

    res_after = monitor.check_mitigation(bars, [fvg])
    assert res_after[0].is_mitigated is True
