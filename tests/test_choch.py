"""Unit tests for the CHoCH filtering utilities."""

import pandas as pd

from core.models import Bar
from market_structure.choch import get_choch_events
from market_structure.structure_models import BreakType, StructureBreak
from market_structure.swing_models import Swing, SwingClassification, SwingStrength, SwingType


def test_get_choch_events() -> None:
    """Verifies that get_choch_events correctly filters a list of breaks to CHoCH events only."""
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
    choch_events = get_choch_events(breaks)

    assert len(choch_events) == 1
    assert choch_events[0].break_id == "break_2"
    assert choch_events[0].break_type == BreakType.CHoCH
