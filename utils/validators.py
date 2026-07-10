"""Reusable data validation functions for the Enterprise Data Engine.

Ensures that price sequences of Bar objects adhere to structural and semantic parameters.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.models import Bar


@dataclass(frozen=True)
class OHLCViolation:
    """A bar whose internal OHLC relationship is physically impossible."""

    timestamp: datetime
    reason: str


def check_ohlc_consistency(bars: Sequence[Bar]) -> list[OHLCViolation]:
    """Flags bars whose OHLC relationship is physically impossible.

    Checks: high >= low, high >= open/close, low <= open/close. Violations
    are reported only -- bars are left untouched in the output. Shared
    between data/download_history.py (reporting: logs every violation,
    never raises) and CSVDataProvider.validate() (fail-fast: raises if any
    violation is found).

    Args:
        bars: Bar sequence to inspect.

    Returns:
        List of OHLCViolation describing each impossible bar found.
    """
    violations: list[OHLCViolation] = []
    for bar in bars:
        if bar.high < bar.low:
            violations.append(
                OHLCViolation(bar.timestamp, f"high ({bar.high}) < low ({bar.low})")
            )
            continue
        if bar.high < bar.open or bar.high < bar.close:
            violations.append(
                OHLCViolation(
                    bar.timestamp,
                    f"high ({bar.high}) is below open ({bar.open}) or close ({bar.close})",
                )
            )
        if bar.low > bar.open or bar.low > bar.close:
            violations.append(
                OHLCViolation(
                    bar.timestamp,
                    f"low ({bar.low}) is above open ({bar.open}) or close ({bar.close})",
                )
            )
    return violations


def validate_non_empty(bars: Sequence[Bar]) -> None:
    """Verifies that the sequence contains data records.

    Args:
        bars: The sequence of Bar objects to validate.

    Raises:
        ValueError: If the sequence is empty.
    """
    if not bars:
        raise ValueError("The provided dataset is empty and contains no records.")


def validate_no_duplicate_timestamps(bars: Sequence[Bar]) -> None:
    """Verifies that the timeline contains no duplicate timestamps.

    Args:
        bars: The sequence of Bar objects to validate.

    Raises:
        ValueError: If duplicate timestamps are detected.
    """
    seen_times = set()
    duplicates = []
    for bar in bars:
        t = bar.timestamp
        if t in seen_times:
            duplicates.append(t)
        else:
            seen_times.add(t)

    if duplicates:
        # Deduplicate to show clean list of duplicate timestamps
        unique_dupes = list(dict.fromkeys(duplicates))
        raise ValueError(f"Duplicate timestamps detected in sequence: {unique_dupes}")


def validate_ordered_timestamps(bars: Sequence[Bar]) -> None:
    """Verifies that the timestamps are sorted chronologically in ascending order.

    Args:
        bars: The sequence of Bar objects to validate.

    Raises:
        ValueError: If timestamps are out of chronological order.
    """
    for i in range(1, len(bars)):
        if bars[i].timestamp < bars[i - 1].timestamp:
            raise ValueError("Timestamps are not strictly sorted in ascending chronological order.")


def validate_numeric_data(bars: Sequence[Bar], cols: list[str] | None = None) -> None:
    """Verifies that specified price fields contain only valid numeric data (no inf/nan).

    Args:
        bars: The sequence of Bar objects to validate.
        cols: Unused list of field names kept for signature compatibility.

    Raises:
        ValueError: If non-numeric data or infinite values are present.
    """
    for bar in bars:
        for field in ("open", "high", "low", "close", "volume"):
            val = getattr(bar, field)
            if not isinstance(val, (int, float)):
                raise ValueError(
                    f"Field '{field}' contains non-numeric values or cannot be parsed as numeric."
                )
            if math.isinf(val):
                raise ValueError(f"Field '{field}' contains infinite values (inf/-inf).")


def validate_no_missing_values(bars: Sequence[Bar], cols: list[str] | None = None) -> None:
    """Verifies that the price fields contain no missing or NaN values.

    Args:
        bars: The sequence of Bar objects to validate.
        cols: Unused list of field names kept for signature compatibility.

    Raises:
        ValueError: If missing or NaN values are found.
    """
    for bar in bars:
        for field in ("open", "high", "low", "close", "volume"):
            val = getattr(bar, field)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                raise ValueError(f"Field '{field}' contains missing or NaN values.")
