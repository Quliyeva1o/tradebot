"""Displacement runs detection module."""

import pandas as pd


class DisplacementDetector:
    """Identifies highly energetic expansion runs (displacement) in price action."""

    def __init__(self, atr_multiplier: float = 2.0) -> None:
        """Initializes the DisplacementDetector.

        Args:
            atr_multiplier: Factor of Average True Range to denote expansion.
        """
        self.atr_multiplier = atr_multiplier

    def find_displacements(self, df: pd.DataFrame) -> pd.DataFrame:
        """Finds candles that exhibit displacement attributes.

        Args:
            df: Historical price candlestick DataFrame.

        Returns:
            A DataFrame with flagged displacement bars.
        """
        raise NotImplementedError(
            "Displacement calculation logic will be implemented in a future sprint."
        )
