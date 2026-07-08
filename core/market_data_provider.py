"""Abstract market data provider interface.

Defines the contract that all concrete data providers (e.g. CSV, MT5, REST, WS)
must implement.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from core.models import Bar


@runtime_checkable
class IMarketDataProvider(Protocol):
    """Protocol for fetching and validating market datasets."""

    def load(self) -> list[Bar]:
        """Loads raw market data from the source.

        Returns:
            A list of Bar objects representing the loaded raw market rates.
        """
        ...

    def validate(self, bars: Sequence[Bar]) -> None:
        """Performs source-specific validation checks on the loaded data.

        Args:
            bars: The sequence of Bar objects to validate.
        """
        ...

    def info(self) -> dict[str, Any]:
        """Provides metadata info about the provider state.

        Returns:
            A dictionary containing key/value details describing the provider.
        """
        ...
