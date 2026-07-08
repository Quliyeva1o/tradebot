"""Data models and parameters for the Swing Detection Engine."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class SwingType(Enum):
    """Supported local swing classifications."""

    NONE = "NONE"
    HIGH = "HIGH"
    LOW = "LOW"


class SwingStrength(Enum):
    """Strength indicators of detected swing pivots."""

    WEAK = "WEAK"
    NORMAL = "NORMAL"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"


class SwingClassification(Enum):
    """Classification of swings based on structural significance."""

    MAJOR = "MAJOR"
    MINOR = "MINOR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SwingConfig:
    """Configuration settings for swing identification.

    Attributes:
        left_bars: Number of confirmation bars required on the left.
        right_bars: Number of confirmation bars required on the right.
        minimum_distance_between_swings: Kept for backward compatibility. Same as minimum_bar_distance.
        allow_equal_highs: If True, adjacent bars matching the swing high price may both be flagged.
        allow_equal_lows: If True, adjacent bars matching the swing low price may both be flagged.
        minimum_bar_distance: Minimum index offset distance between successive swings of the same type.
        minimum_price_distance: Minimum price movement required between swings.
        merge_equal_highs: If True, adjacent or consecutive equal highs will be merged.
        merge_equal_lows: If True, adjacent or consecutive equal lows will be merged.
        merge_close_swings: If True, merges swings of the same type that are close in distance or price.
        classification_enabled: If True, enables Major/Minor swing classification.
        filter_enabled: If True, enables filtering of raw swings.
    """

    left_bars: int = 3
    right_bars: int = 3
    minimum_distance_between_swings: int = 1
    allow_equal_highs: bool = False
    allow_equal_lows: bool = False
    minimum_bar_distance: int = 1
    minimum_price_distance: float = 0.0
    merge_equal_highs: bool = False
    merge_equal_lows: bool = False
    merge_close_swings: bool = False
    classification_enabled: bool = True
    filter_enabled: bool = True

    def __post_init__(self) -> None:
        """Validate and synchronize swing configurations."""
        if self.left_bars <= 0 or self.right_bars <= 0:
            raise ValueError("left_bars and right_bars must be positive integers.")

        # Fallback handling to keep backward compatibility with minimum_distance_between_swings
        min_bar_dist = self.minimum_bar_distance
        if self.minimum_distance_between_swings != 1 and self.minimum_bar_distance == 1:
            min_bar_dist = self.minimum_distance_between_swings

        if min_bar_dist <= 0:
            raise ValueError(
                "minimum_bar_distance and minimum_distance_between_swings must be positive."
            )

        object.__setattr__(self, "minimum_bar_distance", min_bar_dist)
        object.__setattr__(self, "minimum_distance_between_swings", min_bar_dist)

        if self.minimum_price_distance < 0.0:
            raise ValueError("minimum_price_distance must be a non-negative float.")


@dataclass
class Swing:
    """Represents a single detected swing point.

    Attributes:
        id: Unique immutable identifier format: swing_{index}_{type}.
        timestamp: The timestamp of the swing candle.
        index: The index of the swing candle in the series.
        price: The high (for HIGH type) or low (for LOW type) price of the swing.
        type: SwingType (HIGH or LOW).
        classification: SwingClassification (MAJOR, MINOR, UNKNOWN).
        strength: Numerical rating representing the swing's strength.
        strength_category: SwingStrength enum representation (WEAK, NORMAL, STRONG, VERY_STRONG).
        previous_id: ID of the chronologically preceding swing in the graph.
        next_id: ID of the chronologically succeeding swing in the graph.
        bar_distance: Distance in bars to the chronologically preceding swing in the graph.
        price_distance: Price distance to the chronologically preceding swing in the graph.
    """

    id: str
    timestamp: datetime
    index: int
    price: float
    type: SwingType
    classification: SwingClassification = SwingClassification.UNKNOWN
    strength: float = 0.0
    strength_category: SwingStrength = SwingStrength.NORMAL
    previous_id: str | None = None
    next_id: str | None = None
    bar_distance: int | None = None
    price_distance: float | None = None

    def __setattr__(self, name: str, value: Any) -> None:
        """Sets attribute value checking for immutability constraints."""
        # Check if the attribute is already set (immutable fields check)
        immutable_fields = {"id", "timestamp", "index", "price", "type"}
        if name in immutable_fields and name in self.__dict__:
            raise AttributeError(f"Field '{name}' is immutable after Swing initialization.")
        super().__setattr__(name, value)
