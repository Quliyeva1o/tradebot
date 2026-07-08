"""Market Structure Engine module.

Implements a deterministic state machine to track structural trend states,
consecutive swing relationships, and confidence intervals.
"""

from abc import ABC, abstractmethod

from core.models import Bar
from market_structure.structure_models import (
    BreakType,
    MarketStructure,
    StructureBreak,
    StructureConfig,
    StructureState,
    StructureTrend,
    SwingRelationship,
)
from market_structure.swing_models import Swing, SwingClassification, SwingType

# --- Custom Exceptions ---


class StructureException(Exception):
    """Base exception for Market Structure Engine errors."""

    pass


class InvalidSwingSequenceError(StructureException):
    """Raised when swings are out of chronological order."""

    pass


class DuplicateSwingIDError(StructureException):
    """Raised when duplicate swing IDs are processed."""

    pass


class BrokenGraphError(StructureException):
    """Raised when the input Swing sequence contains incorrect graph links."""

    pass


# --- Confidence Calculators ---


class ConfidenceCalculator(ABC):
    """Interface for extensible market structure confidence calculations."""

    @abstractmethod
    def calculate(
        self,
        trend: StructureTrend,
        confirmations: int,
        last_swing: Swing,
        config: StructureConfig,
    ) -> float:
        """Calculates trend confidence from 0.0 to 1.0."""
        pass


class DefaultConfidenceCalculator(ConfidenceCalculator):
    """Default confidence rating based on confirmation count, swing strength, and classification."""

    def calculate(
        self,
        trend: StructureTrend,
        confirmations: int,
        last_swing: Swing,
        config: StructureConfig,
    ) -> float:
        if trend == StructureTrend.UNKNOWN:
            return 0.0

        # 1. Confirmation Count Factor
        base_conf = min(1.0, confirmations / max(1, config.minimum_confirmations))

        # 2. Swing Classification Factor (Major swings are higher confidence)
        class_factor = 1.0
        if last_swing.classification == SwingClassification.MINOR:
            class_factor = 0.75

        # 3. Strength volatility multiplier (capped at 1.0)
        strength_factor = 0.8 + 0.2 * min(1.0, getattr(last_swing, "strength", 1.0) / 2.0)

        confidence = base_conf * class_factor * strength_factor
        return float(round(max(0.0, min(1.0, confidence)), 4))


# --- Market Structure Engine ---


class MarketStructureEngine:
    """Deterministic State Machine tracking market structure and trends from swing data."""

    def __init__(self, config: StructureConfig | None = None) -> None:
        """Initializes the MarketStructureEngine.

        Args:
            config: Configurations for trend detection and tolerances.
        """
        self.config = config or StructureConfig()
        self.confidence_calculator = DefaultConfidenceCalculator()
        self.reset()

    def reset(self) -> None:
        """Resets the engine's internal state history."""
        self.history: list[MarketStructure] = []
        self.processed_ids: set[str] = set()
        self.last_index = -1

        # Current state pointers
        self.current_trend = StructureTrend.UNKNOWN
        self.confirmations_count = 0

        # Swings history trackers
        self.highs_history: list[Swing] = []
        self.lows_history: list[Swing] = []

        # Current active classification targets
        self.last_major_high: Swing | None = None
        self.last_major_low: Swing | None = None
        self.last_minor_high: Swing | None = None
        self.last_minor_low: Swing | None = None

        # Active relationship swing levels (carried over state pointers)
        self.current_hh: Swing | None = None
        self.current_hl: Swing | None = None
        self.current_lh: Swing | None = None
        self.current_ll: Swing | None = None

        # Relationship memory
        self.last_high_relationship = SwingRelationship.UNKNOWN
        self.last_low_relationship = SwingRelationship.UNKNOWN

        # Structure break history and tracking
        self.breaks_history: list[StructureBreak] = []
        self.last_broken_high_id: str | None = None
        self.last_broken_low_id: str | None = None

    def _validate_single(self, swing: Swing) -> None:
        """Validates incoming swing sequence chronologically and checks for duplicate IDs."""
        if swing.id in self.processed_ids:
            raise DuplicateSwingIDError(f"Duplicate swing ID detected: {swing.id}")

        if swing.index <= self.last_index:
            raise InvalidSwingSequenceError(
                f"Swing index {swing.index} violates chronological order. Last index: {self.last_index}"
            )

    def update(self, swing: Swing) -> MarketStructure:
        """Incrementally updates the state machine with a new confirmed swing.

        Note:
            This method is event-driven and must be called only when a new swing is confirmed.
            In contrast, `check_structural_break` is bar-driven and must be called for every
            newly closed bar. These two methods are called independently and at different frequencies.

        Args:
            swing: The new confirmed Swing object.

        Returns:
            The newly computed MarketStructure state.
        """
        self._validate_single(swing)

        # Skip minor swings if configuration mandates major-only tracking
        if self.config.major_only and swing.classification == SwingClassification.MINOR:
            return self.get_current_structure() or self._create_empty_state(swing)

        # 1. Update pointers based on swing details
        if swing.classification == SwingClassification.MAJOR:
            if swing.type == SwingType.HIGH:
                self.last_major_high = swing
                self.last_broken_high_id = None
            elif swing.type == SwingType.LOW:
                self.last_major_low = swing
                self.last_broken_low_id = None
        elif swing.classification == SwingClassification.MINOR:
            if swing.type == SwingType.HIGH:
                self.last_minor_high = swing
            elif swing.type == SwingType.LOW:
                self.last_minor_low = swing

        # 2. Evaluate swing relationships (HH, LH, EH, LL, HL, EL)
        if swing.type == SwingType.HIGH:
            if self.highs_history:
                prev_high = self.highs_history[-1]
                diff = swing.price - prev_high.price
                if diff > self.config.equal_high_tolerance:
                    self.last_high_relationship = SwingRelationship.HH
                    self.current_hh = swing
                    self.current_lh = None
                elif diff < -self.config.equal_high_tolerance:
                    self.last_high_relationship = SwingRelationship.LH
                    self.current_lh = swing
                    self.current_hh = None
                else:
                    self.last_high_relationship = SwingRelationship.EH
            self.highs_history.append(swing)

        elif swing.type == SwingType.LOW:
            if self.lows_history:
                prev_low = self.lows_history[-1]
                diff = swing.price - prev_low.price
                if diff < -self.config.equal_low_tolerance:
                    self.last_low_relationship = SwingRelationship.LL
                    self.current_ll = swing
                    self.current_hl = None
                elif diff > self.config.equal_low_tolerance:
                    self.last_low_relationship = SwingRelationship.HL
                    self.current_hl = swing
                    self.current_ll = None
                else:
                    self.last_low_relationship = SwingRelationship.EL
            self.lows_history.append(swing)

        # 3. Deterministic State Machine Transitions
        new_trend = self.current_trend

        # We need at least one high and one low to begin detecting trends
        if not self.highs_history or not self.lows_history:
            new_trend = StructureTrend.UNKNOWN
        else:
            h_rel = self.last_high_relationship
            l_rel = self.last_low_relationship

            # Transition logic prioritizing structural shifts first
            if self.current_trend == StructureTrend.BULLISH:
                if h_rel == SwingRelationship.LH or l_rel == SwingRelationship.LL:
                    new_trend = StructureTrend.TRANSITION
                elif h_rel == SwingRelationship.HH and l_rel == SwingRelationship.HL:
                    new_trend = StructureTrend.BULLISH
                else:
                    new_trend = StructureTrend.RANGE
            elif self.current_trend == StructureTrend.BEARISH:
                if h_rel == SwingRelationship.HH or l_rel == SwingRelationship.HL:
                    new_trend = StructureTrend.TRANSITION
                elif h_rel == SwingRelationship.LH and l_rel == SwingRelationship.LL:
                    new_trend = StructureTrend.BEARISH
                else:
                    new_trend = StructureTrend.RANGE
            elif self.current_trend == StructureTrend.TRANSITION:
                if h_rel == SwingRelationship.LH and l_rel == SwingRelationship.LL:
                    new_trend = StructureTrend.BEARISH
                elif h_rel == SwingRelationship.HH and l_rel == SwingRelationship.HL:
                    new_trend = StructureTrend.BULLISH
                else:
                    new_trend = StructureTrend.RANGE
            else:  # UNKNOWN or RANGE
                if h_rel == SwingRelationship.HH and l_rel == SwingRelationship.HL:
                    new_trend = StructureTrend.BULLISH
                elif h_rel == SwingRelationship.LH and l_rel == SwingRelationship.LL:
                    new_trend = StructureTrend.BEARISH
                else:
                    new_trend = StructureTrend.RANGE

        # Update confirmation counters
        if new_trend != StructureTrend.UNKNOWN:
            if new_trend == self.current_trend:
                self.confirmations_count += 1
            else:
                self.confirmations_count = 1
        else:
            self.confirmations_count = 0

        self.current_trend = new_trend

        # 4. Handle internal/external structures
        internal_struct = {}
        external_struct = {}

        if self.config.track_external_structure and self.last_major_high and self.last_major_low:
            range_high = self.last_major_high.price
            range_low = self.last_major_low.price
            is_outside = swing.price > range_high or swing.price < range_low
            external_struct = {
                "range_high": range_high,
                "range_low": range_low,
                "is_outside": is_outside,
            }

        if self.config.track_internal_structure and self.last_major_high and self.last_major_low:
            range_high = self.last_major_high.price
            range_low = self.last_major_low.price
            is_inside = range_low <= swing.price <= range_high
            internal_struct = {
                "is_inside": is_inside,
                "last_internal_high": self.last_minor_high.price if self.last_minor_high else None,
                "last_internal_low": self.last_minor_low.price if self.last_minor_low else None,
            }

        # 5. Calculate Confidence
        conf = self.confidence_calculator.calculate(
            self.current_trend, self.confirmations_count, swing, self.config
        )

        # 6. Formulate new MarketStructure entry
        seq_num = len(self.history)
        struct_entry = MarketStructure(
            structure_id=f"struct_state_{seq_num}_{swing.id}",
            timestamp=swing.timestamp,
            trend=self.current_trend,
            last_major_high=self.last_major_high,
            last_major_low=self.last_major_low,
            last_minor_high=self.last_minor_high,
            last_minor_low=self.last_minor_low,
            current_hh=self.current_hh,
            current_hl=self.current_hl,
            current_lh=self.current_lh,
            current_ll=self.current_ll,
            internal_structure=internal_struct,
            external_structure=external_struct,
            confidence=conf,
            sequence_number=seq_num,
        )

        self.history.append(struct_entry)
        self.processed_ids.add(swing.id)
        self.last_index = swing.index
        return struct_entry

    def _create_empty_state(self, swing: Swing) -> MarketStructure:
        """Utility to construct an empty placeholder MarketStructure state."""
        return MarketStructure(
            structure_id=f"struct_state_empty_{swing.id}",
            timestamp=swing.timestamp,
            trend=StructureTrend.UNKNOWN,
            sequence_number=len(self.history),
        )

    def analyze(self, swings: list[Swing]) -> list[MarketStructure]:
        """Processes a full sequence of swings chronologically.

        Args:
            swings: List of confirmed swings.

        Returns:
            The complete history list of MarketStructure states.
        """
        # Validate graph links and chronology before analyzing
        self._validate_graph(swings)

        for swing in swings:
            self.update(swing)
        return self.history

    def _validate_graph(self, swings: list[Swing]) -> None:
        """Verifies sequence chronology and validation links in the Swing list."""
        if not swings:
            return

        last_idx = -1
        ids = set()

        for swing in swings:
            if swing.id in ids:
                raise DuplicateSwingIDError(f"Duplicate swing ID detected: {swing.id}")
            ids.add(swing.id)

            if swing.index <= last_idx:
                raise InvalidSwingSequenceError(
                    f"Swings out of chronological order. index {swing.index} <= {last_idx}"
                )
            last_idx = swing.index

        # Validate doubly linked list connections match index locations
        for i, swing in enumerate(swings):
            if i > 0:
                expected_prev = swings[i - 1].id
                if swing.previous_id != expected_prev:
                    raise BrokenGraphError(
                        f"Swing graph link broken. {swing.id} previous_id = {swing.previous_id}, expected = {expected_prev}"
                    )
            else:
                if swing.previous_id is not None:
                    raise BrokenGraphError(f"First swing {swing.id} cannot have previous_id set.")

            if i < len(swings) - 1:
                expected_next = swings[i + 1].id
                if swing.next_id != expected_next:
                    raise BrokenGraphError(
                        f"Swing graph link broken. {swing.id} next_id = {swing.next_id}, expected = {expected_next}"
                    )
            else:
                if swing.next_id is not None:
                    raise BrokenGraphError(f"Last swing {swing.id} cannot have next_id set.")

    def get_current_structure(self) -> MarketStructure | None:
        """Returns the most recently computed market structure state."""
        return self.history[-1] if self.history else None

    def get_history(self) -> list[MarketStructure]:
        """Returns the history list of all processed market structure state updates."""
        return self.history

    def check_structural_break(self, bar: Bar) -> StructureBreak | None:
        """Checks if the given bar closes past the last major swing levels, creating a structure break.

        Note:
            This method is bar-driven and must be called for every newly closed bar.
            In contrast, `update` is event-driven and must be called only when a new swing
            is confirmed. These two methods are called independently and at different frequencies.

        Args:
            bar: The bar to check for a break.

        Returns:
            A new StructureBreak if detected, else None.
        """
        if not self.last_major_high or not self.last_major_low:
            return None

        # Bullish break
        if (
            bar.close > self.last_major_high.price
            and self.last_major_high.id != self.last_broken_high_id
        ):
            break_type = (
                BreakType.CHoCH if self.current_trend == StructureTrend.BEARISH else BreakType.BOS
            )
            self.last_broken_high_id = self.last_major_high.id
            brk = StructureBreak(
                break_id=f"break_{len(self.breaks_history)}_{bar.timestamp.isoformat()}",
                break_type=break_type,
                broken_swing=self.last_major_high,
                breaking_bar=bar,
                timestamp=bar.timestamp,
            )
            self.breaks_history.append(brk)
            return brk

        # Bearish break
        if (
            bar.close < self.last_major_low.price
            and self.last_major_low.id != self.last_broken_low_id
        ):
            break_type = (
                BreakType.CHoCH if self.current_trend == StructureTrend.BULLISH else BreakType.BOS
            )
            self.last_broken_low_id = self.last_major_low.id
            brk = StructureBreak(
                break_id=f"break_{len(self.breaks_history)}_{bar.timestamp.isoformat()}",
                break_type=break_type,
                broken_swing=self.last_major_low,
                breaking_bar=bar,
                timestamp=bar.timestamp,
            )
            self.breaks_history.append(brk)
            return brk

        return None

    def get_structure_state(self) -> StructureState:
        """Returns the current StructureState containing active swing levels and breaks.

        Returns:
            A StructureState domain object representing the current structural configuration.
        """
        confidence = self.history[-1].confidence if self.history else 0.0
        return StructureState(
            trend=self.current_trend,
            confidence=confidence,
            active_major_high=self.last_major_high,
            active_major_low=self.last_major_low,
            breaks_history=list(self.breaks_history),
        )

    def handle_upgrade(self, swing: Swing) -> None:
        """Handles the upgrade of an existing swing to MAJOR classification."""
        if swing.classification == SwingClassification.MAJOR:
            if swing.type == SwingType.HIGH:
                self.last_major_high = swing
                self.last_broken_high_id = None
            elif swing.type == SwingType.LOW:
                self.last_major_low = swing
                self.last_broken_low_id = None
