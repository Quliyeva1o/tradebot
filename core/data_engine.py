"""Enterprise Data Engine.

Acts as the central coordination layer that consumes IMarketDataProvider implementations,
runs structured check suite validations, and exposes sanitized dataframes.
"""

import pandas as pd

from core.market_data_provider import IMarketDataProvider
from utils.logging import setup_logger
from utils.validators import (
    validate_no_duplicate_timestamps,
    validate_no_missing_values,
    validate_non_empty,
    validate_numeric_data,
    validate_ordered_timestamps,
    validate_required_columns,
)

logger = setup_logger("data_engine")


class DataEngine:
    """Enterprise-grade coordinator driving ingestion, verification, and data delivery."""

    def __init__(self, provider: IMarketDataProvider) -> None:
        """Initializes the DataEngine with a concrete provider.

        Args:
            provider: Implementation of IMarketDataProvider.
        """
        self.provider = provider
        logger.info("DataEngine initialized with provider: %s", self.provider.info())

    def get_data(self) -> pd.DataFrame:
        """Triggers the provider load sequence and runs full framework validation checks.

        Returns:
            A sanitized, validated Pandas DataFrame containing standard OHLCV columns.

        Raises:
            DataValidationError: If any of the structure or schema validation checks fail.
        """
        info = self.provider.info()
        logger.info(
            "Requesting dataset load from provider [Type: %s]...",
            info.get("type", "Unknown"),
        )


        try:
            # 1. Load data from the provider
            df = self.provider.load()
        except Exception as e:
            logger.error("Provider failed to load raw data: %s", e, exc_info=True)
            raise

        logger.info("Raw data loaded successfully. Shape: %s. Initiating validations...", df.shape)

        try:
            # 2. Perform framework validation checks
            validate_non_empty(df)

            required_cols = ["time", "open", "high", "low", "close", "volume"]
            validate_required_columns(df, required_cols)

            numeric_cols = ["open", "high", "low", "close", "volume"]
            validate_numeric_data(df, numeric_cols)

            validate_no_missing_values(df, numeric_cols)
            validate_no_duplicate_timestamps(df)
            validate_ordered_timestamps(df)

            # 3. Perform provider-specific validations
            self.provider.validate(df)

        except Exception as e:
            logger.error("Data validation check failed: %s", e)
            raise

        logger.info("Data validations passed. Clean dataset ready for framework consumption.")
        return df
