"""Reusable data validation functions for the Enterprise Data Engine.

Ensures that price DataFrames adhere to structural and semantic parameters.
"""

import numpy as np
import pandas as pd


def validate_non_empty(df: pd.DataFrame) -> None:
    """Verifies that the DataFrame contains data records.

    Args:
        df: The market rates DataFrame to validate.

    Raises:
        ValueError: If the DataFrame is empty.
    """
    if df.empty:
        raise ValueError("The provided dataset is empty and contains no records.")


def validate_required_columns(df: pd.DataFrame, required_cols: list[str]) -> None:
    """Verifies that all required columns are present in the DataFrame.

    Args:
        df: The market rates DataFrame to validate.
        required_cols: List of column names expected to be present.

    Raises:
        ValueError: If any of the required columns are missing.
    """
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Required column(s) missing from dataset: {missing}")


def validate_no_duplicate_timestamps(df: pd.DataFrame) -> None:
    """Verifies that the index or timestamp column contains no duplicates.

    Assumes the timestamp is set as the DataFrame index or resides in the 'time' column.

    Args:
        df: The market rates DataFrame to validate.

    Raises:
        ValueError: If duplicate timestamps are detected.
    """
    if isinstance(df.index, pd.DatetimeIndex):
        duplicates = df.index.duplicated()
        if duplicates.any():
            duplicate_vals = df.index[duplicates].unique().tolist()
            raise ValueError(f"Duplicate timestamps detected in index: {duplicate_vals}")
    elif "time" in df.columns:
        dupes_series = df["time"].duplicated()
        if dupes_series.any():
            duplicate_vals = df.loc[dupes_series, "time"].unique().tolist()
            raise ValueError(f"Duplicate timestamps detected in 'time' column: {duplicate_vals}")


def validate_ordered_timestamps(df: pd.DataFrame) -> None:
    """Verifies that the timestamps are sorted chronologically.

    Args:
        df: The market rates DataFrame to validate.

    Raises:
        ValueError: If timestamps are out of chronological order.
    """
    index_to_check = df.index if isinstance(df.index, pd.DatetimeIndex) else df["time"]
    # Check if index is monotonically increasing
    if not index_to_check.is_monotonic_increasing:
        raise ValueError("Timestamps are not strictly sorted in ascending chronological order.")


def validate_numeric_data(df: pd.DataFrame, cols: list[str]) -> None:
    """Verifies that specified columns contain only valid numeric data.

    Checks that columns are of numeric type and contain no infinite values or non-numeric types.

    Args:
        df: The market rates DataFrame to validate.
        cols: List of columns to check.

    Raises:
        ValueError: If non-numeric data or infinite values are present.
    """
    for col in cols:
        if col not in df.columns:
            continue

        # Check raw type compatibility
        if not pd.api.types.is_numeric_dtype(df[col]):
            # Try to convert to numeric to see if it was just stored as object/string
            try:
                converted = pd.to_numeric(df[col])
                if converted.isna().any() and not df[col].isna().any():
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError(
                    f"Column '{col}' contains non-numeric values or cannot be parsed as numeric."
                ) from None

        # Check for infinite values (inf / -inf)
        if np.isinf(df[col]).any():
            raise ValueError(f"Column '{col}' contains infinite values (inf/-inf).")


def validate_no_missing_values(df: pd.DataFrame, cols: list[str]) -> None:
    """Verifies that the specified columns contain no missing or NaN values.

    Args:
        df: The market rates DataFrame to validate.
        cols: List of columns to check.

    Raises:
        ValueError: If missing or NaN values are found.
    """
    for col in cols:
        if col in df.columns and df[col].isna().any():
            raise ValueError(f"Column '{col}' contains missing or NaN values.")
