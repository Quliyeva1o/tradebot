"""Pytest test suite for Average True Range (ATR) indicator."""

import pandas as pd
import pytest

from indicators.exceptions import DataValidationError, EmptyDataError, MissingColumnError
from indicators.atr import atr


def test_atr_normal_calculation() -> None:
    """Verifies ATR calculations against a manually computed synthetic OHLC dataset."""
    # Given: A synthetic dataset
    df = pd.DataFrame({
        "open": [10.0, 11.0, 13.0, 12.0, 14.0],
        "high": [12.0, 13.0, 15.0, 13.0, 16.0],
        "low": [9.0, 10.0, 12.0, 10.0, 13.0],
        "close": [11.0, 12.0, 14.0, 11.0, 15.0]
    })
    period = 3

    # When
    result = atr(df, period=period)

    # Then
    assert isinstance(result, pd.Series)
    assert len(result) == 5
    assert result.name == "atr"

    # Manually calculated values for period=3 (alpha = 1 / 3):
    # TR_0 = max(12-9, NaN, NaN) = 3.0
    # TR_1 = max(13-10, |13-11|, |10-11|) = max(3.0, 2.0, 1.0) = 3.0
    # TR_2 = max(15-12, |15-12|, |12-12|) = max(3.0, 3.0, 0.0) = 3.0
    # TR_3 = max(13-10, |13-14|, |10-14|) = max(3.0, 1.0, 4.0) = 4.0
    # TR_4 = max(16-13, |16-11|, |13-11|) = max(3.0, 5.0, 2.0) = 5.0
    #
    # ATR calculations using Wilder's smoothing (.ewm(alpha=1/3, adjust=False)):
    # ATR_0 = TR_0 = 3.0
    # ATR_1 = (2/3) * 3.0 + (1/3) * 3.0 = 3.0
    # ATR_2 = (2/3) * 3.0 + (1/3) * 3.0 = 3.0
    # ATR_3 = (2/3) * 3.0 + (1/3) * 4.0 = 2.0 + 1.3333333333333333 = 3.3333333333333335 (approx 10/3)
    # ATR_4 = (2/3) * (10/3) + (1/3) * 5.0 = 20/9 + 15/9 = 35/9 = 3.888888888888889

    assert pytest.approx(result.iloc[0]) == 3.0
    assert pytest.approx(result.iloc[1]) == 3.0
    assert pytest.approx(result.iloc[2]) == 3.0
    assert pytest.approx(result.iloc[3]) == 10.0 / 3.0
    assert pytest.approx(result.iloc[4]) == 35.0 / 9.0


def test_atr_missing_columns() -> None:
    """Verifies that MissingColumnError is raised when required columns are missing."""
    # Given: missing 'close'
    df_missing_close = pd.DataFrame({
        "open": [10.0, 11.0],
        "high": [12.0, 13.0],
        "low": [9.0, 10.0]
    })

    # Then
    with pytest.raises(MissingColumnError) as exc_info:
        atr(df_missing_close, period=3)
    assert "close" in exc_info.value.missing_cols

    # Given: missing multiple columns
    df_missing_all = pd.DataFrame({
        "open": [10.0, 11.0]
    })
    with pytest.raises(MissingColumnError) as exc_info:
        atr(df_missing_all, period=3)
    assert "high" in exc_info.value.missing_cols
    assert "low" in exc_info.value.missing_cols
    assert "close" in exc_info.value.missing_cols


def test_atr_empty_dataframe() -> None:
    """Verifies that EmptyDataError is raised when the DataFrame is empty."""
    # Given
    df_empty = pd.DataFrame(columns=["open", "high", "low", "close"])

    # Then
    with pytest.raises(EmptyDataError):
        atr(df_empty, period=3)


def test_atr_invalid_period() -> None:
    """Verifies that DataValidationError is raised when period <= 0."""
    # Given
    df = pd.DataFrame({
        "open": [10.0],
        "high": [12.0],
        "low": [9.0],
        "close": [11.0]
    })

    # Then
    with pytest.raises(DataValidationError):
        atr(df, period=0)

    with pytest.raises(DataValidationError):
        atr(df, period=-5)
