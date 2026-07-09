"""Strategy engine orchestrator module."""

from market_structure.structure_models import MarketState
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


class StrategyEngine:
    """Coordinates and executes registered strategy modules.

    Acts as a pure orchestrator. It does not perform any structural calculations
    or filtering, complying with the Open-Closed and Single Responsibility principles.
    """

    def __init__(self) -> None:
        """Initializes the StrategyEngine with an empty list of strategies."""
        self.strategies: list[TradeSetupStrategy] = []

    def register_strategy(self, strategy: TradeSetupStrategy) -> None:
        """Registers a trading strategy module.

        Args:
            strategy: A concrete implementation of TradeSetupStrategy.
        """
        self.strategies.append(strategy)

    def run(self, market_state: MarketState) -> list[TradeSetup]:
        """Executes all registered strategies against the given MarketState.

        Args:
            market_state: The read-only MarketState domain aggregate.

        Returns:
            A list of generated TradeSetup candidates.
        """
        setups: list[TradeSetup] = []
        for strategy in self.strategies:
            setup = strategy.evaluate(market_state)
            if setup is not None:
                setups.append(setup)
        return setups

    def reset(self) -> None:
        """Resets all registered strategies."""
        for strategy in self.strategies:
            if hasattr(strategy, "reset"):
                strategy.reset()

    def get_diagnostics(self) -> dict[str, dict]:
        """Aggregates rejection diagnostics from all registered strategies.

        Strategies without a `diagnostics` attribute are silently skipped, so
        this stays optional/duck-typed rather than part of the strategy
        Protocol.

        Returns:
            Mapping of "{registration index}_{class name}" to that
            strategy's diagnostics summary (see StrategyDiagnostics.summary).
        """
        result: dict[str, dict] = {}
        for idx, strategy in enumerate(self.strategies):
            diagnostics = getattr(strategy, "diagnostics", None)
            if diagnostics is not None:
                result[f"{idx}_{strategy.__class__.__name__}"] = diagnostics.summary()
        return result

