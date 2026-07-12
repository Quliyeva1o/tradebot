"""Interfaces and protocols for strategy modules."""

from typing import Protocol, runtime_checkable

from core.models import Timeframe
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

    def reset(self) -> None:
        """Resets the internal state of the strategy (e.g. proposed keys)."""
        ...

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """Optional hint for BacktestConfig.max_holding_bars.

        Data-independent default is None (no recommendation -- unlimited
        holding, i.e. no behavior change for strategies that don't override
        this or aren't configured to). Session-scoped strategies may override
        this to derive a bar count from their own configured session window,
        but only when explicitly opted into via a constructor parameter --
        never from a hardcoded wall-clock assumption, since the traded
        instrument's actual hours (CFD/index/metals vs. classic cash-market
        hours) can't be known without inspecting the real market data.
        """
        return None
