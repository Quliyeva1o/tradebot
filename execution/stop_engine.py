"""Swappable stop-loss resolution for TradeManager.

StopEngine exists so a future, conditional Sprint 5 (TrailingStopEngine/
BreakEvenStopEngine -- NOT built this sprint, see execution/trade_manager.py
and the Sprint 4 deliverable notes) can be dropped into TradeManager without
any TradeManager code change: TradeManager calls StopEngine.resolve_stop(setup),
never strategy/risk_reward.py directly.

FixedStopEngine is the only implementation this sprint: a thin adapter over
strategy.risk_reward.resolve_stop_and_target(), used verbatim -- no
reimplementation. Its resolve_stop(setup) -> float shape mirrors
resolve_stop_and_target()'s existing (TradeSetup) -> (stop_loss, take_profit)
shape (just the stop_loss half), since that is the one strategy/risk_reward.py
function that actually operates on a TradeSetup -- this sprint is a pure
interface extraction, not new logic, so no new calculation shape was invented.
"""

from typing import Protocol, runtime_checkable

from strategy.models import TradeSetup
from strategy.risk_reward import resolve_stop_and_target


@runtime_checkable
class StopEngine(Protocol):
    """Protocol every stop-loss resolution strategy must implement.

    Decouples TradeManager from any one way of deciding a stop level -- the
    same Dependency Inversion / Open-Closed role IBroker and
    TradeSetupStrategy play elsewhere in this codebase.
    """

    def resolve_stop(self, setup: TradeSetup) -> float:
        """Resolves the stop-loss price to track an open trade against.

        Args:
            setup: The TradeSetup the trade was opened from.

        Returns:
            The stop-loss price.
        """
        ...


class FixedStopEngine(StopEngine):
    """Thin adapter over strategy.risk_reward.resolve_stop_and_target().

    "Fixed" as in unconditional/static: the same stop level for the whole
    life of the trade -- the current (Sprint 1-3) behavior, as opposed to a
    future, conditional Sprint 5 TrailingStopEngine that would move it
    bar-by-bar.
    """

    def resolve_stop(self, setup: TradeSetup) -> float:
        """Resolves setup's stop-loss via resolve_stop_and_target(), verbatim.

        Args:
            setup: The TradeSetup the trade was opened from.

        Returns:
            resolve_stop_and_target(setup)[0] (the stop_loss half).
        """
        stop_loss, _take_profit = resolve_stop_and_target(setup)
        return stop_loss
