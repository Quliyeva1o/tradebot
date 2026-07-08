"""Strategy package.

Defines base classes for trading algorithms, engines driving strategy logic,
and concrete strategy modules for SMC setups.
"""

from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup
from strategy.strategy_engine import StrategyEngine

__all__ = [
    "BearishContinuationStrategy",
    "BullishContinuationStrategy",
    "StrategyEngine",
    "TradeSetup",
    "TradeSetupStrategy",
]
