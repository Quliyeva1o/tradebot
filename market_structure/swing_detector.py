"""Swing Detection Engine module.

Implements high-performance, non-repainting swing high and swing low pivot detectors.
"""

from abc import ABC, abstractmethod
import numpy as np
import pandas as pd

from core.exceptions import DataValidationError, InvalidTimestampError, MissingColumnError
from market_structure.swing_models import (
    Swing,
    SwingClassification,
    SwingConfig,
    SwingStrength,
    SwingType,
)


# --- Strength Calculators ---

class SwingStrengthCalculator(ABC):
    """Interface for extensible swing strength calculation."""

    @abstractmethod
    def calculate(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        """Calculates a specific strength metric for the list of swings."""
        pass


class LocalDominanceStrengthCalculator(SwingStrengthCalculator):
    """Calculates swing strength based on local wick divergence relative to average volatility range."""

    def calculate(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if not swings:
            return swings

        left = config.left_bars
        right = config.right_bars
        total_window = left + right + 1

        # Volatility normalization factor: average high-low range of surrounding window
        avg_range = (df["high"] - df["low"]).rolling(window=total_window, center=True).mean()
        # Handle edges and guard against division by zero
        avg_range = avg_range.ffill().bfill().replace(0.0, 1.0)

        # Precalculate mean series to avoid O(N) rolling calculations in the loop
        left_mean_high = df["high"].shift(1).rolling(window=left).mean()
        right_mean_high = df["high"].shift(-right).rolling(window=right).mean()
        divergence_high = df["high"] - (left_mean_high + right_mean_high) / 2.0
        rel_strength_high = (divergence_high / avg_range).fillna(0.0)

        left_mean_low = df["low"].shift(1).rolling(window=left).mean()
        right_mean_low = df["low"].shift(-right).rolling(window=right).mean()
        divergence_low = (left_mean_low + right_mean_low) / 2.0 - df["low"]
        rel_strength_low = (divergence_low / avg_range).fillna(0.0)

        rel_strength_high_arr = rel_strength_high.to_numpy()
        rel_strength_low_arr = rel_strength_low.to_numpy()

        for swing in swings:
            idx = swing.index
            if swing.type == SwingType.HIGH:
                swing.strength = float(rel_strength_high_arr[idx])
            elif swing.type == SwingType.LOW:
                swing.strength = float(rel_strength_low_arr[idx])
            
            # Map float strength to category for backward compatibility
            if swing.strength < 0.5:
                swing.strength_category = SwingStrength.WEAK
            elif swing.strength < 1.0:
                swing.strength_category = SwingStrength.NORMAL
            elif swing.strength < 2.0:
                swing.strength_category = SwingStrength.STRONG
            else:
                swing.strength_category = SwingStrength.VERY_STRONG

        return swings


class PriceExcursionStrengthCalculator(SwingStrengthCalculator):
    """Calculates swing strength based on the maximum price excursion relative to opposite extreme."""

    def calculate(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if not swings:
            return swings
        left = config.left_bars
        right = config.right_bars

        # Precalculate min/max arrays to do fast lookups
        low_min = df["low"].rolling(window=left + right + 1, center=True).min()
        high_max = df["high"].rolling(window=left + right + 1, center=True).max()

        low_min_arr = low_min.to_numpy()
        high_max_arr = high_max.to_numpy()
        high_arr = df["high"].to_numpy()
        low_arr = df["low"].to_numpy()

        for swing in swings:
            idx = swing.index
            if swing.type == SwingType.HIGH:
                min_low = low_min_arr[idx]
                if np.isnan(min_low):
                    min_low = low_arr[max(0, idx - left): min(len(df), idx + right + 1)].min()
                excursion = swing.price - min_low
                swing.strength = max(swing.strength, float(excursion))
            elif swing.type == SwingType.LOW:
                max_high = high_max_arr[idx]
                if np.isnan(max_high):
                    max_high = high_arr[max(0, idx - left): min(len(df), idx + right + 1)].max()
                excursion = max_high - swing.price
                swing.strength = max(swing.strength, float(excursion))
        return swings


class BarDistanceStrengthCalculator(SwingStrengthCalculator):
    """Calculates swing strength based on the left/right confirmation bar configuration."""

    def calculate(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        val = float(config.left_bars + config.right_bars)
        for swing in swings:
            swing.strength = max(swing.strength, val)
        return swings


class CompositeSwingStrengthCalculator(SwingStrengthCalculator):
    """Orchestrates all strength calculations."""

    def __init__(self) -> None:
        self.calculators = [
            LocalDominanceStrengthCalculator(),
            PriceExcursionStrengthCalculator(),
            BarDistanceStrengthCalculator()
        ]

    def calculate(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        for calc in self.calculators:
            swings = calc.calculate(swings, df, config)
        return swings


# --- Filtering Layer ---

class SwingFilter(ABC):
    """Interface for modular swing filtering rules."""

    @abstractmethod
    def filter(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        """Applies a filter to the swing sequence and returns the filtered list."""
        pass


class MinBarDistanceFilter(SwingFilter):
    """Filters consecutive swings of the same type that are closer than minimum_bar_distance."""

    def filter(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if len(swings) <= 1:
            return swings

        # Filter HIGHs and LOWs separately to avoid overlapping type interference
        highs = [s for s in swings if s.type == SwingType.HIGH]
        lows = [s for s in swings if s.type == SwingType.LOW]

        min_dist = config.minimum_bar_distance

        def resolve_list(items: list[Swing], is_high: bool) -> list[Swing]:
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

        filtered_highs = resolve_list(highs, is_high=True)
        filtered_lows = resolve_list(lows, is_high=False)

        # Merge them back chronologically
        combined = sorted(filtered_highs + filtered_lows, key=lambda s: s.index)
        return combined


class MinPriceDistanceFilter(SwingFilter):
    """Filters swings of the same type that do not meet the minimum_price_distance."""

    def filter(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if config.minimum_price_distance <= 0.0 or not swings:
            return swings

        highs = [s for s in swings if s.type == SwingType.HIGH]
        lows = [s for s in swings if s.type == SwingType.LOW]

        def resolve_price_dist(items: list[Swing]) -> list[Swing]:
            if not items:
                return []
            filtered = [items[0]]
            for item in items[1:]:
                prev = filtered[-1]
                if abs(item.price - prev.price) < config.minimum_price_distance:
                    better = (item.price > prev.price) if item.type == SwingType.HIGH else (item.price < prev.price)
                    if better:
                        filtered[-1] = item
                else:
                    filtered.append(item)
            return filtered

        filtered_highs = resolve_price_dist(highs)
        filtered_lows = resolve_price_dist(lows)

        combined = sorted(filtered_highs + filtered_lows, key=lambda s: s.index)
        return combined


class DuplicateSwingsFilter(SwingFilter):
    """Enforces alternation between HIGH and LOW swings. Keeps the extreme one of consecutive duplicates."""

    def filter(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if len(swings) <= 1:
            return swings

        filtered: list[Swing] = []
        for swing in swings:
            if not filtered:
                filtered.append(swing)
                continue

            prev = filtered[-1]
            if prev.type == swing.type:
                # Consecutive duplicate swing type! Keep the extreme one
                if swing.type == SwingType.HIGH:
                    if swing.price > prev.price:
                        filtered[-1] = swing
                    elif swing.price == prev.price and config.allow_equal_highs:
                        filtered.append(swing)
                elif swing.type == SwingType.LOW:
                    if swing.price < prev.price:
                        filtered[-1] = swing
                    elif swing.price == prev.price and config.allow_equal_lows:
                        filtered.append(swing)
            else:
                filtered.append(swing)

        return filtered


class MergeCloseSwingsFilter(SwingFilter):
    """Merges swings that are close, keeping the dominant swing ID and fields."""

    def filter(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if not config.merge_close_swings or len(swings) <= 1:
            return swings

        # Merges close swings of the same type using MinBarDistanceFilter
        bar_filter = MinBarDistanceFilter()
        return bar_filter.filter(swings, df, config)


# --- Classifiers ---

class SwingClassifier(ABC):
    """Interface for swing classification logic."""

    @abstractmethod
    def classify(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        """Classifies each swing (e.g. Major, Minor, Unknown)."""
        pass


class WindowScaleSwingClassifier(SwingClassifier):
    """Classifies swings as MAJOR if they are also pivots at a larger scale (e.g. left_bars*2, right_bars*2).

    Otherwise classified as MINOR.
    """

    def classify(self, swings: list[Swing], df: pd.DataFrame, config: SwingConfig) -> list[Swing]:
        if not swings:
            return swings

        left_major = config.left_bars * 2
        right_major = config.right_bars * 2

        high_arr = df["high"].to_numpy()
        low_arr = df["low"].to_numpy()

        for swing in swings:
            idx = swing.index
            is_major = True

            if swing.type == SwingType.HIGH:
                # Check left
                for j in range(1, left_major + 1):
                    if idx - j >= 0:
                        shifted_val = high_arr[idx - j]
                        if config.allow_equal_highs:
                            if swing.price < shifted_val:
                                is_major = False
                                break
                        else:
                            if swing.price <= shifted_val:
                                is_major = False
                                break
                    else:
                        is_major = False
                        break
                # Check right
                if is_major:
                    for k in range(1, right_major + 1):
                        if idx + k < len(high_arr):
                            shifted_val = high_arr[idx + k]
                            if config.allow_equal_highs:
                                if swing.price < shifted_val:
                                    is_major = False
                                    break
                            else:
                                if swing.price <= shifted_val:
                                    is_major = False
                                    break
                        else:
                            is_major = False
                            break
            elif swing.type == SwingType.LOW:
                # Check left
                for j in range(1, left_major + 1):
                    if idx - j >= 0:
                        shifted_val = low_arr[idx - j]
                        if config.allow_equal_lows:
                            if swing.price > shifted_val:
                                is_major = False
                                break
                        else:
                            if swing.price >= shifted_val:
                                is_major = False
                                break
                    else:
                        is_major = False
                        break
                # Check right
                if is_major:
                    for k in range(1, right_major + 1):
                        if idx + k < len(low_arr):
                            shifted_val = low_arr[idx + k]
                            if config.allow_equal_lows:
                                if swing.price > shifted_val:
                                    is_major = False
                                    break
                            else:
                                if swing.price >= shifted_val:
                                    is_major = False
                                    break
                        else:
                            is_major = False
                            break
            else:
                is_major = False

            if is_major:
                swing.classification = SwingClassification.MAJOR
            else:
                swing.classification = SwingClassification.MINOR

        return swings


# --- Swing Graph ---

class SwingGraph:
    """Represents the relationships and traversal paths of the Swing sequence."""

    def __init__(self, swings: list[Swing]) -> None:
        self.swings_dict = {s.id: s for s in swings}
        self.swings_list = sorted(swings, key=lambda s: s.index)

    def get_swing(self, swing_id: str) -> Swing | None:
        """Finds a swing by its unique ID."""
        return self.swings_dict.get(swing_id)

    def get_previous_swing(self, swing: Swing) -> Swing | None:
        """Gets the chronologically preceding swing in the graph."""
        if swing.previous_id:
            return self.get_swing(swing.previous_id)
        return None

    def get_next_swing(self, swing: Swing) -> Swing | None:
        """Gets the chronologically succeeding swing in the graph."""
        if swing.next_id:
            return self.get_swing(swing.next_id)
        return None

    def get_previous_of_type(self, swing: Swing, swing_type: SwingType) -> Swing | None:
        """Traverses backwards to find the nearest swing of the specified type."""
        curr = swing
        while True:
            prev = self.get_previous_swing(curr)
            if not prev:
                return None
            if prev.type == swing_type:
                return prev
            curr = prev

    def get_next_of_type(self, swing: Swing, swing_type: SwingType) -> Swing | None:
        """Traverses forwards to find the nearest swing of the specified type."""
        curr = swing
        while True:
            nxt = self.get_next_swing(curr)
            if not nxt:
                return None
            if nxt.type == swing_type:
                return nxt
            curr = nxt

    def get_previous_major(self, swing: Swing) -> Swing | None:
        """Traverses backwards to find the nearest MAJOR swing."""
        curr = swing
        while True:
            prev = self.get_previous_swing(curr)
            if not prev:
                return None
            if prev.classification == SwingClassification.MAJOR:
                return prev
            curr = prev

    def get_next_major(self, swing: Swing) -> Swing | None:
        """Traverses forwards to find the nearest MAJOR swing."""
        curr = swing
        while True:
            nxt = self.get_next_swing(curr)
            if not nxt:
                return None
            if nxt.classification == SwingClassification.MAJOR:
                return nxt
            curr = nxt


# --- Swing Detector ---

class SwingDetector:
    """Stateless engine to detect swing high and swing low pivot levels in price data."""

    def __init__(self, config: SwingConfig | None = None) -> None:
        """Initializes the SwingDetector.

        Args:
            config: Configurations for window sizes and equal level boundaries.
        """
        self.config = config or SwingConfig()
        
        # State containers for public APIs
        self.raw_swings: list[Swing] = []
        self.filtered_swings: list[Swing] = []
        self.major_swings: list[Swing] = []
        self.minor_swings: list[Swing] = []
        self.swings: list[Swing] = []  # Final swings output
        self.swing_graph: SwingGraph | None = None

        # Pipelines
        self.strength_calculator = CompositeSwingStrengthCalculator()
        
        # Filters configuration
        self.filters: list[SwingFilter] = []
        if self.config.filter_enabled:
            # 1. Resolve distance spacing (same type distance)
            self.filters.append(MinBarDistanceFilter())
            # 2. Merge equal highs/lows and ignore duplicates
            self.filters.append(DuplicateSwingsFilter())
            # 3. Minimum price distance filter
            if self.config.minimum_price_distance > 0.0:
                self.filters.append(MinPriceDistanceFilter())
            # 4. Merge close swings filter
            if self.config.merge_close_swings:
                self.filters.append(MergeCloseSwingsFilter())

        # Classifiers configuration
        self.classifier = WindowScaleSwingClassifier()

    def _validate(self, df: pd.DataFrame) -> None:
        """Ensures that the input DataFrame meets structural requirements.

        Args:
            df: Input price DataFrame.

        Raises:
            DataValidationError: If structural checks fail.
            MissingColumnError: If required high/low columns are missing.
        """
        required = ["high", "low", "time"]
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise MissingColumnError(missing)

        min_required_len = self.config.left_bars + self.config.right_bars + 1
        if len(df) < min_required_len:
            raise DataValidationError(
                f"Dataset too small. Requires at least {min_required_len} rows for detection."
            )

        if not df["time"].is_monotonic_increasing:
            raise InvalidTimestampError("Price history timestamps must be sorted chronologically.")

        if df["time"].duplicated().any():
            from core.exceptions import DuplicateTimestampError
            raise DuplicateTimestampError("Duplicate timestamps detected in swing input.")

    def _detect_highs(self, df: pd.DataFrame) -> pd.Series:
        """Detects candidate swing highs using price shifts."""
        high = df["high"]
        is_high = pd.Series(True, index=df.index)

        # Compare with preceding candles
        for j in range(1, self.config.left_bars + 1):
            shifted = high.shift(j)
            if self.config.allow_equal_highs:
                is_high &= (high >= shifted)
            else:
                is_high &= (high > shifted)

        # Compare with succeeding candles
        for k in range(1, self.config.right_bars + 1):
            shifted = high.shift(-k)
            if self.config.allow_equal_highs:
                is_high &= (high >= shifted)
            else:
                is_high &= (high > shifted)

        # Bound edges are unconfirmable
        is_high.iloc[: self.config.left_bars] = False
        is_high.iloc[-self.config.right_bars :] = False
        return is_high.fillna(False)

    def _detect_lows(self, df: pd.DataFrame) -> pd.Series:
        """Detects candidate swing lows using price shifts."""
        low = df["low"]
        is_low = pd.Series(True, index=df.index)

        # Compare with preceding candles
        for j in range(1, self.config.left_bars + 1):
            shifted = low.shift(j)
            if self.config.allow_equal_lows:
                is_low &= (low <= shifted)
            else:
                is_low &= (low < shifted)

        # Compare with succeeding candles
        for k in range(1, self.config.right_bars + 1):
            shifted = low.shift(-k)
            if self.config.allow_equal_lows:
                is_low &= (low <= shifted)
            else:
                is_low &= (low < shifted)

        # Bound edges are unconfirmable
        is_low.iloc[: self.config.left_bars] = False
        is_low.iloc[-self.config.right_bars :] = False
        return is_low.fillna(False)

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """Runs the Swing Detection Engine pipeline.

        Args:
            df: Price history DataFrame containing standard columns.

        Returns:
            Enriched DataFrame with appended swing structures.
        """
        self._validate(df)

        # 1. Vectorized Raw Pivot Candidate Search (Phase 1)
        highs_mask = self._detect_highs(df)
        lows_mask = self._detect_lows(df)

        time_arr = df["time"].to_numpy()
        high_arr = df["high"].to_numpy()
        low_arr = df["low"].to_numpy()

        high_indices = highs_mask.index[highs_mask].to_numpy()
        low_indices = lows_mask.index[lows_mask].to_numpy()

        raw_swings: list[Swing] = []
        for idx in high_indices:
            raw_swings.append(
                Swing(
                    id=f"swing_{idx}_high",
                    timestamp=pd.Timestamp(time_arr[idx]),
                    index=int(idx),
                    price=float(high_arr[idx]),
                    type=SwingType.HIGH,
                )
            )

        for idx in low_indices:
            raw_swings.append(
                Swing(
                    id=f"swing_{idx}_low",
                    timestamp=pd.Timestamp(time_arr[idx]),
                    index=int(idx),
                    price=float(low_arr[idx]),
                    type=SwingType.LOW,
                )
            )

        # Sort chronologically
        raw_swings = sorted(raw_swings, key=lambda s: s.index)
        
        # Calculate initial strengths for raw swings
        raw_swings = self.strength_calculator.calculate(raw_swings, df, self.config)
        self.raw_swings = raw_swings

        # 2. Filter Swings (Phase 2)
        filtered_swings = list(raw_swings)
        if self.config.filter_enabled:
            for f in self.filters:
                filtered_swings = f.filter(filtered_swings, df, self.config)
        self.filtered_swings = filtered_swings

        # 3. Classify Swings (Phase 3)
        classified_swings = list(filtered_swings)
        if self.config.classification_enabled:
            classified_swings = self.classifier.classify(classified_swings, df, self.config)
        
        self.major_swings = [s for s in classified_swings if s.classification == SwingClassification.MAJOR]
        self.minor_swings = [s for s in classified_swings if s.classification == SwingClassification.MINOR]
        
        # 4. Construct Graph Relationships (Phase 4)
        if classified_swings:
            for i, swing in enumerate(classified_swings):
                if i > 0:
                    prev = classified_swings[i - 1]
                    swing.previous_id = prev.id
                    swing.bar_distance = swing.index - prev.index
                    swing.price_distance = float(swing.price - prev.price)
                else:
                    swing.previous_id = None
                    swing.bar_distance = None
                    swing.price_distance = None

                if i < len(classified_swings) - 1:
                    nxt = classified_swings[i + 1]
                    swing.next_id = nxt.id
                else:
                    swing.next_id = None

        self.swings = classified_swings
        self.swing_graph = SwingGraph(classified_swings)

        # 5. Populate Enriched DataFrame for Backward Compatibility (Phase 5)
        # Initialize numpy arrays
        n = len(df)
        is_swing_high_arr = np.zeros(n, dtype=bool)
        is_swing_low_arr = np.zeros(n, dtype=bool)
        swing_price_arr = np.full(n, np.nan)
        swing_type_arr = np.full(n, SwingType.NONE.value, dtype=object)
        swing_strength_arr = np.full(n, "", dtype=object)
        swing_index_arr = np.full(n, np.nan)

        # Populate numpy arrays in loop (extremely fast, no pandas overhead)
        for swing in self.swings:
            idx = swing.index
            if swing.type == SwingType.HIGH:
                is_swing_high_arr[idx] = True
                swing_type_arr[idx] = SwingType.HIGH.value
            elif swing.type == SwingType.LOW:
                is_swing_low_arr[idx] = True
                swing_type_arr[idx] = SwingType.LOW.value
            
            swing_price_arr[idx] = swing.price
            swing_strength_arr[idx] = swing.strength_category.value
            swing_index_arr[idx] = float(idx)

        # Assign to DataFrame all at once
        result = df.copy()
        result["is_swing_high"] = is_swing_high_arr
        result["is_swing_low"] = is_swing_low_arr
        result["swing_price"] = swing_price_arr
        result["swing_type"] = swing_type_arr
        result["swing_strength"] = swing_strength_arr
        result["swing_index"] = swing_index_arr

        return result

    # --- Clean Public APIs for advanced swing structures ---

    def get_raw_swings(self) -> list[Swing]:
        """Returns all raw swings before filtering."""
        return self.raw_swings

    def get_filtered_swings(self) -> list[Swing]:
        """Returns swings after the filtering layer is applied."""
        return self.filtered_swings

    def get_major_swings(self) -> list[Swing]:
        """Returns all swings classified as MAJOR."""
        return self.major_swings

    def get_minor_swings(self) -> list[Swing]:
        """Returns all swings classified as MINOR."""
        return self.minor_swings

    def get_swings(self) -> list[Swing]:
        """Returns all final (filtered & classified) swings."""
        return self.swings

    def get_swing_graph(self) -> SwingGraph | None:
        """Returns the swing graph relationships object."""
        return self.swing_graph
