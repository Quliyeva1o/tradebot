"""CSV Market Data Provider.

Concrete implementation of IMarketDataProvider that reads, normalizes,
and validates historical market rates from local CSV files.
"""

from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import Settings
from core.exceptions import InvalidTimestampError, MissingColumnError
from core.market_data_provider import IMarketDataProvider
from utils.logging import setup_logger
from utils.validators import (
    validate_required_columns,
)

logger = setup_logger("csv_provider")


class CSVDataProvider(IMarketDataProvider):
    """Loads and sanitizes raw financial market data from a local CSV file."""

    def __init__(
        self,
        filepath: str | Path,
        settings: Settings | None = None,
    ) -> None:
        """Initializes the CSVDataProvider.

        Args:
            filepath: Absolute or relative file path to the target CSV.
            settings: Configuration overrides. If None, default Settings are loaded.
        """
        self.filepath = Path(filepath)
        self.settings = settings or Settings.load()

    def load(self) -> pd.DataFrame:
        """Loads and processes the CSV data according to framework specifications.

        Returns:
            A clean DataFrame with columns: ['time', 'open', 'high', 'low', 'close', 'volume']
            where 'time' is parsed as Datetime and sorted.

        Raises:
            FileNotFoundError: If the CSV file does not exist.
            MissingColumnError: If required columns or timestamps are missing.
            InvalidTimestampError: If date parsing fails.
        """
        if not self.filepath.exists():
            logger.error("CSV file not found: %s", self.filepath)
            raise FileNotFoundError(f"CSV data file not found at: {self.filepath}")

        logger.info("Loading raw CSV data from: %s", self.filepath)

        # Read raw CSV file
        df = pd.read_csv(self.filepath, delimiter=self.settings.CSV_DELIMITER)

        # Normalize column names: strip whitespace and convert to lowercase
        df.columns = [col.strip().lower() for col in df.columns]

        # Identify timestamp column
        time_col = None
        for possible_name in ("time", "timestamp", "date", "datetime"):
            if possible_name in df.columns:
                time_col = possible_name
                break

        if time_col is None:
            logger.error("No timestamp column found in CSV: %s", self.filepath)
            raise MissingColumnError(["time"])

        # Rename timestamp column to unified name 'time'
        if time_col != "time":
            df.rename(columns={time_col: "time"}, inplace=True)

        # Convert timestamps to pandas Datetime Series
        try:
            # We first try to parse using the configured format
            df["time"] = pd.to_datetime(
                df["time"],
                format=self.settings.DATETIME_FORMAT,
                errors="raise",
            )
        except (ValueError, TypeError):
            # Fallback to automatic format inference if format fails
            try:
                df["time"] = pd.to_datetime(
                    df["time"],
                    errors="raise",
                )
            except Exception as e:
                logger.error("Failed to parse datetime columns in %s: %s", self.filepath, e)
                raise InvalidTimestampError(
                    f"Timestamp column contains unparseable datetime strings: {e}"
                ) from e

        # Ensure timezone standard format
        if df["time"].dt.tz is None:
            df["time"] = df["time"].dt.tz_localize(self.settings.TIMEZONE)
        else:
            df["time"] = df["time"].dt.tz_convert(self.settings.TIMEZONE)

        # Normalize required OHLCV columns (case-insensitive done by lower())
        required_ohlcv = ["open", "high", "low", "close"]
        validate_required_columns(df, required_ohlcv)

        # Add optional volume if missing
        if "volume" not in df.columns:
            df["volume"] = 0.0

        # Select target columns only
        target_columns = ["time", "open", "high", "low", "close", "volume"]
        df = df[target_columns].copy()

        # Handle duplicates based on configuration policy
        if self.settings.DUPLICATE_POLICY == "drop":
            duplicate_mask = df["time"].duplicated()
            if duplicate_mask.any():
                dup_count = duplicate_mask.sum()
                logger.warning("Removing %d duplicate timestamps from dataset.", dup_count)
                df.drop_duplicates(subset=["time"], keep="last", inplace=True)

        # Handle missing values based on policy
        self._handle_missing_values(df)

        # Convert price columns to float values
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Sort values chronologically
        df.sort_values(by="time", ascending=True, inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info("Successfully loaded and standardized %d rows from CSV.", len(df))
        return df

    def _handle_missing_values(self, df: pd.DataFrame) -> None:
        """Applies configuration policies to resolve NaN / missing values."""
        cols_to_check = ["open", "high", "low", "close", "volume"]

        has_nans = df[cols_to_check].isna().any().any()
        if not has_nans:
            return

        policy = self.settings.MISSING_VALUE_POLICY
        if policy == "raise":
            # Will be verified in validate phase, raise now to be safe
            pass
        elif policy == "drop":
            before = len(df)
            df.dropna(subset=cols_to_check, inplace=True)
            dropped = before - len(df)
            logger.warning("Dropped %d rows containing missing columns.", dropped)
        elif policy == "fill":
            logger.warning("Filling missing value cells using ffill/bfill.")
            df[cols_to_check] = df[cols_to_check].ffill().bfill()

    def validate(self, df: pd.DataFrame) -> None:
        """Applies provider-specific validation logic.

        Args:
            df: Processed DataFrame to inspect.
        """
        # Validate that prices make physical sense (open, high, low, close > 0)
        price_cols = ["open", "high", "low", "close"]
        for col in price_cols:
            if (df[col] <= 0).any():
                from core.exceptions import InvalidNumericDataError
                raise InvalidNumericDataError(f"Column '{col}' contains negative or zero prices.")

    def info(self) -> dict[str, Any]:
        """Exposes metadata metrics about this CSV dataset.

        Returns:
            A metadata dict containing file settings and filepath.
        """
        return {
            "type": "CSV",
            "filepath": str(self.filepath),
            "delimiter": self.settings.CSV_DELIMITER,
            "timezone": self.settings.TIMEZONE,
        }
