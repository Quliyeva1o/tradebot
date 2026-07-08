"""Breaker Block detection module."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.models import Bar
from smc.order_block import OBDirection, OrderBlock


@dataclass(frozen=True)
class BreakerBlock:
    """Represents a Breaker Block zone (failed Order Block).

    Attributes:
        id: Unique identifier format breaker_{ob.id}.
        bar_index: Original bar index of the Order Block.
        high: High price boundary of the breaker block.
        low: Low price boundary of the breaker block.
        direction: Direction of the breaker block ("BULLISH" or "BEARISH").
        timestamp: The timestamp when the block was broken/validated as a breaker.
        source_ob_id: The ID of the original Order Block.
    """

    id: str
    bar_index: int
    high: float
    low: float
    direction: str  # "BULLISH" or "BEARISH"
    timestamp: datetime
    source_ob_id: str


class BreakerDetector:
    """Identifies Breaker Blocks (failed order blocks that broke structure)."""

    def __init__(self) -> None:
        """Initializes the BreakerDetector."""
        pass

    def detect_breakers(
        self, bars: Sequence[Bar], failed_obs: list[OrderBlock]
    ) -> list[BreakerBlock]:
        """Finds valid breaker block zones in price action.

        Args:
            bars: Sequence of Bar objects representing price history.
            failed_obs: List containing candidate/failed Order Blocks.

        Returns:
            A list of validated BreakerBlock objects.
        """
        breakers = []
        n_bars = len(bars)

        for ob in failed_obs:
            # Check only bars occurring after the Order Block was formed
            start_idx = ob.bar_index + 1
            if start_idx >= n_bars:
                continue

            for j in range(start_idx, n_bars):
                bar = bars[j]
                is_broken = False
                breaker_direction = ""

                if ob.direction == OBDirection.BULLISH:
                    # Bullish OB is broken if price closes below its low
                    if bar.close < ob.low:
                        is_broken = True
                        breaker_direction = "BEARISH"
                elif ob.direction == OBDirection.BEARISH:
                    # Bearish OB is broken if price closes above its high
                    if bar.close > ob.high:
                        is_broken = True
                        breaker_direction = "BULLISH"

                if is_broken:
                    breakers.append(
                        BreakerBlock(
                            id=f"breaker_{ob.id}",
                            bar_index=ob.bar_index,
                            high=ob.high,
                            low=ob.low,
                            direction=breaker_direction,
                            timestamp=bar.timestamp,
                            source_ob_id=ob.id,
                        )
                    )
                    # Once an OB is converted into a breaker, stop checking subsequent bars for it
                    break

        return breakers
