"""Structured order/fill event logging for the execution layer (Sprint 6c, T2).

Every fill (PaperBroker or MT5Broker, place_order/close_position) logs a
structured JSON event recording the intended price (what the strategy/
TradeManager expected, or the pre-send broker quote) vs. the actual fill
price, with the difference computed as realized slippage -- the exact
number Sprint 7's demo run needs to compare against Sprint 6b's robustness-
test assumption ($6.03/trade average). Additive only: existing human-
readable logger.info()/logger.error() calls in PaperBroker/MT5Broker are
untouched; log_fill() is called alongside them, not instead of them.
"""

from core.models import OrderType
from utils.logging import setup_structured_logger

_execution_logger = setup_structured_logger("execution_events")

_BUY_SIDE_ORDER_TYPES = (OrderType.BUY_MARKET, OrderType.BUY_LIMIT, OrderType.BUY_STOP)


def log_fill(
    *,
    broker: str,
    event: str,
    order_id: str,
    symbol: str,
    order_type: OrderType,
    volume: float,
    intended_price: float | None,
    actual_price: float,
) -> float | None:
    """Logs a structured fill event and computes realized slippage cost.

    Args:
        broker: Which IBroker implementation produced this fill (e.g.
            "PaperBroker", "MT5Broker").
        event: "open" for a place_order() fill, "close" for a
            close_position() fill.
        order_id: The order/position id this fill belongs to.
        symbol: Trading instrument symbol.
        order_type: The filled order's OrderType (BUY_MARKET, SELL_MARKET, ...).
        volume: Filled volume.
        intended_price: The price the strategy/TradeManager (or the broker's
            own pre-send quote) expected. None if no intended price was
            available to compare against -- logged explicitly as null, not
            silently skipped, so a gap in the data is visible rather than
            indistinguishable from "zero slippage."
        actual_price: The price the fill actually executed at.

    Returns:
        realized_slippage_cost: the adverse price move (direction-aware --
            paying more on a buy-side fill or receiving less on a sell-side
            fill both count as positive/costly, matching
            execution/fill_simulator.py's own BUY/SELL sign convention)
            converted to account-currency terms via `* volume`, directly
            comparable to Sprint 6b's per-trade dollar slippage figures.
            None if intended_price was None.
    """
    if intended_price is None:
        adverse_price_delta = None
        realized_slippage_cost = None
    else:
        is_buy_side = order_type in _BUY_SIDE_ORDER_TYPES
        adverse_price_delta = (
            (actual_price - intended_price) if is_buy_side else (intended_price - actual_price)
        )
        realized_slippage_cost = adverse_price_delta * volume

    _execution_logger.info(
        {
            "event_type": "fill",
            "broker": broker,
            "fill_event": event,
            "order_id": order_id,
            "symbol": symbol,
            "order_type": order_type.name,
            "volume": volume,
            "intended_price": intended_price,
            "actual_price": actual_price,
            "adverse_price_delta": adverse_price_delta,
            "realized_slippage_cost": realized_slippage_cost,
        }
    )
    return realized_slippage_cost
