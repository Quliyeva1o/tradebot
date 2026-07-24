"""MT5 implementation of the IBroker execution interface."""

import hashlib
import time
from datetime import UTC, datetime

import MetaTrader5 as mt5  # noqa: N813

from core.models import AccountInfo, OrderType
from core.validation import require_non_negative, require_positive
from execution.event_log import log_fill
from execution.interfaces import IBroker
from execution.models import OrderRequest, OrderResult, Position
from mt5.connector import MT5Connector
from risk.kill_switch import activate_kill_switch
from utils.logging import setup_logger

logger = setup_logger("mt5_broker")

# MT5's order-comment field is rejected by order_send()/order_check()
# (retcode None, error (-2, 'Invalid "comment" argument')) past a hard
# length limit. This is NOT the commonly-cited 31 characters -- a first fix
# assumed 31 and still lost two real trades to this exact error, because 31
# itself is already past the real cutoff. Empirically confirmed against the
# real demo account via mt5.order_check() (validate-only, no order placed;
# MetaTrader5 package 5.0.5735, USTEC, 2026-07-24): a 29-character comment is
# accepted (retcode 10017 "Trade disabled" -- a business decline, proving
# the comment itself passed validation); a 30-character comment reproduces
# the exact same "(-2, 'Invalid "comment" argument')" order_check()/
# order_send() failure as the production crash logs. 29 is therefore used
# here as the confirmed-safe maximum, not another guess.
#
# TradeManager.open_trade() passes the full TradeSetup.setup_id as the
# comment (e.g. "setup_midline_sweep_USTEC_M5_SELL_0c30fdc3_20260724_
# 143000_000000", 65 chars), which is always over this limit, so every real
# order placed through MT5Broker failed before order_send() could even
# evaluate the rest of the request. The full setup_id remains traceable
# without it: run_live_demo.py logs setup_id alongside the resulting
# order_id to trade_events.log, and log_fill() below logs that same
# order_id to execution_events.log, so the two logs join on order_id.
_MT5_COMMENT_MAX_LENGTH = 29


def _mt5_comment(comment: str) -> str:
    """Shrinks `comment` to fit MT5's order-comment length limit (see constant above).

    Short comments pass through untouched. Longer ones are shortened to a
    readable prefix plus a short content hash (rather than a plain
    truncation), so two long comments sharing the same prefix -- e.g. two
    setup_ids from the same symbol/strategy/direction that only differ in
    their trailing timestamp -- don't collide into the same MT5 comment.

    Args:
        comment: The caller-supplied comment (e.g. OrderRequest.comment).

    Returns:
        `comment` unchanged if it already fits; otherwise a truncated
        prefix + "_" + 8-hex-char sha256 digest of the full string, sized
        to fit within _MT5_COMMENT_MAX_LENGTH.
    """
    if len(comment) <= _MT5_COMMENT_MAX_LENGTH:
        return comment
    digest = hashlib.sha256(comment.encode()).hexdigest()[:8]
    prefix_length = _MT5_COMMENT_MAX_LENGTH - len(digest) - 1
    return f"{comment[:prefix_length]}_{digest}"

_ORDER_TYPE_MAP: dict[OrderType, int] = {
    OrderType.BUY_MARKET: mt5.ORDER_TYPE_BUY,
    OrderType.SELL_MARKET: mt5.ORDER_TYPE_SELL,
    OrderType.BUY_LIMIT: mt5.ORDER_TYPE_BUY_LIMIT,
    OrderType.SELL_LIMIT: mt5.ORDER_TYPE_SELL_LIMIT,
    OrderType.BUY_STOP: mt5.ORDER_TYPE_BUY_STOP,
    OrderType.SELL_STOP: mt5.ORDER_TYPE_SELL_STOP,
}

_MARKET_ORDER_TYPES = (OrderType.BUY_MARKET, OrderType.SELL_MARKET)

# Retcodes that represent the venue accepting the request: DONE/DONE_PARTIAL
# for an immediately-executed market order, PLACED for an accepted pending order.
_SUCCESS_RETCODES = frozenset(
    {
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_DONE_PARTIAL,
        mt5.TRADE_RETCODE_PLACED,
    }
)


class MT5Broker(IBroker):
    """Adapts MT5Connector to the IBroker execution interface.

    Wraps an MT5Connector instance by composition rather than inheriting
    from it: connection and account-info reads delegate to the connector's
    existing, already-tested implementations, while order
    placement/cancellation/position lookups are implemented here using the
    same mt5 module calls and error-handling conventions MT5Connector
    itself uses. mt5/connector.py is intentionally left unmodified.
    """

    def __init__(
        self,
        connector: MT5Connector | None = None,
        max_reconnect_attempts: int = 5,
        initial_backoff_seconds: float = 1.0,
        backoff_multiplier: float = 2.0,
    ) -> None:
        """Initializes the MT5Broker.

        Args:
            connector: The MT5Connector to delegate connection/account-info
                reads to. Defaults to a new MT5Connector() instance;
                overridable (e.g. with a test double) via injection.
            max_reconnect_attempts: Maximum consecutive connect() attempts
                (T4, Sprint 6c) before giving up and activating the
                kill-switch. The first attempt counts toward this total.
            initial_backoff_seconds: Delay before the second attempt; grows
                by backoff_multiplier after each subsequent failure.
            backoff_multiplier: Growth factor applied to the backoff delay
                after each failed attempt.

        Raises:
            ValueError: If max_reconnect_attempts or initial_backoff_seconds
                is not strictly positive, or backoff_multiplier is negative.
        """
        require_positive(max_reconnect_attempts, "max_reconnect_attempts")
        require_positive(initial_backoff_seconds, "initial_backoff_seconds")
        require_non_negative(backoff_multiplier, "backoff_multiplier")
        self._connector = connector if connector is not None else MT5Connector()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._initial_backoff_seconds = initial_backoff_seconds
        self._backoff_multiplier = backoff_multiplier

    def connect(self) -> bool:
        """Connects to MT5, retrying with exponential backoff on failure (T4).

        Doubles as both "initial connect" and "reconnect after a detected
        connection loss" -- a future live loop calls this same method again
        when it notices the connection is down; the retry/backoff/kill-switch
        behavior is identical either way, since MT5Connector.connect() itself
        does not distinguish "never connected" from "was connected, now isn't".

        Returns:
            True as soon as MT5Connector.connect() succeeds (on the first
            attempt or any retry). False only after max_reconnect_attempts
            consecutive failures.

        Raises:
            Nothing -- if every attempt fails, the kill-switch
            (risk.kill_switch.activate_kill_switch) is activated instead of
            leaving the caller to decide what "connection permanently down"
            means; an ambiguous half-connected state is exactly what a
            kill-switch exists to short-circuit. activate_kill_switch()
            itself never raises (best-effort Telegram alert, fail-open flag
            write) -- see risk/kill_switch.py.
        """
        backoff = self._initial_backoff_seconds
        for attempt in range(1, self._max_reconnect_attempts + 1):
            if self._connector.connect():
                if attempt > 1:
                    logger.info(
                        "MT5 reconnected successfully on attempt %d/%d.",
                        attempt,
                        self._max_reconnect_attempts,
                    )
                return True

            logger.warning("MT5 connect attempt %d/%d failed.", attempt, self._max_reconnect_attempts)
            if attempt < self._max_reconnect_attempts:
                time.sleep(backoff)
                backoff *= self._backoff_multiplier

        reason = f"MT5Broker could not connect to MT5 after {self._max_reconnect_attempts} attempts."
        logger.error(reason)
        activate_kill_switch(reason)
        return False

    def get_account_info(self) -> AccountInfo:
        """Delegates to MT5Connector.fetch_account_info().

        Returns:
            An AccountInfo snapshot of the connected account.

        Raises:
            RuntimeError: If MT5Connector.fetch_account_info() raises (e.g.
                mt5.account_info() returned None).
        """
        return self._connector.fetch_account_info()

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submits an order to MT5 via mt5.order_send().

        Raises RuntimeError when the MT5 API itself is unusable (symbol
        unavailable, no tick data, order_send returns None) -- mirroring
        MT5Connector's fetch_*() precedent for infrastructure failures.
        Returns OrderResult(success=False, ...) when MT5 responds but
        declines the trade (e.g. no money, invalid stops): that is a normal
        business outcome for the caller to handle, not a system fault.

        Args:
            order: The order to submit.

        Returns:
            An OrderResult describing whether the venue accepted the order.

        Raises:
            RuntimeError: If the symbol cannot be selected, tick data is
                unavailable for a market order needing a live price, or
                mt5.order_send() returns None.
            ValueError: If order.order_type is a pending order type
                (*_LIMIT/*_STOP) and order.price is not set.
        """
        if not mt5.symbol_select(order.symbol, True):
            raise RuntimeError(f"Symbol {order.symbol} is not available in the MT5 terminal.")

        mt5_type = _ORDER_TYPE_MAP[order.order_type]
        is_market = order.order_type in _MARKET_ORDER_TYPES

        price = order.price
        if price is None:
            if not is_market:
                raise ValueError(f"price is required for pending order type {order.order_type!r}")
            tick = mt5.symbol_info_tick(order.symbol)
            if tick is None:
                raise RuntimeError(
                    f"mt5.symbol_info_tick() returned None for {order.symbol}. "
                    f"Error code: {mt5.last_error()}"
                )
            price = tick.ask if order.order_type == OrderType.BUY_MARKET else tick.bid

        request: dict[str, object] = {
            "action": mt5.TRADE_ACTION_DEAL if is_market else mt5.TRADE_ACTION_PENDING,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": mt5_type,
            "price": price,
            "deviation": order.deviation,
        }
        if order.stop_loss is not None:
            request["sl"] = order.stop_loss
        if order.take_profit is not None:
            request["tp"] = order.take_profit
        if order.comment:
            request["comment"] = _mt5_comment(order.comment)

        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"mt5.order_send() returned None. Error code: {mt5.last_error()}")

        success = result.retcode in _SUCCESS_RETCODES
        if success:
            logger.info(
                "Order placed for %s: ticket=%s price=%s", order.symbol, result.order, result.price
            )
            log_fill(
                broker="MT5Broker",
                event="open",
                order_id=str(result.order),
                symbol=order.symbol,
                order_type=order.order_type,
                volume=result.volume,
                intended_price=price,
                actual_price=result.price,
            )
        else:
            logger.error(
                "Order rejected for %s: retcode=%s comment=%s",
                order.symbol,
                result.retcode,
                result.comment,
            )

        return OrderResult(
            success=success,
            order_id=str(result.order),
            position_id=str(result.order),
            price=result.price,
            volume=result.volume,
            retcode=result.retcode,
            comment=result.comment or "",
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order via mt5.order_send(TRADE_ACTION_REMOVE).

        Args:
            order_id: The MT5 order ticket, as a string.

        Returns:
            True if the order was canceled, False if MT5 declined the
            cancellation (e.g. the order no longer exists).

        Raises:
            ValueError: If order_id is not a valid integer ticket.
            RuntimeError: If mt5.order_send() returns None.
        """
        try:
            ticket = int(order_id)
        except ValueError as exc:
            raise ValueError(f"order_id must be a valid integer ticket, got {order_id!r}") from exc

        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
        if result is None:
            raise RuntimeError(
                f"mt5.order_send() (cancel) returned None for order {ticket}. "
                f"Error code: {mt5.last_error()}"
            )

        success: bool = result.retcode == mt5.TRADE_RETCODE_DONE
        if success:
            logger.info("Canceled order %d", ticket)
        else:
            logger.error(
                "Failed to cancel order %d: retcode=%s comment=%s",
                ticket,
                result.retcode,
                result.comment,
            )
        return success

    def close_position(self, position_id: str) -> OrderResult:
        """Closes an open position via an opposite-direction mt5.order_send(TRADE_ACTION_DEAL).

        Mirrors the MetaTrader5 package's own Close() convenience helper:
        an opposite-direction market order carrying "position": ticket in
        the request, which MT5 nets against the existing position instead
        of opening a new one.

        Args:
            position_id: The MT5 position ticket, as a string.

        Returns:
            An OrderResult describing whether the venue accepted the close.

        Raises:
            ValueError: If position_id is not a valid integer ticket.
            RuntimeError: If mt5.positions_get() returns None, no open
                position exists for position_id, tick data is unavailable,
                or mt5.order_send() returns None.
        """
        try:
            ticket = int(position_id)
        except ValueError as exc:
            raise ValueError(f"position_id must be a valid integer ticket, got {position_id!r}") from exc

        positions = mt5.positions_get(ticket=ticket)
        if positions is None:
            raise RuntimeError(f"mt5.positions_get() returned None. Error code: {mt5.last_error()}")
        if len(positions) == 0:
            raise RuntimeError(f"No open position found for ticket {ticket}.")
        position = positions[0]

        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            raise RuntimeError(
                f"mt5.symbol_info_tick() returned None for {position.symbol}. "
                f"Error code: {mt5.last_error()}"
            )

        if position.type == mt5.POSITION_TYPE_BUY:
            closing_type = mt5.ORDER_TYPE_SELL
            closing_order_type = OrderType.SELL_MARKET
            price = tick.bid
        else:
            closing_type = mt5.ORDER_TYPE_BUY
            closing_order_type = OrderType.BUY_MARKET
            price = tick.ask

        request: dict[str, object] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": closing_type,
            "position": ticket,
            "price": price,
            "deviation": 10,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"mt5.order_send() (close) returned None. Error code: {mt5.last_error()}")

        success = result.retcode in _SUCCESS_RETCODES
        if success:
            logger.info("Closed position %d: price=%s", ticket, result.price)
            log_fill(
                broker="MT5Broker",
                event="close",
                order_id=str(result.order),
                symbol=position.symbol,
                order_type=closing_order_type,
                volume=result.volume,
                intended_price=price,
                actual_price=result.price,
            )
        else:
            logger.error(
                "Failed to close position %d: retcode=%s comment=%s",
                ticket,
                result.retcode,
                result.comment,
            )

        return OrderResult(
            success=success,
            order_id=str(result.order),
            position_id=str(ticket),
            price=result.price,
            volume=result.volume,
            retcode=result.retcode,
            comment=result.comment or "",
        )

    def get_open_positions(self) -> list[Position]:
        """Fetches all open positions on the connected account via mt5.positions_get().

        Returns:
            A list of currently open Position records.

        Raises:
            RuntimeError: If mt5.positions_get() returns None.
        """
        positions = mt5.positions_get()
        if positions is None:
            raise RuntimeError(f"mt5.positions_get() returned None. Error code: {mt5.last_error()}")

        return [
            Position(
                id=str(pos.ticket),
                symbol=pos.symbol,
                order_type=(
                    OrderType.BUY_MARKET if pos.type == mt5.POSITION_TYPE_BUY else OrderType.SELL_MARKET
                ),
                volume=pos.volume,
                open_price=pos.price_open,
                current_price=pos.price_current,
                stop_loss=pos.sl or None,
                take_profit=pos.tp or None,
                profit=pos.profit,
                timestamp=datetime.fromtimestamp(int(pos.time), tz=UTC),
            )
            for pos in positions
        ]
