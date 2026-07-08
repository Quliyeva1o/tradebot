"""Interfaces and protocols for strategy modules."""

from typing import Protocol, runtime_checkable

from market_structure.structure_models import MarketState
from strategy.models import TradeSetup


@runtime_checkable
class TradeSetupStrategy(Protocol):
    """Protocol that every independent trading strategy must implement.

    Adhering to this interface ensures compliance with the Dependency Inversion
    and Open-Closed principles.
    """

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the given MarketState and returns a TradeSetup if rules pass.

        Args:
            market_state: The read-only MarketState domain aggregate.

        Returns:
            A TradeSetup candidate if conditions are met, otherwise None.
        """
        ...
