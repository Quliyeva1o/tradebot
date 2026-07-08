"""Unit tests for the Order Block (OB) Detector."""

from datetime import datetime, timedelta

from core.models import Bar
from market_structure.structure_models import BreakType, StructureBreak, StructureState
from market_structure.swing_models import Swing, SwingClassification, SwingStrength, SwingType
from smc.order_block import OBDirection, OrderBlockDetector


def _create_bars(prices: list[tuple[float, float, float, float, float]]) -> list[Bar]:
    """Helper to generate standard dummy Bar sequence with Volume."""
    start = datetime(2026, 1, 1)
    bars = []
    for i, (o, h, low_val, c, v) in enumerate(prices):
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=15 * i),
                open=o,
                high=h,
                low=low_val,
                close=c,
                volume=v,
            )
        )
    return bars


def test_empty_order_blocks() -> None:
    """Verifies that empty input returns no Order Blocks."""
    detector = OrderBlockDetector()
    assert detector.detect_order_blocks([], StructureState()) == []


def test_order_block_detection() -> None:
    """Verifies detection of bullish and bearish order blocks on structural breaks."""
    # Build bars
    # i=0: bullish, volume=100
    # i=1: bearish candle, volume=100 (potential OB candidate for bullish break)
    # i=2: Swing High pivot candle (high=1.1050), volume=100
    # i=3: break candle (close=1.1060 > 1.1050), volume=200
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1005, 10.0),  # i=0 (bullish)
        (1.1005, 1.1020, 1.0995, 1.1000, 100.0),  # i=1 (bearish OB candidate)
        (1.1000, 1.1050, 1.1000, 1.1040, 10.0),  # i=2 (swing high pivot)
        (1.1040, 1.1060, 1.1030, 1.1055, 10.0),  # i=3 (break bar)
    ]
    bars = _create_bars(prices)

    # Construct the Swing High that is broken
    swing_high = Swing(
        id="swing_2_high",
        timestamp=bars[2].timestamp,
        index=2,
        price=1.1050,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
        strength=1.0,
        strength_category=SwingStrength.NORMAL,
    )

    # Construct StructureState with a bullish break
    brk = StructureBreak(
        break_id="break_1",
        break_type=BreakType.BOS,
        broken_swing=swing_high,
        breaking_bar=bars[3],
        timestamp=bars[3].timestamp,
    )
    state = StructureState(breaks_history=[brk])

    # 1. Test with volume multiplier = 0.0 (disable volume checks)
    detector = OrderBlockDetector(volume_multiplier=0.0)
    obs = detector.detect_order_blocks(bars, state)

    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction == OBDirection.BULLISH
    assert ob.bar_index == 1  # Last bearish bar before the swing high
    assert ob.high == 1.1020
    assert ob.low == 1.0995
    assert ob.is_mitigated is False

    # Verify that "swing" mode also selects the same bar in this configuration
    detector_swing = OrderBlockDetector(volume_multiplier=0.0, anchor_mode="swing")
    obs_swing = detector_swing.detect_order_blocks(bars, state)
    assert len(obs_swing) == 1
    assert obs_swing[0].bar_index == 1
    assert obs_swing[0].high == 1.1020
    assert obs_swing[0].low == 1.0995

    # 2. Test with volume multiplier = 1.5
    # Preceding volume at index 1 is bars[0].volume = 10.0, avg_volume = 10.0.
    # bars[1].volume = 100.0 >= 10.0 * 1.5 (passes volume check!)
    detector_vol = OrderBlockDetector(volume_multiplier=1.5)
    obs_vol = detector_vol.detect_order_blocks(bars, state)
    assert len(obs_vol) == 1

    # 3. Test with high volume multiplier = 15.0
    # 100.0 < 10.0 * 15.0 (should fail volume check!)
    detector_vol_high = OrderBlockDetector(volume_multiplier=15.0)
    obs_vol_high = detector_vol_high.detect_order_blocks(bars, state)
    assert len(obs_vol_high) == 0


def test_order_block_anchor_modes() -> None:
    """Verifies that breaking_bar and swing anchor modes select different candidates when appropriate."""
    # Setup bars:
    # i=0: bullish (open=1.1000, close=1.1010), volume=10.0
    # i=1: bearish (open=1.1010, close=1.1000), volume=10.0  <- Potential OB candidate for 'swing'
    # i=2: Swing High (open=1.1000, close=1.1040, high=1.1050), volume=10.0
    # i=3: bearish (open=1.1040, close=1.1030), volume=10.0  <- Potential OB candidate for 'breaking_bar'
    # i=4: break candle (open=1.1030, close=1.1060), volume=10.0
    prices = [
        (1.1000, 1.1010, 1.0990, 1.1010, 10.0),  # i=0 (bullish)
        (1.1010, 1.1020, 1.0995, 1.1000, 10.0),  # i=1 (bearish, near swing)
        (1.1000, 1.1050, 1.1000, 1.1040, 10.0),  # i=2 (swing high pivot)
        (1.1040, 1.1045, 1.1025, 1.1030, 10.0),  # i=3 (bearish, near break bar)
        (1.1030, 1.1060, 1.1020, 1.1060, 10.0),  # i=4 (break bar)
    ]
    bars = _create_bars(prices)

    swing_high = Swing(
        id="swing_2_high",
        timestamp=bars[2].timestamp,
        index=2,
        price=1.1050,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
        strength=1.0,
        strength_category=SwingStrength.NORMAL,
    )

    brk = StructureBreak(
        break_id="break_1",
        break_type=BreakType.BOS,
        broken_swing=swing_high,
        breaking_bar=bars[4],
        timestamp=bars[4].timestamp,
    )
    state = StructureState(breaks_history=[brk])

    # 1. Test "breaking_bar" mode (should find the bearish candle at index 3, which is closer to the break bar)
    detector_breaking = OrderBlockDetector(volume_multiplier=0.0, anchor_mode="breaking_bar")
    obs_breaking = detector_breaking.detect_order_blocks(bars, state)
    assert len(obs_breaking) == 1
    assert obs_breaking[0].bar_index == 3
    assert obs_breaking[0].high == 1.1045
    assert obs_breaking[0].low == 1.1025

    # 2. Test "swing" mode (should find the bearish candle at index 1, which is at/before the swing high)
    detector_swing = OrderBlockDetector(volume_multiplier=0.0, anchor_mode="swing")
    obs_swing = detector_swing.detect_order_blocks(bars, state)
    assert len(obs_swing) == 1
    assert obs_swing[0].bar_index == 1
    assert obs_swing[0].high == 1.1020
    assert obs_swing[0].low == 1.0995

    # 3. Test default behavior (should be "breaking_bar" mode)
    detector_default = OrderBlockDetector(volume_multiplier=0.0)
    obs_default = detector_default.detect_order_blocks(bars, state)
    assert len(obs_default) == 1
    assert obs_default[0].bar_index == 3
