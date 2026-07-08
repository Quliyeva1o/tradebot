"""Adapter converting between pandas DataFrames and pure domain models for swing detection.

Allows research and backtesting scripts to interface with the domain-level SwingDetector.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from core.models import Bar
from market_structure.structure_models import SwingGraph
from market_structure.swing_detector import SwingDetector
from market_structure.swing_models import Swing, SwingClassification, SwingConfig, SwingType


def dataframe_to_bars(df: pd.DataFrame) -> list[Bar]:
    """Converts a pandas DataFrame to a list of domain Bar objects.

    Ensures columns and timestamps are validated.
    """
    required = ["time", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        from core.exceptions import MissingColumnError

        raise MissingColumnError(missing)

    # Validate dataframe size is not checked here but in the detector itself.
    # Check chronological ordering and uniqueness of timestamps
    if not df["time"].is_monotonic_increasing:
        from core.exceptions import InvalidTimestampError

        raise InvalidTimestampError("Price history timestamps must be sorted chronologically.")

    if df["time"].duplicated().any():
        from core.exceptions import DuplicateTimestampError

        raise DuplicateTimestampError("Duplicate timestamps detected in swing input.")

    bars = []
    for row in df.itertuples():
        row_any: Any = row
        bars.append(
            Bar(
                timestamp=pd.Timestamp(row_any.time).to_pydatetime(),
                open=float(row_any.open),
                high=float(row_any.high),
                low=float(row_any.low),
                close=float(row_any.close),
                volume=float(row_any.volume),
                spread=float(getattr(row_any, "spread", 0.0)),
            )
        )
    return bars


def adapt_swings_to_dataframe(df: pd.DataFrame, swings: Sequence[Swing]) -> pd.DataFrame:
    """Enriches a copy of the input DataFrame with columns detailing detected swings."""
    n = len(df)
    is_swing_high_arr = np.zeros(n, dtype=bool)
    is_swing_low_arr = np.zeros(n, dtype=bool)
    swing_price_arr = np.full(n, np.nan)
    swing_type_arr = np.full(n, SwingType.NONE.value, dtype=object)
    swing_strength_arr = np.full(n, "", dtype=object)
    swing_index_arr = np.full(n, np.nan)

    for swing in swings:
        idx = swing.index
        if 0 <= idx < n:
            if swing.type == SwingType.HIGH:
                is_swing_high_arr[idx] = True
                swing_type_arr[idx] = SwingType.HIGH.value
            elif swing.type == SwingType.LOW:
                is_swing_low_arr[idx] = True
                swing_type_arr[idx] = SwingType.LOW.value

            swing_price_arr[idx] = swing.price
            swing_strength_arr[idx] = swing.strength_category.value
            swing_index_arr[idx] = float(idx)

    result = df.copy()
    result["is_swing_high"] = is_swing_high_arr
    result["is_swing_low"] = is_swing_low_arr
    result["swing_price"] = swing_price_arr
    result["swing_type"] = swing_type_arr
    result["swing_strength"] = swing_strength_arr
    result["swing_index"] = swing_index_arr
    return result


class DataFrameSwingDetectorAdapter:
    """Adapter wrapping SwingDetector to support pandas DataFrame inputs and outputs."""

    def __init__(self, config: SwingConfig | None = None) -> None:
        self.detector = SwingDetector(config=config)
        self.raw_swings: list[Swing] = []
        self.filtered_swings: list[Swing] = []
        self.major_swings: list[Swing] = []
        self.minor_swings: list[Swing] = []
        self.swings: list[Swing] = []
        self.swing_graph: SwingGraph | None = None

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """Runs the domain detector and enriches the input DataFrame."""
        bars = dataframe_to_bars(df)
        self.swings = self.detector.detect_batch(bars)

        # In the simplified pure detector, all emitted swings are filtered/final swings
        self.raw_swings = self.swings
        self.filtered_swings = self.swings
        self.major_swings = [
            s for s in self.swings if s.classification == SwingClassification.MAJOR
        ]
        self.minor_swings = [
            s for s in self.swings if s.classification == SwingClassification.MINOR
        ]

        # Build passive SwingGraph
        self.swing_graph = SwingGraph()
        for swing in self.swings:
            self.swing_graph.add_swing(swing)

        return adapt_swings_to_dataframe(df, self.swings)

    def get_raw_swings(self) -> list[Swing]:
        return self.raw_swings

    def get_filtered_swings(self) -> list[Swing]:
        return self.filtered_swings

    def get_major_swings(self) -> list[Swing]:
        return self.major_swings

    def get_minor_swings(self) -> list[Swing]:
        return self.minor_swings

    def get_swings(self) -> list[Swing]:
        return self.swings

    def get_swing_graph(self) -> SwingGraph | None:
        return self.swing_graph
