"""Displacement runs detection module."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.models import Bar


@dataclass(frozen=True)
class DisplacementBar:
    """Represents a candle exhibiting displacement (impulsive expansion).

    Attributes:
        bar_index: The index of the displacement candle.
        timestamp: The timestamp of the candle.
        direction: Direction of the candle ("BULLISH", "BEARISH", or "NEUTRAL").
        range: The total price range of the candle (high - low).
        atr: The ATR value at the time of the candle.
    """

    bar_index: int
    timestamp: datetime
    direction: str
    range: float
    atr: float


class DisplacementDetector:
    """Identifies highly energetic expansion runs (displacement) in price action."""

    def __init__(self, atr_multiplier: float = 2.0, atr_period: int = 14) -> None:
        """Initializes the DisplacementDetector.

        Args:
            atr_multiplier: Factor of Average True Range to denote expansion.
            atr_period: Average True Range period window size.
        """
        self.atr_multiplier = atr_multiplier
        self.atr_period = atr_period

    def _calculate_tr_and_atr(self, bars: Sequence[Bar]) -> tuple[list[float], list[float]]:
        """Calculates True Range (TR) and Wilder's smoothed ATR for a Sequence of Bars."""
        n = len(bars)
        if n == 0:
            return [], []

        tr_values = []
        for idx, bar in enumerate(bars):
            if idx == 0:
                tr_values.append(bar.high - bar.low)
            else:
                prev_close = bars[idx - 1].close
                tr1 = bar.high - bar.low
                tr2 = abs(bar.high - prev_close)
                tr3 = abs(bar.low - prev_close)
                tr_values.append(max(tr1, tr2, tr3))

        atr_values = [0.0] * n
        if n <= self.atr_period:
            # Fallback if too few bars
            avg = sum(tr_values) / n if n > 0 else 0.0
            atr_values = [avg] * n
        else:
            # Initial ATR is simple average of first 'atr_period' elements
            first_avg = sum(tr_values[: self.atr_period]) / self.atr_period
            for i in range(self.atr_period):
                atr_values[i] = first_avg

            current_atr = first_avg
            for i in range(self.atr_period, n):
                current_atr = (current_atr * (self.atr_period - 1) + tr_values[i]) / self.atr_period
                atr_values[i] = current_atr

        return tr_values, atr_values

    def find_displacements(self, bars: Sequence[Bar]) -> list[DisplacementBar]:
        """Finds candles that exhibit displacement attributes.

        Args:
            bars: Sequence of historical price candlestick Bar objects.

        Returns:
            A list of DisplacementBar objects detailing the expansion points.
        """
        displacements: list[DisplacementBar] = []
        n = len(bars)
        if n < self.atr_period + 1:
            return displacements

        tr_values, atr_values = self._calculate_tr_and_atr(bars)

        # Scan bars from the end of the ATR startup period
        for i in range(self.atr_period, n):
            bar = bars[i]
            tr = tr_values[i]
            atr = atr_values[i]

            # Displacement check:
            # 1. Total range (or True Range) is greater than atr_multiplier * ATR
            # 2. Candle body is a significant portion of its total range (e.g. >= 50%)
            #    to ensure it is an actual expansion rather than a high-volatility pin bar.
            candle_range = bar.high - bar.low
            if candle_range > 0 and tr >= self.atr_multiplier * atr:
                body = abs(bar.close - bar.open)
                if body >= 0.5 * candle_range:
                    if bar.close > bar.open:
                        direction = "BULLISH"
                    elif bar.close < bar.open:
                        direction = "BEARISH"
                    else:
                        direction = "NEUTRAL"

                    displacements.append(
                        DisplacementBar(
                            bar_index=i,
                            timestamp=bar.timestamp,
                            direction=direction,
                            range=candle_range,
                            atr=atr,
                        )
                    )

        return displacements
