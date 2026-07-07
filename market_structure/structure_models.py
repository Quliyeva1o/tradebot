"""Data models and configurations for the Market Structure Engine."""

from dataclasses import dataclass, field
from enum import Enum
import pandas as pd

from market_structure.swing_models import Swing


class StructureTrend(Enum):
    """Supported market structure trend states."""
    UNKNOWN = "UNKNOWN"
    RANGE = "RANGE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    TRANSITION = "TRANSITION"


class SwingRelationship(Enum):
    """Relationships between consecutive swings."""
    HH = "HH"  # Higher High
    HL = "HL"  # Higher Low
    LH = "LH"  # Lower High
    LL = "LL"  # Lower Low
    EH = "EH"  # Equal High
    EL = "EL"  # Equal Low
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StructureConfig:
    """Configuration settings for structural trend identification.

    Attributes:
        minimum_confirmations: Consecutive trend-aligned updates required for full confidence.
        equal_high_tolerance: Absolute price offset within which highs are considered equal.
        equal_low_tolerance: Absolute price offset within which lows are considered equal.
        major_only: If True, only MAJOR swings are processed; MINOR swings are ignored.
        track_internal_structure: If True, tracks internal (minor) swings within major swing ranges.
        track_external_structure: If True, tracks external major swing ranges.
    """
    minimum_confirmations: int = 2
    equal_high_tolerance: float = 0.0001
    equal_low_tolerance: float = 0.0001
    major_only: bool = False
    track_internal_structure: bool = True
    track_external_structure: bool = True

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        if self.minimum_confirmations <= 0:
            raise ValueError("minimum_confirmations must be a positive integer.")
        if self.equal_high_tolerance < 0.0 or self.equal_low_tolerance < 0.0:
            raise ValueError("Tolerances must be non-negative floats.")


@dataclass(frozen=True)
class MarketStructure:
    """Represents the market structure state at a specific confirmed swing update.

    Attributes:
        structure_id: Unique identifier for this state update.
        timestamp: Timestamp of the confirming swing.
        trend: Current structural trend (UNKNOWN, RANGE, BULLISH, BEARISH, TRANSITION).
        last_major_high: Most recent confirmed MAJOR swing high.
        last_major_low: Most recent confirmed MAJOR swing low.
        last_minor_high: Most recent confirmed MINOR swing high.
        last_minor_low: Most recent confirmed MINOR swing low.
        current_hh: The swing forming the current Higher High (if applicable).
        current_hl: The swing forming the current Higher Low (if applicable).
        current_lh: The swing forming the current Lower High (if applicable).
        current_ll: The swing forming the current Lower Low (if applicable).
        internal_structure: Metadata dictionary tracking internal swing patterns.
        external_structure: Metadata dictionary tracking external swing boundaries.
        confidence: Numerical confidence rating (0.0 to 1.0) of the detected trend.
        sequence_number: Sequential update index (0-indexed).
    """
    structure_id: str
    timestamp: pd.Timestamp
    trend: StructureTrend
    last_major_high: Swing | None = None
    last_major_low: Swing | None = None
    last_minor_high: Swing | None = None
    last_minor_low: Swing | None = None
    current_hh: Swing | None = None
    current_hl: Swing | None = None
    current_lh: Swing | None = None
    current_ll: Swing | None = None
    internal_structure: dict = field(default_factory=dict)
    external_structure: dict = field(default_factory=dict)
    confidence: float = 0.0
    sequence_number: int = 0
