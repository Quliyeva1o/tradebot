"""Data models and configurations for the Market Structure Engine."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto

from core.models import Bar, Timeframe
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
    timestamp: datetime
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


class BreakType(Enum):
    """Types of market structure breaks."""
    BOS = "BOS"
    CHoCH = "CHoCH"


@dataclass(frozen=True)
class StructureBreak:
    """Represents a validated structure break (BOS or CHoCH)."""
    break_id: str
    break_type: BreakType
    broken_swing: Swing
    breaking_bar: Bar
    timestamp: datetime


@dataclass
class StructureState:
    """Active structural trend configuration and historical breaks tracker."""
    trend: StructureTrend = StructureTrend.UNKNOWN
    confidence: float = 0.0
    active_major_high: Swing | None = None
    active_major_low: Swing | None = None
    breaks_history: list[StructureBreak] = field(default_factory=list)


@dataclass
class SwingGraph:
    """Direct, queryable network of swing high and low pivots."""
    _nodes: list[Swing] = field(default_factory=list, repr=False)
    edges: dict[str, list[str]] = field(default_factory=dict)

    @property
    def nodes(self) -> list[Swing]:
        """Exposes a read-only copy of the swing graph nodes."""
        return list(self._nodes)

    def add_swing(self, swing: Swing) -> None:
        """Adds a swing node to the graph."""
        self._nodes.append(swing)
        if swing.previous_id:
            self.edges.setdefault(swing.previous_id, []).append(swing.id)

    def get_latest_high(self) -> Swing | None:
        """Returns the most recent confirmed swing high."""
        highs = [s for s in self._nodes if s.type.name == "HIGH"]
        return highs[-1] if highs else None

    def get_latest_low(self) -> Swing | None:
        """Returns the most recent confirmed swing low."""
        lows = [s for s in self._nodes if s.type.name == "LOW"]
        return lows[-1] if lows else None

    def find_equal_highs(self, tolerance: float = 0.0001) -> list[Swing]:
        """Finds groups of swing highs close in price (potential double/triple highs)."""
        highs = [s for s in self._nodes if s.type.name == "HIGH"]
        equal_highs = []
        for i, s1 in enumerate(highs):
            for s2 in highs[i + 1:]:
                if abs(s1.price - s2.price) <= tolerance:
                    if s1 not in equal_highs:
                        equal_highs.append(s1)
                    if s2 not in equal_highs:
                        equal_highs.append(s2)
        return equal_highs

    def find_equal_lows(self, tolerance: float = 0.0001) -> list[Swing]:
        """Finds groups of swing lows close in price (potential double/triple lows)."""
        lows = [s for s in self._nodes if s.type.name == "LOW"]
        equal_lows = []
        for i, s1 in enumerate(lows):
            for s2 in lows[i + 1:]:
                if abs(s1.price - s2.price) <= tolerance:
                    if s1 not in equal_lows:
                        equal_lows.append(s1)
                    if s2 not in equal_lows:
                        equal_lows.append(s2)
        return equal_lows


@dataclass
class MarketState:
    """Domain aggregate root container representing instrument market timeline state."""
    symbol: str
    timeframe: Timeframe
    _bars: list[Bar] = field(default_factory=list, repr=False)
    swing_graph: SwingGraph = field(default_factory=SwingGraph)
    structure_state: StructureState = field(default_factory=StructureState)

    @property
    def bars(self) -> list[Bar]:
        """Exposes a read-only copy of the price bars."""
        return list(self._bars)

    def append_bar(self, bar: Bar) -> None:
        """Appends a new bar to the timeframe timeline."""
        self._bars.append(bar)

    def get_latest_bar(self) -> Bar | None:
        """Returns the most recently closed bar."""
        return self._bars[-1] if self._bars else None
