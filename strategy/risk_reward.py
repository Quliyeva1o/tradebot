"""Shared risk/reward price-level calculations for trade-setup strategies and the execution layer.

calculate_take_profit is copied verbatim (identical arithmetic, identical
positive-risk/positive-risk_reward preconditions) from
NasdaqMidlineSweepStrategy.evaluate()'s original inline "Calculate
Entry/Stop/Target" block. Moved here so it is independently testable and
reusable by any strategy computing a fixed risk_reward-multiple target from
an already-known entry/stop, without duplicating the formula. The strategy
still owns risk_dist's non-positive-risk/non-positive-risk_reward rejection
checks (RejectionReason.NON_POSITIVE_RISK/RR_GATE_FAILED) -- those are
diagnostics-coupled and strategy-specific, so they were not moved; this
function assumes its caller already validated risk_dist/risk_reward are
strictly positive.

resolve_stop_and_target turns a TradeSetup's direction-aware stop_zone/
target_zone (a (low, high) price range) into the single scalar price level
on each side a trade is actually risked/targeted against -- the same
selection rule backtest/engine.py's pending-setup execution block already
uses (sl = zone_low if BUY else zone_high, same for target_zone). This is
what execution.trade_manager.TradeManager calls to translate a strategy's
TradeSetup into the OrderRequest.stop_loss/take_profit scalars a broker
needs, so a live/paper trade is tracked against the identical price level a
historical backtest run would have used for the same TradeSetup.
"""

from core.models import SignalDirection
from strategy.models import TradeSetup


def calculate_take_profit(
    entry: float,
    direction: SignalDirection,
    risk_dist: float,
    risk_reward: float,
) -> float:
    """Computes the take-profit price for a fixed risk_reward multiple.

    Args:
        entry: The trade's entry price.
        direction: BUY or SELL.
        risk_dist: The (already-validated strictly positive) distance from
            entry to stop_loss.
        risk_reward: Reward:risk multiple (e.g. 2.0 = target twice the risk
            distance beyond entry). Must already be validated strictly
            positive by the caller.

    Returns:
        entry + risk_dist * risk_reward for BUY, entry - risk_dist *
        risk_reward for SELL.
    """
    reward_dist = risk_dist * risk_reward
    return entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist


def resolve_stop_and_target(setup: TradeSetup) -> tuple[float, float]:
    """Resolves the scalar (stop_loss, take_profit) prices a TradeSetup is traded against.

    Args:
        setup: The TradeSetup to resolve.

    Returns:
        A (stop_loss, take_profit) pair: for BUY, the low edge of
        stop_zone/target_zone; for SELL, the high edge of each -- mirrors
        BacktestEngine.run()'s own pending-setup sl_price/tp_price selection.
    """
    sl_low, sl_high = setup.stop_zone
    tp_low, tp_high = setup.target_zone
    if setup.direction == SignalDirection.BUY:
        return sl_low, tp_low
    return sl_high, tp_high
