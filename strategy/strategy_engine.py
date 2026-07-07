"""Strategy engine router and event dispatcher."""

from core.interfaces import IDataFeed, IExecutionProvider, IStrategy


class StrategyEngine:
    """Coordinates active strategies, mapping and route feeding inputs."""

    def __init__(self, data_feed: IDataFeed, execution_provider: IExecutionProvider) -> None:
        """Initializes the StrategyEngine.

        Args:
            data_feed: Feeds historical/live pricing.
            execution_provider: Broker engine wrapper.
        """
        self.data_feed = data_feed
        self.execution_provider = execution_provider
        self.active_strategies: list[IStrategy] = []

    def register_strategy(self, strategy: IStrategy) -> None:
        """Adds a strategy to the engine.

        Args:
            strategy: Concrete implementation of IStrategy.
        """
        strategy.on_init(self.execution_provider, self.data_feed)
        self.active_strategies.append(strategy)
