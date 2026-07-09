"""Fair Value Gap (FVG) detection module."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.models import Bar


class FVGDirection(Enum):
    """Direction of the Fair Value Gap."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class FairValueGap:
    """Represents a detected Fair Value Gap zone.

    Attributes:
        id: Unique identifier.
        start_index: Index of the first bar in the FVG sequence.
        end_index: Index of the third bar in the FVG sequence.
        upper_price: High price boundary of the FVG.
        lower_price: Low price boundary of the FVG.
        direction: Direction of FVG (BULLISH/BEARISH).
        timestamp: Timestamp of the second (imbalance) candle.
        is_mitigated: True once price has fully closed through the FVG and
            past its far edge, invalidating it. A close that merely enters
            or retests the zone (the intended entry trigger) does NOT set
            this -- see MitigationMonitor.check_mitigation (Bug #22).
    """

    id: str
    start_index: int
    end_index: int
    upper_price: float
    lower_price: float
    direction: FVGDirection
    timestamp: datetime
    is_mitigated: bool = False


class FVGDetector:
    """Identifies Fair Value Gaps (FVG) and checks for fill (mitigation) metrics."""

    def __init__(self, min_gap_pips: float = 1.0, pip_size: float = 0.0001) -> None:
        """Initializes the FVGDetector.

        Args:
            min_gap_pips: Minimum required pip size of the imbalance gap.
            pip_size: The price value of a single pip (e.g. 0.0001).
        """
        self.min_gap_pips = min_gap_pips
        self.pip_size = pip_size

    def detect_fvgs(self, bars: Sequence[Bar]) -> list[FairValueGap]:
        """Finds open imbalances (FVGs) in three-candle sequences.

        Args:
            bars: Sequence of historical price candlestick Bar objects.

        Returns:
            A list of detected FairValueGap objects.
        """
        fvgs: list[FairValueGap] = []
        n = len(bars)
        if n < 3:
            return fvgs

        min_gap = self.min_gap_pips * self.pip_size

        for i in range(1, n - 1):
            bar0 = bars[i - 1]
            bar1 = bars[i]
            bar2 = bars[i + 1]

            # Bullish FVG Check
            if bar2.low - bar0.high >= min_gap:
                fvgs.append(
                    FairValueGap(
                        id=f"fvg_{i - 1}_bullish",
                        start_index=i - 1,
                        end_index=i + 1,
                        upper_price=bar2.low,
                        lower_price=bar0.high,
                        direction=FVGDirection.BULLISH,
                        timestamp=bar1.timestamp,
                        is_mitigated=False,
                    )
                )

            # Bearish FVG Check
            elif bar0.low - bar2.high >= min_gap:
                fvgs.append(
                    FairValueGap(
                        id=f"fvg_{i - 1}_bearish",
                        start_index=i - 1,
                        end_index=i + 1,
                        upper_price=bar0.low,
                        lower_price=bar2.high,
                        direction=FVGDirection.BEARISH,
                        timestamp=bar1.timestamp,
                        is_mitigated=False,
                    )
                )

        return fvgs
