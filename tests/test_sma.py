"""Pytest test suite for Simple Moving Average (SMA) indicator."""

import pandas as pd
import pytest

from indicators.exceptions import DataValidationError, EmptyDataError
from indicators.sma import sma


def test_sma_normal_calculation() -> None:
    """Verifies that SMA is calculated correctly for standard datasets."""
    # Given
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], name="close")
    period = 3

    # When
    result = sma(series, period=period)

    # Then
    assert isinstance(result, pd.Series)
    # The first (period - 1) i.e. 2 elements should be NaN
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    # The 3rd element: (1+2+3)/3 = 2.0
    assert result.iloc[2] == 2.0
    # The 4th element: (2+3+4)/3 = 3.0
    assert result.iloc[3] == 3.0
    # The 5th element: (3+4+5)/3 = 4.0
    assert result.iloc[4] == 4.0


def test_sma_small_dataset() -> None:
    """Verifies that SMA handles datasets smaller than the period window correctly."""
    # Given
    series = pd.Series([10.0, 20.0], name="close")
    period = 3

    # When
    result = sma(series, period=period)

    # Then
    assert len(result) == 2
    assert pd.isna(result).all()


def test_sma_invalid_period() -> None:
    """Verifies that DataValidationError is raised when period <= 0."""
    series = pd.Series([1.0, 2.0, 3.0])

    with pytest.raises(DataValidationError):
        sma(series, period=0)

    with pytest.raises(DataValidationError):
        sma(series, period=-5)


def test_sma_empty_series() -> None:
    """Verifies that EmptyDataError is raised when series is empty."""
    series = pd.Series([], dtype=float)

    with pytest.raises(EmptyDataError):
        sma(series, period=3)
