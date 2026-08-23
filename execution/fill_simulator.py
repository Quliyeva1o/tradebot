"""Isolated fill-price simulation for paper-traded market orders.

Mirrors backtest/engine.py's spread/slippage convention exactly (see
BacktestConfig.spread's docstring in backtest/models.py): half the spread is
applied on entry, half on exit, so a full round-trip costs exactly one
spread width; slippage is applied in full on each leg independently. This
module implements only the entry side of that convention -- Sprint 2 covers
placing orders (PENDING -> FILLED/REJECTED/CANCELLED, see execution/order.py),
not managing/closing positions, so there is no exit-price counterpart here.

The same worse-price-for-the-trader direction as
BacktestEngine.run()'s pending-setup entry-price calculation is used: a BUY
pays more (price + half spread + slippage), a SELL receives less (price -
half spread - slippage).

Kept free of any IBroker/broker-class/MT5 dependency (a pure function over
primitive values) specifically so it is unit-testable in isolation and its
math can be cross-checked directly against backtest/engine.py's formula in
tests, without mocking MT5 or a broker.
"""

from core.models import OrderType

_MARKET_ORDER_TYPES = (OrderType.BUY_MARKET, OrderType.SELL_MARKET)


def simulate_market_fill(
    order_type: OrderType,
    next_bar_open: float,
    spread: float,
    slippage: float,
) -> float:
    """Computes the simulated fill price for a market order.

    Args:
        order_type: OrderType.BUY_MARKET or OrderType.SELL_MARKET.
        next_bar_open: The open price of the bar the order fills against
            (the next available real price after the order was placed).
        spread: Full bid/ask spread in price units (Ask - Bid); half is
            applied here, mirroring BacktestConfig.spread's convention.
        slippage: Price units of adverse slippage, applied in full.

    Returns:
        The simulated fill price.

    Raises:
        ValueError: If order_type is not a market order type (BUY_MARKET/
            SELL_MARKET) -- pending order types have no fill model here,
            since they are not filled synchronously by PaperBroker.
    """
    if order_type not in _MARKET_ORDER_TYPES:
        raise ValueError(
            f"simulate_market_fill only supports market order types "
            f"(BUY_MARKET/SELL_MARKET), got {order_type!r}."
        )

    if order_type == OrderType.BUY_MARKET:
        return next_bar_open + spread / 2 + slippage
    return next_bar_open - spread / 2 - slippage
