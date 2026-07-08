"""Data Feed Outbound Port."""

from collections.abc import Callable
from typing import Protocol

from core.models import Bar, Timeframe


class IDataFeedPort(Protocol):
    """Outbound port for market data ingestion services."""

    def fetch_historical_bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        """Fetches historical bars buffer for a symbol and timeframe.

        Args:
            symbol: Target symbol asset.
            timeframe: Candle timeframe.
            count: Number of historical bars to retrieve.

        Returns:
            A list of Bar objects.
        """
        ...

    def stream_realtime_data(
        self, symbol: str, timeframe: Timeframe, callback: Callable[[Bar], None]
    ) -> None:
        """Subscribes to live bar updates, feeding them to a callback.

        Args:
            symbol: Target symbol asset.
            timeframe: Candle timeframe.
            callback: Execution handler when a new bar completes.
        """
        ...
