"""Stop-and-reverse: while a bot's trade is open, an opposite pending stop order rests at its stop.

When the stop is hit the broker fills both on the same tick -- the trade closes and the reverse
trade opens at the same price -- so no poll timing is involved. The reverse trade trades the same
lots with its stop back at the original entry (the original's entry-to-stop distance) and its
target `reward_r` times that distance. It is never reversed again.

The bot only polls every two minutes, so the order is kept in step at each poll:
  - one of the bot's own trades open, no matching resting order -> place it
  - a resting order for any other trade, or the bot has no trade open (the trade reached its
    target, was closed by hand or by the broker) -> cancel it
  - the reverse trade itself is open -> nothing rests
A resting order is also cancelled while trading is halted: it is a new entry waiting to happen.

Needs a HEDGING account (FundingPips-Trial reports margin_mode 2): on a netting account the
pending order would close the original position instead of opening a second one. PaperBroker
cannot fill pending stop orders, so this is Demo only.

Chosen 2026-09-17 by the user at 0.5R, against the backtest shown to them (live replay, six Demo
bots, reverse filled at the stop's own fill price, one position per symbol): reversal-only PF 1.23
(+3.4R) in the month to 2026-09-17, but 0.74 (-49R) over the last 12 months and 0.87 (-103R) over
6.7 years.
"""

from __future__ import annotations

from collections.abc import Callable

from core.models import OrderType
from execution.interfaces import IBroker
from execution.models import OrderRequest, Position
from execution.mt5_broker import _MT5_COMMENT_MAX_LENGTH

REVERSE_MARKER = "_sar"


def reverse_tag(strategy_tag: str) -> str:
    """The comment prefix of a bot's reverse orders -- and of the positions they open.

    It still starts with the bot's STRATEGY_TAG, so an open reverse trade counts as the bot's own
    position: it is managed like any other and blocks a new entry until it closes.
    """
    return f"{strategy_tag}{REVERSE_MARKER}"


def is_reverse(comment: str, strategy_tag: str) -> bool:
    return comment.startswith(reverse_tag(strategy_tag))


def reverse_comment(strategy_tag: str, position_id: str) -> str:
    """Names the trade a reverse order belongs to, within MT5's comment limit.

    Only the ticket's trailing digits fit, which is enough: tickets are sequential, so two live
    trades sharing them are a million orders apart.
    """
    base = reverse_tag(strategy_tag)
    room = _MT5_COMMENT_MAX_LENGTH - len(base)
    if room <= 0:
        raise ValueError(f"strategy tag {strategy_tag!r} leaves no room for a ticket in the comment")
    return f"{base}{position_id[-room:]}"


def _to_tick(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 10)


def plan_reverse_order(position: Position, reward_r: float, tick_size: float,
                       strategy_tag: str) -> OrderRequest | None:
    """The pending order that reverses `position` at its stop, or None when it has no stop."""
    if position.stop_loss is None or reward_r <= 0:
        return None
    distance = abs(position.open_price - position.stop_loss)
    if distance <= 0:
        return None
    price = position.stop_loss
    if position.order_type == OrderType.BUY_MARKET:
        order_type, stop, target = OrderType.SELL_STOP, price + distance, price - reward_r * distance
    else:
        order_type, stop, target = OrderType.BUY_STOP, price - distance, price + reward_r * distance
    return OrderRequest(
        symbol=position.symbol, order_type=order_type, volume=position.volume,
        price=_to_tick(price, tick_size), stop_loss=_to_tick(stop, tick_size),
        take_profit=_to_tick(target, tick_size), comment=reverse_comment(strategy_tag, position.id),
    )


def sync_reverse_order(
    broker: IBroker,
    symbol: str,
    strategy_tag: str,
    mine: list[Position],
    reward_r: float,
    log_event: Callable[..., None],
    halted: bool = False,
) -> None:
    """Brings the bot's resting reverse order in line with its open trade -- see module docstring.

    Args:
        mine: This bot's open positions on `symbol` (the runner's _partition_positions).
        log_event: The runner's _log_trade_event.
        halted: True while the kill-switch is active; nothing may rest then.
    """
    tag = reverse_tag(strategy_tag)
    resting = [o for o in broker.get_pending_orders(symbol) if o.comment.startswith(tag)]

    wanted: OrderRequest | None = None
    if not halted and len(mine) == 1 and not is_reverse(mine[0].comment, strategy_tag):
        tick_size = broker.get_symbol_constraints(symbol).tick_size
        wanted = plan_reverse_order(mine[0], reward_r, tick_size, strategy_tag)
        if wanted is None:
            log_event("reverse_order_unplannable", symbol=symbol, position_id=mine[0].id)

    kept = False
    for order in resting:
        if wanted is not None and not kept and order.comment == wanted.comment:
            kept = True
            continue
        canceled = broker.cancel_order(order.id)
        log_event("reverse_order_canceled" if canceled else "reverse_order_cancel_failed",
                  symbol=symbol, order_id=order.id, comment=order.comment, halted=halted)

    if wanted is None or kept:
        return
    result = broker.place_order(wanted)
    log_event("reverse_order_placed" if result.success else "reverse_order_rejected",
              symbol=symbol, position_id=mine[0].id, order_id=result.order_id,
              order_type=wanted.order_type.name, price=wanted.price, stop_loss=wanted.stop_loss,
              take_profit=wanted.take_profit, volume=wanted.volume, retcode=result.retcode,
              reason=result.comment)
