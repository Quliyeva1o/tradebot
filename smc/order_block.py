"""Order Block (OB) identification module."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from core.models import Bar
from market_structure.structure_models import StructureState
from market_structure.swing_models import SwingType


class OBDirection(Enum):
    """Direction of the Order Block."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class OrderBlock:
    """Represents a validated institutional Order Block.

    Attributes:
        id: Unique identifier format ob_{bar_index}_{direction}.
        bar_index: Index of the candle identified as the Order Block.
        high: High price limit of the OB candle.
        low: Low price limit of the OB candle.
        direction: OBDirection (BULLISH/BEARISH).
        timestamp: The timestamp of the OB candle.
        is_mitigated: True once price has fully closed through the OB and
            past its far edge, invalidating it. A close that merely enters
            or retests the zone (the intended entry trigger) does NOT set
            this -- see MitigationMonitor.check_mitigation (Bug #22).
    """

    id: str
    bar_index: int
    high: float
    low: float
    direction: OBDirection
    timestamp: datetime
    is_mitigated: bool = False


class OrderBlockDetector:
    """Identifies institutional order blocks based on market structure changes and volume."""

    def __init__(
        self,
        volume_multiplier: float = 1.5,
        anchor_mode: Literal["breaking_bar", "swing"] = "breaking_bar",
    ) -> None:
        """Initializes the OrderBlockDetector.

        Args:
            volume_multiplier: Volume check threshold relative to moving average.
            anchor_mode: Method to determine the backward search starting point.
                - "breaking_bar": Begins backward search from the breaking bar itself. Assumes the OB is
                  the last opposite candle close to the breakout moment.
                - "swing": Begins backward search from the broken swing pivot high/low. Assumes the OB
                  is the opposite candle that formed right at the swing pivot before price moved away.
        """
        self.volume_multiplier = volume_multiplier
        self.anchor_mode = anchor_mode

    def detect_order_blocks(
        self, bars: Sequence[Bar], structure_state: StructureState
    ) -> list[OrderBlock]:
        """Detects bullish and bearish order blocks in price action.

        Args:
            bars: Sequence of Bar objects representing price history.
            structure_state: The current structure state containing breaks history.

        Returns:
            A list of detected OrderBlock objects.
        """
        order_blocks: list[OrderBlock] = []
        if not bars or not structure_state.breaks_history:
            return order_blocks

        # Map timestamps to indices for fast lookup
        ts_to_idx = {bar.timestamp: idx for idx, bar in enumerate(bars)}

        for brk in structure_state.breaks_history:
            # 1. Determine direction
            if brk.broken_swing.type == SwingType.HIGH:
                ob_direction = OBDirection.BULLISH
            elif brk.broken_swing.type == SwingType.LOW:
                ob_direction = OBDirection.BEARISH
            else:
                continue

            # Get start index for backwards search based on anchor_mode
            if self.anchor_mode == "breaking_bar":
                start_idx = ts_to_idx.get(brk.breaking_bar.timestamp)
                if start_idx is None:
                    continue
            else:
                # "swing" mode
                swing_idx = ts_to_idx.get(brk.broken_swing.timestamp)
                if swing_idx is None:
                    swing_idx = brk.broken_swing.index
                    if swing_idx < 0 or swing_idx >= len(bars):
                        continue
                start_idx = swing_idx

            # 2. Search backwards from start_idx to find the last opposite candle
            candidate_idx = -1
            for idx in range(start_idx, -1, -1):
                bar = bars[idx]
                if ob_direction == OBDirection.BULLISH:
                    # Bullish OB: last bearish candle (close < open) before the break
                    if bar.close < bar.open:
                        candidate_idx = idx
                        break
                else:
                    # Bearish OB: last bullish candle (close > open) before the break
                    if bar.close > bar.open:
                        candidate_idx = idx
                        break

            # Fallback if no opposite candle is found
            if candidate_idx == -1:
                candidate_idx = start_idx

            candidate_bar = bars[candidate_idx]

            # 3. Volume filter check relative to preceding 20 candles
            if self.volume_multiplier > 0:
                prec_volumes = [
                    bars[j].volume for j in range(max(0, candidate_idx - 20), candidate_idx)
                ]
                avg_volume = sum(prec_volumes) / len(prec_volumes) if prec_volumes else 0.0
                if avg_volume > 0 and candidate_bar.volume < avg_volume * self.volume_multiplier:
                    continue

            # 4. Create OB
            ob_id = f"ob_{candidate_idx}_{ob_direction.value.lower()}"
            if any(ob.id == ob_id for ob in order_blocks):
                continue

            order_blocks.append(
                OrderBlock(
                    id=ob_id,
                    bar_index=candidate_idx,
                    high=candidate_bar.high,
                    low=candidate_bar.low,
                    direction=ob_direction,
                    timestamp=candidate_bar.timestamp,
                    is_mitigated=False,
                )
            )

        return order_blocks
