"""Pytest test suite for Moving Average Convergence Divergence (MACD) indicator."""

import pandas as pd
import pytest

from indicators.exceptions import DataValidationError, EmptyDataError
from indicators.ema import ema
from indicators.macd import macd


def test_macd_normal_calculation() -> None:
    """Verifies that MACD components are calculated correctly using the underlying EMA function."""
    # Given
    series = pd.Series([10.0, 12.0, 15.0, 14.0, 16.0, 20.0, 18.0, 22.0, 25.0, 24.0], name="close")
    fast_period = 3
    slow_period = 6
    signal_period = 4

    # When
    macd_line, signal_line, histogram = macd(
        series,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
    )

    # Then
    assert isinstance(macd_line, pd.Series)
    assert isinstance(signal_line, pd.Series)
    assert isinstance(histogram, pd.Series)

    # Verify lengths match the input series length
    assert len(macd_line) == len(series)
    assert len(signal_line) == len(series)
    assert len(histogram) == len(series)

    # Verify exact calculations match the required formula logic
    expected_macd_line = ema(series, fast_period) - ema(series, slow_period)
    expected_signal_line = ema(expected_macd_line, signal_period)
    expected_histogram = expected_macd_line - expected_signal_line

    pd.testing.assert_series_equal(macd_line, expected_macd_line)
    pd.testing.assert_series_equal(signal_line, expected_signal_line)
    pd.testing.assert_series_equal(histogram, expected_histogram)


def test_macd_fast_ge_slow() -> None:
    """Verifies that DataValidationError is raised when fast_period >= slow_period."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], name="close")

    # fast_period == slow_period
    with pytest.raises(DataValidationError) as exc_info:
        macd(series, fast_period=12, slow_period=12)
    assert "must be less than slow_period" in str(exc_info.value)

    # fast_period > slow_period
    with pytest.raises(DataValidationError) as exc_info:
        macd(series, fast_period=15, slow_period=10)
    assert "must be less than slow_period" in str(exc_info.value)


def test_macd_invalid_period() -> None:
    """Verifies that DataValidationError is raised when any period is <= 0."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], name="close")

    # fast_period <= 0
    with pytest.raises(DataValidationError):
        macd(series, fast_period=0, slow_period=26, signal_period=9)

    # slow_period <= 0
    with pytest.raises(DataValidationError):
        macd(series, fast_period=12, slow_period=-5, signal_period=9)

    # signal_period <= 0
    with pytest.raises(DataValidationError):
        macd(series, fast_period=12, slow_period=26, signal_period=0)


def test_macd_empty_series() -> None:
    """Verifies that EmptyDataError is raised when the input series is empty."""
    series = pd.Series([], dtype=float)

    with pytest.raises(EmptyDataError):
        macd(series, fast_period=12, slow_period=26, signal_period=9)
