"""Moving Average Convergence Divergence (MACD) indicator module."""

import pandas as pd

from indicators.ema import ema


def macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates Moving Average Convergence Divergence (MACD) on a pandas Series.

    Args:
        series: Pandas Series of price values (typically close).
        fast_period: Window size for the fast EMA.
        slow_period: Window size for the slow EMA.
        signal_period: Window size for the signal EMA.

    Returns:
        A tuple of (macd_line, signal_line, histogram) as pandas Series.

    Raises:
        EmptyDataError: If the input series is empty.
        DataValidationError: If any period is less than or equal to 0, or if
            fast_period is greater than or equal to slow_period.
    """
    if series.empty:
        from indicators.exceptions import EmptyDataError

        raise EmptyDataError()

    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        from indicators.exceptions import DataValidationError

        raise DataValidationError(
            f"Periods must be greater than 0. Got fast_period={fast_period}, "
            f"slow_period={slow_period}, signal_period={signal_period}."
        )

    if fast_period >= slow_period:
        from indicators.exceptions import DataValidationError

        raise DataValidationError(
            f"fast_period ({fast_period}) must be less than slow_period ({slow_period})."
        )

    macd_line = ema(series, fast_period) - ema(series, slow_period)
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram
