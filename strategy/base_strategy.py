"""Base Strategy definition module."""

from core.interfaces import IDataFeed, IExecutionProvider, IStrategy
from core.models import Bar, Tick, Timeframe


class BaseStrategy(IStrategy):
    """Abstract base class that trading strategies will extend."""

    def __init__(self) -> None:
        """Initializes the BaseStrategy settings."""
        self.broker: IExecutionProvider | None = None
        self.data_feed: IDataFeed | None = None

    def on_init(self, execution_provider: IExecutionProvider, data_feed: IDataFeed) -> None:
        """Initializes dependencies.

        Args:
            execution_provider: Broker interface.
            data_feed: Market data provider.
        """
        self.broker = execution_provider
        self.data_feed = data_feed

    def on_bar(self, bar: Bar, symbol: str, timeframe: Timeframe) -> None:
        """Executed when a new bar completes.

        Args:
            bar: Completed bar structure.
            symbol: Target symbol asset.
            timeframe: Candle timeframe.
        """
        pass

    def on_tick(self, tick: Tick, symbol: str) -> None:
        """Executed when a new price update tick occurs.

        Args:
            tick: Active price tick details.
            symbol: Target symbol asset.
        """
        pass

    def on_deinit(self) -> None:
        """Lifecycle shutdown handler."""
        pass
