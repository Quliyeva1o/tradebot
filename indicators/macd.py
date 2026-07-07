"""Moving Average Convergence Divergence (MACD) indicator module."""

import pandas as pd


def macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates MACD on a pandas Series.

    Args:
        series: Pandas Series of price values (typically close).
        fast_period: Window size for the fast EMA.
        slow_period: Window size for the slow EMA.
        signal_period: Window size for the signal EMA.

    Returns:
        A tuple of (macd_line, signal_line, histogram) as pandas Series.
    """
    raise NotImplementedError("MACD calculation logic will be implemented in a future sprint.")
