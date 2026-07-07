"""Exponential Moving Average (EMA) indicator module."""

import pandas as pd


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Calculates Exponential Moving Average on a pandas Series.

    Args:
        series: Pandas Series of price values (typically close).
        period: Period window size.

    Returns:
        A pandas Series containing the computed EMA values.

    Raises:
        EmptyDataError: If the input series is empty.
        DataValidationError: If the period is less than or equal to 0.
    """
    if series.empty:
        raise ValueError("The provided dataset is empty and contains no records.")

    if period <= 0:
        raise ValueError(
            f"Invalid period {period} for EMA. Period must be greater than 0."
        )

    return series.ewm(span=period, adjust=False).mean()
