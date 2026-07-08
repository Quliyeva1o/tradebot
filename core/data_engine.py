"""Enterprise Data Engine.

Acts as the central coordination layer that consumes IMarketDataProvider implementations,
runs structured check suite validations, and exposes sanitized bar sequences.
"""

from core.exceptions import (
    DataValidationError,
    DuplicateTimestampError,
    EmptyDataError,
    InvalidNumericDataError,
    InvalidTimestampError,
)
from core.market_data_provider import IMarketDataProvider
from core.models import Bar
from utils.logging import setup_logger
from utils.validators import (
    validate_no_duplicate_timestamps,
    validate_no_missing_values,
    validate_non_empty,
    validate_numeric_data,
    validate_ordered_timestamps,
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

    def get_data(self) -> list[Bar]:
        """Triggers the provider load sequence and runs full framework validation checks.

        Returns:
            A sanitized, validated list of Bar objects.

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
            bars = self.provider.load()
        except Exception as e:
            logger.error("Provider failed to load raw data: %s", e, exc_info=True)
            raise

        logger.info("Raw data loaded successfully. Count: %d. Initiating validations...", len(bars))

        try:
            # 2. Perform framework validation checks
            try:
                validate_non_empty(bars)
            except ValueError as e:
                logger.error("Data validation check failed: Empty dataset.")
                raise EmptyDataError() from e

            try:
                validate_numeric_data(bars)
            except ValueError as e:
                logger.error("Data validation check failed: Invalid numeric values.")
                raise InvalidNumericDataError(str(e)) from e

            try:
                validate_no_missing_values(bars)
            except ValueError as e:
                logger.error("Data validation check failed: Missing values in numeric columns.")
                raise InvalidNumericDataError(str(e)) from e

            try:
                validate_no_duplicate_timestamps(bars)
            except ValueError as e:
                logger.error("Data validation check failed: Duplicate timestamps.")
                raise DuplicateTimestampError(str(e)) from e

            try:
                validate_ordered_timestamps(bars)
            except ValueError as e:
                logger.error("Data validation check failed: Unordered timestamps.")
                raise InvalidTimestampError(str(e)) from e

            # 3. Perform provider-specific validations
            self.provider.validate(bars)

        except Exception as e:
            if not isinstance(e, DataValidationError):
                logger.error("Data validation check failed: Unknown error: %s", e)
            raise

        logger.info("Data validations passed. Clean dataset ready for framework consumption.")
        return bars
