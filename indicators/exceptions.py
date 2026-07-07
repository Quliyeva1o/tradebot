"""Custom exceptions for technical indicators.

Ensures the indicators package remains isolated from core domain configurations.
"""


class IndicatorError(Exception):
    """Base exception for all technical indicator calculation failures."""
    pass


class EmptyDataError(IndicatorError):
    """Raised when the input series or dataframe contains no records."""
    pass


class DataValidationError(IndicatorError):
    """Raised when indicator parameters are invalid (e.g. period <= 0)."""
    pass


class MissingColumnError(IndicatorError):
    """Raised when a required column is missing from the input dataframe."""

    def __init__(self, missing_cols: list[str]) -> None:
        """Initializes the exception with missing columns.

        Args:
            missing_cols: List of column names that were expected but missing.
        """
        super().__init__(f"Required column(s) missing for indicator: {missing_cols}")
        self.missing_cols = missing_cols
