"""Pytest test suite for Exponential Moving Average (EMA) indicator."""

import pandas as pd
import pytest

from core.exceptions import DataValidationError, EmptyDataError
from indicators.ema import ema


def test_ema_normal_calculation() -> None:
    """Verifies that EMA is calculated correctly using a manually calculated small sample."""
    # Given
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], name="close")
    period = 3

    # When
    result = ema(series, period=period)

    # Then
    assert isinstance(result, pd.Series)
    assert len(result) == 5
    # Manually calculated values for period=3 (alpha = 2 / (3 + 1) = 0.5)
    # EMA_0 = 1.0
    # EMA_1 = 0.5 * 2.0 + 0.5 * 1.0 = 1.5
    # EMA_2 = 0.5 * 3.0 + 0.5 * 1.5 = 2.25
    # EMA_3 = 0.5 * 4.0 + 0.5 * 2.25 = 3.125
    # EMA_4 = 0.5 * 5.0 + 0.5 * 3.125 = 4.0625
    assert pytest.approx(result.iloc[0]) == 1.0
    assert pytest.approx(result.iloc[1]) == 1.5
    assert pytest.approx(result.iloc[2]) == 2.25
    assert pytest.approx(result.iloc[3]) == 3.125
    assert pytest.approx(result.iloc[4]) == 4.0625


def test_ema_constant_values() -> None:
    """Verifies that EMA of a constant series remains constant."""
    # Given
    series = pd.Series([10.0, 10.0, 10.0, 10.0], name="close")
    period = 3

    # When
    result = ema(series, period=period)

    # Then
    assert isinstance(result, pd.Series)
    assert len(result) == 4
    for val in result:
        assert pytest.approx(val) == 10.0


def test_ema_invalid_period() -> None:
    """Verifies that DataValidationError is raised when period <= 0."""
    series = pd.Series([1.0, 2.0, 3.0])

    with pytest.raises(DataValidationError):
        ema(series, period=0)

    with pytest.raises(DataValidationError):
        ema(series, period=-3)


def test_ema_empty_series() -> None:
    """Verifies that EmptyDataError is raised when series is empty."""
    series = pd.Series([], dtype=float)

    with pytest.raises(EmptyDataError):
        ema(series, period=3)
