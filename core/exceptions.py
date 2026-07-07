"""Custom exceptions for the enterprise data engine.

Defines a hierarchy of exceptions to prevent leaky raw database or pandas
exceptions during the ingestion and validation phases.
"""


class DataValidationError(Exception):
    """Base domain exception for all data engine validation failures."""

    def __init__(self, message: str) -> None:
        """Initializes the exception with a detail message.

        Args:
            message: Explanation of the validation failure.
        """
        super().__init__(message)
        self.message = message


class MissingColumnError(DataValidationError):
    """Raised when one or more required columns are missing in the dataset."""

    def __init__(self, missing_cols: list[str]) -> None:
        """Initializes the exception.

        Args:
            missing_cols: List of column names that were expected but missing.
        """
        super().__init__(f"Required column(s) missing from dataset: {missing_cols}")
        self.missing_cols = missing_cols


class DuplicateTimestampError(DataValidationError):
    """Raised when duplicate timestamps are found in historical rates."""


class InvalidTimestampError(DataValidationError):
    """Raised when timestamps are invalid, unparseable, or out of order."""


class InvalidNumericDataError(DataValidationError):
    """Raised when numeric data columns contain invalid or non-numeric values."""


class EmptyDataError(DataValidationError):
    """Raised when the loaded dataset contains no records."""

    def __init__(self) -> None:
        """Initializes the exception with a default empty error message."""
        super().__init__("The provided dataset is empty and contains no records.")
