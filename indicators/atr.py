"""Average True Range (ATR) indicator module."""

import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculates Average True Range on a pandas DataFrame.

    Args:
        df: DataFrame containing columns: open, high, low, close.
        period: Period window size.

    Returns:
        A pandas Series containing the computed ATR values.

    Raises:
        EmptyDataError: If the input DataFrame is empty.
        MissingColumnError: If any of the required columns ('open', 'high', 'low', 'close') are missing.
        DataValidationError: If the period is less than or equal to 0.
    """
    if df.empty:
        from core.exceptions import EmptyDataError

        raise EmptyDataError()

    if period <= 0:
        from core.exceptions import DataValidationError

        raise DataValidationError(
            f"Invalid period {period} for ATR. Period must be greater than 0."
        )

    from utils.validators import validate_required_columns

    validate_required_columns(df, ["open", "high", "low", "close"])

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothed average of True Range
    atr_series = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    atr_series.name = "atr"

    return atr_series
