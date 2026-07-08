"""Swing Detection Engine module.

Implements high-performance, non-repainting, Pandas-free swing high and swing low pivot detectors.
"""

from collections.abc import Sequence

from core.exceptions import DataValidationError, DuplicateTimestampError, InvalidTimestampError
from core.models import Bar
from market_structure.structure_models import SwingGraph
from market_structure.swing_models import (
    Swing,
    SwingClassification,
    SwingConfig,
    SwingStrength,
    SwingType,
)


class SwingDetector:
    """Stateless engine to detect swing high and swing low pivot levels in price data.

    This class has been refactored to be pure domain logic, completely independent of Pandas/NumPy.
    """

    def __init__(self, config: SwingConfig | None = None) -> None:
        """Initializes the SwingDetector.

        Args:
            config: Configurations for window sizes and equal level boundaries.
        """
        self.config = config or SwingConfig()

    def _validate(self, bars: Sequence[Bar]) -> None:
        """Ensures that the input sequence meets structural requirements."""
        min_required_len = self.config.left_bars + self.config.right_bars + 1
        if len(bars) < min_required_len:
            raise DataValidationError(
                f"Dataset too small. Requires at least {min_required_len} rows for detection."
            )

        last_time = None
        seen_times = set()
        for bar in bars:
            t = bar.timestamp
            if last_time is not None and t < last_time:
                raise InvalidTimestampError(
                    "Price history timestamps must be sorted chronologically."
                )
            if t in seen_times:
                raise DuplicateTimestampError("Duplicate timestamps detected in swing input.")
            seen_times.add(t)
            last_time = t

    def detect_batch(self, bars: Sequence[Bar]) -> list[Swing]:
        """Scans the entire historical sequence of bars and returns all confirmed swings.

        Args:
            bars: List of candlestick bars to process.

        Returns:
            A chronologically sorted list of confirmed Swing objects.
        """
        self._validate(bars)

        left = self.config.left_bars
        right = self.config.right_bars
        n = len(bars)

        raw_candidates: list[Swing] = []

        # 1. Candidate Detection
        for i in range(left, n - right):
            bar = bars[i]

            # Swing High Check
            is_high = True
            for j in range(1, left + 1):
                prev_high = bars[i - j].high
                if self.config.allow_equal_highs:
                    if bar.high < prev_high:
                        is_high = False
                        break
                else:
                    if bar.high <= prev_high:
                        is_high = False
                        break

            if is_high:
                for k in range(1, right + 1):
                    next_high = bars[i + k].high
                    if self.config.allow_equal_highs:
                        if bar.high < next_high:
                            is_high = False
                            break
                    else:
                        if bar.high <= next_high:
                            is_high = False
                            break

            if is_high:
                raw_candidates.append(
                    Swing(
                        id=f"swing_{i}_high",
                        timestamp=bar.timestamp,
                        index=i,
                        price=bar.high,
                        type=SwingType.HIGH,
                    )
                )

            # Swing Low Check
            is_low = True
            for j in range(1, left + 1):
                prev_low = bars[i - j].low
                if self.config.allow_equal_lows:
                    if bar.low > prev_low:
                        is_low = False
                        break
                else:
                    if bar.low >= prev_low:
                        is_low = False
                        break

            if is_low:
                for k in range(1, right + 1):
                    next_low = bars[i + k].low
                    if self.config.allow_equal_lows:
                        if bar.low > next_low:
                            is_low = False
                            break
                    else:
                        if bar.low >= next_low:
                            is_low = False
                            break

            if is_low:
                raw_candidates.append(
                    Swing(
                        id=f"swing_{i}_low",
                        timestamp=bar.timestamp,
                        index=i,
                        price=bar.low,
                        type=SwingType.LOW,
                    )
                )

        # 2. Filter Swings (Alternate and spacing filters)
        filtered_swings = self._apply_filtering(raw_candidates, bars)

        # 3. Classify Swings (MAJOR vs. MINOR)
        if self.config.classification_enabled:
            for swing in filtered_swings:
                swing.classification = self._classify_swing(swing, bars)
        else:
            for swing in filtered_swings:
                swing.classification = SwingClassification.UNKNOWN

        # 4. Calculate Strength & Categories
        for swing in filtered_swings:
            swing.strength = self._calculate_strength(swing, bars)
            swing.strength_category = self._map_strength_category(swing.strength)

        # 5. Build Graph Links & Distances
        if filtered_swings:
            for idx, swing in enumerate(filtered_swings):
                if idx > 0:
                    prev = filtered_swings[idx - 1]
                    swing.previous_id = prev.id
                    swing.bar_distance = swing.index - prev.index
                    swing.price_distance = float(swing.price - prev.price)
                else:
                    swing.previous_id = None
                    swing.bar_distance = None
                    swing.price_distance = None

                if idx < len(filtered_swings) - 1:
                    nxt = filtered_swings[idx + 1]
                    swing.next_id = nxt.id
                else:
                    swing.next_id = None

        return filtered_swings

    def detect_incremental(self, bars: Sequence[Bar], graph: SwingGraph) -> Swing | None:
        """Processes the latest completed candle and returns a Swing if confirmed at T - R.

        Args:
            bars: The full candlestick timeline sequence.
            graph: Passive SwingGraph storing all historical swings.

        Returns:
            A new Swing object if one is confirmed, else None.
        """
        left = self.config.left_bars
        right = self.config.right_bars
        n = len(bars)

        # Candidate check index is T - right
        t = n - 1
        candidate_idx = t - right
        if candidate_idx < left:
            return None

        bar = bars[candidate_idx]

        # Check candidate Swing High
        is_high = True
        for j in range(1, left + 1):
            prev_high = bars[candidate_idx - j].high
            if self.config.allow_equal_highs:
                if bar.high < prev_high:
                    is_high = False
                    break
            else:
                if bar.high <= prev_high:
                    is_high = False
                    break

        if is_high:
            for k in range(1, right + 1):
                next_high = bars[candidate_idx + k].high
                if self.config.allow_equal_highs:
                    if bar.high < next_high:
                        is_high = False
                        break
                else:
                    if bar.high <= next_high:
                        is_high = False
                        break

        # Check candidate Swing Low
        is_low = True
        for j in range(1, left + 1):
            prev_low = bars[candidate_idx - j].low
            if self.config.allow_equal_lows:
                if bar.low > prev_low:
                    is_low = False
                    break
            else:
                if bar.low >= prev_low:
                    is_low = False
                    break

        if is_low:
            for k in range(1, right + 1):
                next_low = bars[candidate_idx + k].low
                if self.config.allow_equal_lows:
                    if bar.low > next_low:
                        is_low = False
                        break
                else:
                    if bar.low >= next_low:
                        is_low = False
                        break

        candidate = None
        if is_high:
            candidate = Swing(
                id=f"swing_{candidate_idx}_high",
                timestamp=bar.timestamp,
                index=candidate_idx,
                price=bar.high,
                type=SwingType.HIGH,
            )
        elif is_low:
            candidate = Swing(
                id=f"swing_{candidate_idx}_low",
                timestamp=bar.timestamp,
                index=candidate_idx,
                price=bar.low,
                type=SwingType.LOW,
            )

        if not candidate:
            return None

        # Filter check against graph
        if self.config.filter_enabled:
            # Min Bar Distance filter
            last_same_type = (
                graph.get_latest_high()
                if candidate.type == SwingType.HIGH
                else graph.get_latest_low()
            )
            if (
                last_same_type
                and (candidate.index - last_same_type.index) < self.config.minimum_bar_distance
            ):
                # Keep the extreme one
                better = (
                    (candidate.price > last_same_type.price)
                    if candidate.type == SwingType.HIGH
                    else (candidate.price < last_same_type.price)
                )
                if better:
                    # In a stateless/non-mutating design, we cannot modify the graph.
                    # However, to be strictly correct, we return candidate if it is better.
                    # The caller aggregate root is responsible for replacing it.
                    pass
                else:
                    return None

            # Alternate/Duplicate Swing filter
            nodes = graph.nodes
            if nodes:
                last_swing = nodes[-1]
                if last_swing.type == candidate.type:
                    # Consecutive duplicate. Keep the extreme one.
                    better = (
                        (candidate.price > last_swing.price)
                        if candidate.type == SwingType.HIGH
                        else (candidate.price < last_swing.price)
                    )
                    if better:
                        pass
                    elif candidate.price == last_swing.price and (
                        (candidate.type == SwingType.HIGH and self.config.allow_equal_highs)
                        or (candidate.type == SwingType.LOW and self.config.allow_equal_lows)
                    ):
                        pass
                    else:
                        return None

            # Min Price Distance filter
            if self.config.minimum_price_distance > 0.0 and last_same_type:
                if abs(candidate.price - last_same_type.price) < self.config.minimum_price_distance:
                    better = (
                        (candidate.price > last_same_type.price)
                        if candidate.type == SwingType.HIGH
                        else (candidate.price < last_same_type.price)
                    )
                    if not better:
                        return None

        # Classify candidates
        if self.config.classification_enabled:
            candidate.classification = self._classify_swing(candidate, bars)
        else:
            candidate.classification = SwingClassification.UNKNOWN

        # Calculate Strength
        candidate.strength = self._calculate_strength(candidate, bars)
        candidate.strength_category = self._map_strength_category(candidate.strength)

        # Set Links to last swing in graph
        nodes = graph.nodes
        if nodes:
            prev = nodes[-1]
            candidate.previous_id = prev.id
            candidate.bar_distance = candidate.index - prev.index
            candidate.price_distance = float(candidate.price - prev.price)

        return candidate

    def check_upgrade(self, swing: Swing, bars: Sequence[Bar]) -> bool:
        """Determines if an existing MINOR swing is eligible for upgrade to MAJOR.

        Args:
            swing: The confirmed MINOR Swing to check.
            bars: The historical bar timeline sequence.

        Returns:
            True if the swing qualifies as MAJOR, False otherwise.
        """
        if swing.classification == SwingClassification.MAJOR:
            return True

        left_major = self.config.left_bars * 2
        right_major = self.config.right_bars * 2
        idx = swing.index
        n = len(bars)

        # Ensure we have enough bars to evaluate the full major window
        if idx - left_major < 0 or idx + right_major >= n:
            return False

        if swing.type == SwingType.HIGH:
            # Check left
            for j in range(1, left_major + 1):
                prev_high = bars[idx - j].high
                if self.config.allow_equal_highs:
                    if swing.price < prev_high:
                        return False
                else:
                    if swing.price <= prev_high:
                        return False
            # Check right
            for k in range(1, right_major + 1):
                next_high = bars[idx + k].high
                if self.config.allow_equal_highs:
                    if swing.price < next_high:
                        return False
                else:
                    if swing.price <= next_high:
                        return False

        elif swing.type == SwingType.LOW:
            # Check left
            for j in range(1, left_major + 1):
                prev_low = bars[idx - j].low
                if self.config.allow_equal_lows:
                    if swing.price > prev_low:
                        return False
                else:
                    if swing.price >= prev_low:
                        return False
            # Check right
            for k in range(1, right_major + 1):
                next_low = bars[idx + k].low
                if self.config.allow_equal_lows:
                    if swing.price > next_low:
                        return False
                else:
                    if swing.price >= next_low:
                        return False

        return True

    def _apply_filtering(self, swings: list[Swing], bars: Sequence[Bar]) -> list[Swing]:
        """Applies MinBarDistance, Duplicate, and MinPriceDistance filters to candidate list."""
        if not self.config.filter_enabled or len(swings) <= 1:
            return swings

        # 1. Bar Distance Filter (HIGHs and LOWs resolved separately)
        highs = [s for s in swings if s.type == SwingType.HIGH]
        lows = [s for s in swings if s.type == SwingType.LOW]
        min_dist = self.config.minimum_bar_distance

        def resolve_dist(items: list[Swing], is_high: bool) -> list[Swing]:
            if not items:
                return []
            filtered = [items[0]]
            for item in items[1:]:
                prev = filtered[-1]
                if item.index - prev.index < min_dist:
                    better = (item.price > prev.price) if is_high else (item.price < prev.price)
                    if better:
                        filtered[-1] = item
                else:
                    filtered.append(item)
            return filtered

        filtered_highs = resolve_dist(highs, is_high=True)
        filtered_lows = resolve_dist(lows, is_high=False)

        combined = sorted(filtered_highs + filtered_lows, key=lambda s: s.index)

        # 2. Duplicate Swings Filter (enforces alternation of types)
        alternated: list[Swing] = []
        for swing in combined:
            if not alternated:
                alternated.append(swing)
                continue
            prev = alternated[-1]
            if prev.type == swing.type:
                better = (
                    (swing.price > prev.price)
                    if swing.type == SwingType.HIGH
                    else (swing.price < prev.price)
                )
                if better:
                    alternated[-1] = swing
                elif swing.price == prev.price and (
                    (swing.type == SwingType.HIGH and self.config.allow_equal_highs)
                    or (swing.type == SwingType.LOW and self.config.allow_equal_lows)
                ):
                    alternated.append(swing)
            else:
                alternated.append(swing)

        # 3. Price Distance Filter
        if self.config.minimum_price_distance <= 0.0:
            return alternated

        highs_alt = [s for s in alternated if s.type == SwingType.HIGH]
        lows_alt = [s for s in alternated if s.type == SwingType.LOW]

        def resolve_price(items: list[Swing]) -> list[Swing]:
            if not items:
                return []
            filtered = [items[0]]
            for item in items[1:]:
                prev = filtered[-1]
                if abs(item.price - prev.price) < self.config.minimum_price_distance:
                    better = (
                        (item.price > prev.price)
                        if item.type == SwingType.HIGH
                        else (item.price < prev.price)
                    )
                    if better:
                        filtered[-1] = item
                else:
                    filtered.append(item)
            return filtered

        filtered_highs_p = resolve_price(highs_alt)
        filtered_lows_p = resolve_price(lows_alt)

        return sorted(filtered_highs_p + filtered_lows_p, key=lambda s: s.index)

    def _classify_swing(self, swing: Swing, bars: Sequence[Bar]) -> SwingClassification:
        """Classifies a swing as MAJOR if it satisfies twice the window bounds."""
        left_major = self.config.left_bars * 2
        right_major = self.config.right_bars * 2
        idx = swing.index
        n = len(bars)

        # Ensure we have enough bounds to check for major classification
        if idx - left_major < 0 or idx + right_major >= n:
            return SwingClassification.MINOR

        if swing.type == SwingType.HIGH:
            # Check left
            for j in range(1, left_major + 1):
                prev_high = bars[idx - j].high
                if self.config.allow_equal_highs:
                    if swing.price < prev_high:
                        return SwingClassification.MINOR
                else:
                    if swing.price <= prev_high:
                        return SwingClassification.MINOR
            # Check right
            for k in range(1, right_major + 1):
                next_high = bars[idx + k].high
                if self.config.allow_equal_highs:
                    if swing.price < next_high:
                        return SwingClassification.MINOR
                else:
                    if swing.price <= next_high:
                        return SwingClassification.MINOR

        elif swing.type == SwingType.LOW:
            # Check left
            for j in range(1, left_major + 1):
                prev_low = bars[idx - j].low
                if self.config.allow_equal_lows:
                    if swing.price > prev_low:
                        return SwingClassification.MINOR
                else:
                    if swing.price >= prev_low:
                        return SwingClassification.MINOR
            # Check right
            for k in range(1, right_major + 1):
                next_low = bars[idx + k].low
                if self.config.allow_equal_lows:
                    if swing.price > next_low:
                        return SwingClassification.MINOR
                else:
                    if swing.price >= next_low:
                        return SwingClassification.MINOR

        return SwingClassification.MAJOR

    def _calculate_strength(self, swing: Swing, bars: Sequence[Bar]) -> float:
        """Calculates relative strength of a swing using wick divergence normalized by average range."""
        idx = swing.index
        left = self.config.left_bars
        right = self.config.right_bars

        # Extract window
        start = max(0, idx - left)
        end = min(len(bars), idx + right + 1)
        window = bars[start:end]
        if not window:
            return 1.0

        # Average high-low range of the window
        avg_range = sum(b.high - b.low for b in window) / len(window)
        if avg_range == 0.0:
            avg_range = 1.0

        left_window = bars[max(0, idx - left) : idx]
        right_window = bars[idx + 1 : min(len(bars), idx + right + 1)]

        left_val = (
            sum(b.high for b in left_window) / len(left_window) if left_window else swing.price
        )
        right_val = (
            sum(b.high for b in right_window) / len(right_window) if right_window else swing.price
        )

        if swing.type == SwingType.HIGH:
            divergence = swing.price - (left_val + right_val) / 2.0
            strength = divergence / avg_range
        else:
            left_val_low = (
                sum(b.low for b in left_window) / len(left_window) if left_window else swing.price
            )
            right_val_low = (
                sum(b.low for b in right_window) / len(right_window)
                if right_window
                else swing.price
            )
            divergence = (left_val_low + right_val_low) / 2.0 - swing.price
            strength = divergence / avg_range

        return float(max(0.0, strength))

    def _map_strength_category(self, strength: float) -> SwingStrength:
        """Maps numeric strength score to SwingStrength enum categorization."""
        if strength < 0.5:
            return SwingStrength.WEAK
        elif strength < 1.0:
            return SwingStrength.NORMAL
        elif strength < 2.0:
            return SwingStrength.STRONG
        else:
            return SwingStrength.VERY_STRONG
