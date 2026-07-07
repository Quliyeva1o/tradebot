"""Abstract market data provider interface.

Defines the contract that all concrete data providers (e.g. CSV, MT5, REST, WS)
must implement.
"""

from typing import Any, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class IMarketDataProvider(Protocol):
    """Protocol for fetching and validating market datasets."""

    def load(self) -> pd.DataFrame:
        """Loads raw market data from the source.

        Returns:
            A pandas DataFrame representing the loaded raw market rates.
        """
        ...

    def validate(self, df: pd.DataFrame) -> None:
        """Performs source-specific validation checks on the loaded data.

        Args:
            df: The pandas DataFrame to validate.
        """
        ...

    def info(self) -> dict[str, Any]:
        """Provides metadata info about the provider state.

        Returns:
            A dictionary containing key/value details describing the provider.
        """
        ...
