"""Unit tests for the BOS filtering utilities."""

import pandas as pd

from core.models import Bar
from market_structure.bos import get_bos_events
from market_structure.structure_models import BreakType, StructureBreak
from market_structure.swing_models import Swing, SwingClassification, SwingStrength, SwingType


def test_get_bos_events() -> None:
    """Verifies that get_bos_events correctly filters a list of breaks to BOS events only."""
    # Create dummy swing and bars
    swing1 = Swing(
        id="swing_1",
        timestamp=pd.Timestamp("2026-01-01"),
        index=1,
        price=1.1000,
        type=SwingType.HIGH,
        classification=SwingClassification.MAJOR,
        strength=1.0,
        strength_category=SwingStrength.NORMAL,
    )
    bar1 = Bar(
        timestamp=pd.Timestamp("2026-01-02"),
        open=1.0990,
        high=1.1010,
        low=1.0980,
        close=1.1005,
        volume=100.0,
    )

    brk_bos = StructureBreak(
        break_id="break_1",
        break_type=BreakType.BOS,
        broken_swing=swing1,
        breaking_bar=bar1,
        timestamp=bar1.timestamp,
    )

    brk_choch = StructureBreak(
        break_id="break_2",
        break_type=BreakType.CHoCH,
        broken_swing=swing1,
        breaking_bar=bar1,
        timestamp=bar1.timestamp,
    )

    breaks = [brk_bos, brk_choch]
    bos_events = get_bos_events(breaks)

    assert len(bos_events) == 1
    assert bos_events[0].break_id == "break_1"
    assert bos_events[0].break_type == BreakType.BOS
