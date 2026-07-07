"""Relative Strength Index (RSI) indicator module."""

import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculates Relative Strength Index on a pandas Series.

    Args:
        series: Pandas Series of price values (typically close).
        period: Period window size.

    Returns:
        A pandas Series containing the computed RSI values.

    Raises:
        EmptyDataError: If the input series is empty.
        DataValidationError: If the period is less than or equal to 0,
            or if the series length is less than the period.
    """
    if series.empty:
        from core.exceptions import EmptyDataError

        raise EmptyDataError()

    if period <= 0:
        from core.exceptions import DataValidationError

        raise DataValidationError(
            f"Invalid period {period} for RSI. Period must be greater than 0."
        )

    if len(series) < period:
        from core.exceptions import DataValidationError

        raise DataValidationError(
            f"Series length {len(series)} is less than period {period}."
        )

    import numpy as np

    diff = series.diff()
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)

    # Wilder's smoothed average: alpha = 1 / period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    # Calculate RS, avoiding division by zero warnings
    rs = pd.Series(np.nan, index=series.index)
    valid_mask = (avg_loss != 0) & (avg_loss.notna())
    rs.loc[valid_mask] = avg_gain.loc[valid_mask] / avg_loss.loc[valid_mask]

    rsi_series = 100.0 - (100.0 / (1.0 + rs))

    # If avg_loss is 0 (and not NaN), RSI should be 100.0
    zero_loss_mask = (avg_loss == 0.0) & (avg_loss.notna())
    rsi_series.loc[zero_loss_mask] = 100.0

    return rsi_series
