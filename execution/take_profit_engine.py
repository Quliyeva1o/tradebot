"""Swappable take-profit resolution for TradeManager.

TakeProfitEngine exists for the same reason as StopEngine
(execution/stop_engine.py) -- so a future, conditional Sprint 5 target
strategy could be dropped into TradeManager without any TradeManager code
change.

FixedTakeProfitEngine wraps strategy.risk_reward.resolve_stop_and_target(),
not calculate_take_profit(): calculate_take_profit() needs risk_dist/
risk_reward values that only exist inside a strategy's own evaluate() call,
before a TradeSetup is built -- they are not TradeSetup fields (TradeSetup
carries only the strategy's already-decided zones, per Sprint 3's design;
see strategy/models.py). resolve_stop_and_target() is the only
strategy/risk_reward.py function that actually operates on a TradeSetup, so
it is what a TradeSetup-driven engine wraps -- calculate_take_profit() is
one layer up, the function that originally produced the number now baked
into TradeSetup.target_zone, inside the strategy itself.
"""

from typing import Protocol, runtime_checkable

from strategy.models import TradeSetup
from strategy.risk_reward import resolve_stop_and_target


@runtime_checkable
class TakeProfitEngine(Protocol):
    """Protocol every take-profit resolution strategy must implement.

    Decouples TradeManager from any one way of deciding a target level --
    the same Dependency Inversion / Open-Closed role StopEngine plays for
    stop-loss resolution.
    """

    def resolve_take_profit(self, setup: TradeSetup) -> float:
        """Resolves the take-profit price to track an open trade against.

        Args:
            setup: The TradeSetup the trade was opened from.

        Returns:
            The take-profit price.
        """
        ...


class FixedTakeProfitEngine(TakeProfitEngine):
    """Thin adapter over strategy.risk_reward.resolve_stop_and_target().

    "Fixed" as in unconditional/static: the same target for the whole life
    of the trade -- the current (Sprint 1-3) behavior.
    """

    def resolve_take_profit(self, setup: TradeSetup) -> float:
        """Resolves setup's take-profit via resolve_stop_and_target(), verbatim.

        Args:
            setup: The TradeSetup the trade was opened from.

        Returns:
            resolve_stop_and_target(setup)[1] (the take_profit half).
        """
        _stop_loss, take_profit = resolve_stop_and_target(setup)
        return take_profit
