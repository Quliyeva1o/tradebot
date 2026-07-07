"""Pytest test suite for Relative Strength Index (RSI) indicator."""

import pandas as pd
import pytest

from indicators.rsi import rsi


def test_rsi_normal_calculation() -> None:
    """Verifies RSI calculations with a known increasing and decreasing pattern."""
    # Given: prices rising, then falling
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 12.0, 11.0, 10.0], name="close")
    period = 3

    # When
    result = rsi(series, period=period)

    # Then
    assert isinstance(result, pd.Series)
    assert len(result) == 7
    # First 3 elements are NaN because period = 3 (needs 3 valid change points starting from index 1)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])
    assert not pd.isna(result.iloc[3])
    assert not pd.isna(result.iloc[4])
    assert not pd.isna(result.iloc[5])
    assert not pd.isna(result.iloc[6])


def test_rsi_monotonic_increasing() -> None:
    """Verifies that RSI approaches/reaches 100 for monotonically increasing series."""
    # Given
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0], name="close")
    period = 3

    # When
    result = rsi(series, period=period)

    # Then
    # For a monotonically increasing series, avg_loss will be 0.0, so RSI should be 100.0.
    assert result.iloc[3] == 100.0
    assert result.iloc[4] == 100.0
    assert result.iloc[5] == 100.0


def test_rsi_monotonic_decreasing() -> None:
    """Verifies that RSI approaches/reaches 0 for monotonically decreasing series."""
    # Given
    series = pd.Series([15.0, 14.0, 13.0, 12.0, 11.0, 10.0], name="close")
    period = 3

    # When
    result = rsi(series, period=period)

    # Then
    # For a monotonically decreasing series, avg_gain will be 0.0, and avg_loss > 0, so RSI should be 0.0.
    assert result.iloc[3] == 0.0
    assert result.iloc[4] == 0.0
    assert result.iloc[5] == 0.0


def test_rsi_zero_loss_edge_case() -> None:
    """Verifies that avg_loss = 0 does not raise ZeroDivisionError and returns RSI = 100."""
    # Given: A series where prices only rise or stay constant (no losses)
    series = pd.Series([100.0, 101.0, 101.0, 102.0, 103.0], name="close")
    period = 3

    # When
    result = rsi(series, period=period)

    # Then
    assert result.iloc[3] == 100.0
    assert result.iloc[4] == 100.0


def test_rsi_invalid_period() -> None:
    """Verifies that DataValidationError is raised when period <= 0."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0])

    with pytest.raises(ValueError):
        rsi(series, period=0)

    with pytest.raises(ValueError):
        rsi(series, period=-5)


def test_rsi_short_series() -> None:
    """Verifies that DataValidationError is raised when series length < period."""
    series = pd.Series([1.0, 2.0, 3.0])

    with pytest.raises(ValueError):
        rsi(series, period=4)


def test_rsi_empty_series() -> None:
    """Verifies that EmptyDataError is raised when series is empty."""
    series = pd.Series([], dtype=float)

    with pytest.raises(ValueError):
        rsi(series, period=3)
