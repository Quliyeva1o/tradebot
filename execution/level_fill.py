"""How a resting limit order, a stop or a target fills on a single bar.

Shared by PaperBroker's level fills and the First FVG window backtest, so a paper
trade and its backtest twin are priced by the same rules and can be compared exactly.

Bars are bid bars; spread is charged separately, in R, by whoever reports the trade.
"""

from __future__ import annotations

from core.models import Bar, OrderType, SignalDirection


def limit_fill(order_type: OrderType, price: float, bar: Bar) -> float | None:
    """The price a working limit fills at on `bar`, or None if the bar never reaches it.

    A bar that opens through the limit fills at its open, which is the better price.
    """
    if order_type == OrderType.BUY_LIMIT:
        return None if bar.low > price else min(price, bar.open)
    if order_type == OrderType.SELL_LIMIT:
        return None if bar.high < price else max(price, bar.open)
    raise ValueError(f"limit_fill only prices limit orders, got {order_type!r}")


def exit_fill(
    direction: SignalDirection,
    stop: float,
    target: float,
    bar: Bar,
    *,
    entry_bar: bool,
    after_break: bool,
) -> tuple[float, str] | None:
    """(price, "SL" | "TP") if `bar` closes the trade, else None.

    - A bar opening beyond a level fills at its open. Not on the entry bar: that open
      printed before the entry filled.
    - A bar touching both levels is a stop (the conservative reading).
    - The first bar after a trading break often opens on a stale re-quote of the
      pre-break close, so a stop it crosses fills at its close when that is worse.
    """
    long = direction == SignalDirection.BUY
    if not entry_bar:
        if (bar.open <= stop) if long else (bar.open >= stop):
            return bar.open, "SL"
        if (bar.open >= target) if long else (bar.open <= target):
            return bar.open, "TP"
    if (bar.low <= stop) if long else (bar.high >= stop):
        if after_break and ((bar.close < stop) if long else (bar.close > stop)):
            return bar.close, "SL"
        return stop, "SL"
    if (bar.high >= target) if long else (bar.low <= target):
        return target, "TP"
    return None
