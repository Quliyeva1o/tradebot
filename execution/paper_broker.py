"""PaperBroker: an in-memory, JSON-persisted IBroker implementation.

Places/fills/cancels orders and tracks a virtual balance/equity/open
positions entirely in memory, so the rest of the system (strategies, a
future order-sending loop) can run end-to-end against IBroker without
touching real money. Real market prices are read via the existing
MT5Connector.fetch_recent_bars() (read-only; no real order is ever sent to
MT5) so fills and mark-to-market P&L reflect genuine live price action
instead of synthetic data.

Not consumed anywhere yet (Sprint 2 scope): no production code path
constructs a PaperBroker or reads config.execution_config.ExecutionConfig to
decide to. live_signal_check.py is untouched.
"""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from core.models import AccountInfo, OrderType, SymbolConstraints
from core.validation import require_non_negative, require_positive
from execution.event_log import log_fill
from execution.fill_simulator import simulate_market_fill
from execution.interfaces import IBroker
from execution.models import OrderRequest, OrderResult, Position
from execution.order import Order, OrderStatus
from mt5.connector import MT5Connector
from utils.logging import setup_logger

logger = setup_logger("paper_broker")

STATE_FILE = Path(__file__).parent / "paper_broker_state.json"

_MARKET_ORDER_TYPES = (OrderType.BUY_MARKET, OrderType.SELL_MARKET)


def _order_to_dict(order: Order) -> dict:
    """Serializes an Order (including its originating OrderRequest) to a JSON-safe dict."""
    request = order.request
    return {
        "order_id": order.order_id,
        "status": order.status.name,
        "created_at": order.created_at.isoformat(),
        "filled_at": order.filled_at.isoformat() if order.filled_at is not None else None,
        "fill_price": order.fill_price,
        "request": {
            "symbol": request.symbol,
            "order_type": request.order_type.name,
            "volume": request.volume,
            "price": request.price,
            "stop_loss": request.stop_loss,
            "take_profit": request.take_profit,
            "deviation": request.deviation,
            "comment": request.comment,
        },
    }


def _order_from_dict(data: dict) -> Order:
    """Reconstructs an Order from _order_to_dict()'s output.

    Replays the persisted terminal status through Order's own validated
    fill()/reject()/cancel() transition methods (rather than setting
    order.status directly), so a corrupted/unrecognized status value raises
    instead of silently producing an inconsistent Order -- the caller
    (PaperBroker._load_state) treats any such failure as state corruption
    and resets to a fresh paper account.
    """
    request_data = data["request"]
    request = OrderRequest(
        symbol=request_data["symbol"],
        order_type=OrderType[request_data["order_type"]],
        volume=request_data["volume"],
        price=request_data["price"],
        stop_loss=request_data["stop_loss"],
        take_profit=request_data["take_profit"],
        deviation=request_data["deviation"],
        comment=request_data["comment"],
    )
    order = Order(
        order_id=data["order_id"],
        request=request,
        created_at=datetime.fromisoformat(data["created_at"]),
    )

    status = OrderStatus[data["status"]]
    if status is OrderStatus.FILLED:
        order.fill(data["fill_price"], filled_at=datetime.fromisoformat(data["filled_at"]))
    elif status is OrderStatus.REJECTED:
        order.reject()
    elif status is OrderStatus.CANCELLED:
        order.cancel()
    elif status is not OrderStatus.PENDING:
        raise ValueError(f"Unrecognized persisted order status: {status!r}")

    return order


def _position_to_dict(position: Position) -> dict:
    """Serializes a Position to a JSON-safe dict.

    current_price/profit are intentionally NOT persisted: they are
    mark-to-market values recomputed fresh from live MT5 prices on every
    read (see PaperBroker._mark_to_market), so a stale persisted value
    would never actually be used.
    """
    return {
        "id": position.id,
        "symbol": position.symbol,
        "order_type": position.order_type.name,
        "volume": position.volume,
        "open_price": position.open_price,
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "timestamp": position.timestamp.isoformat(),
        "comment": position.comment,
    }


def _position_from_dict(data: dict) -> Position:
    """Reconstructs a Position from _position_to_dict()'s output.

    current_price is seeded to open_price; it is overwritten by the next
    _mark_to_market() call before it is ever returned to a caller.
    """
    return Position(
        id=data["id"],
        symbol=data["symbol"],
        order_type=OrderType[data["order_type"]],
        volume=data["volume"],
        open_price=data["open_price"],
        current_price=data["open_price"],
        stop_loss=data["stop_loss"],
        take_profit=data["take_profit"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        # .get(): state files written before ownership tracking existed have
        # no comment key -- those positions read back as "ownership unknown".
        comment=data.get("comment", ""),
    )


class PaperBroker(IBroker):
    """Virtual IBroker implementation for paper trading against real MT5 prices."""

    def __init__(
        self,
        connector: MT5Connector | None = None,
        initial_balance: float = 10_000.0,
        slippage: float = 0.0,
        timeframe: str = "M5",
    ) -> None:
        """Initializes the PaperBroker, loading any persisted state from STATE_FILE.

        Args:
            connector: The MT5Connector used for read-only real price data
                (fetch_recent_bars). Defaults to a new MT5Connector()
                instance; overridable (e.g. with a test double) via
                injection, the same composition pattern MT5Broker uses.
            initial_balance: Starting virtual balance, used only when no
                valid prior state exists (fresh account or fail-open reset).
            slippage: Price units of adverse slippage applied to every
                simulated market-order fill (see execution/fill_simulator.py).
            timeframe: MT5 timeframe key (e.g. "M5") used to fetch the
                reference bar for fills and mark-to-market pricing.

        Raises:
            ValueError: If initial_balance is not strictly positive, or
                slippage is negative.
        """
        require_positive(initial_balance, "initial_balance")
        require_non_negative(slippage, "slippage")
        self._connector = connector if connector is not None else MT5Connector()
        self._initial_balance = initial_balance
        self._slippage = slippage
        self._timeframe = timeframe
        self._balance: float = initial_balance
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._load_state()

    def connect(self) -> bool:
        """Delegates to MT5Connector.connect().

        Paper trading still needs a live (read-only) MT5 session for real
        price data, even though no real order is ever sent.

        Returns:
            True if connection initialized successfully, False otherwise.
        """
        return self._connector.connect()

    def get_account_info(self) -> AccountInfo:
        """Computes virtual account info: balance plus mark-to-market floating P&L.

        Returns:
            An AccountInfo snapshot of the virtual paper account. margin is
            always 0.0 -- margin/leverage modeling is not implemented this
            sprint, so free_margin always equals equity.
        """
        positions = self.get_open_positions()
        floating_pnl = sum(position.profit for position in positions)
        equity = self._balance + floating_pnl
        return AccountInfo(balance=self._balance, equity=equity, margin=0.0, free_margin=equity)

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        """Delegates to the underlying MT5Connector.fetch_symbol_info().

        Real MT5 symbol metadata, same as MT5Broker -- purely read-only
        (mt5.symbol_info()), consistent with PaperBroker's existing use of
        the connector for real price data while never sending a real order.

        Args:
            symbol: Trading instrument symbol.

        Returns:
            A SymbolConstraints snapshot of the symbol's trading constraints.

        Raises:
            RuntimeError: If MT5Connector.fetch_symbol_info() raises (e.g.
                mt5.symbol_info() returned None).
        """
        return self._connector.fetch_symbol_info(symbol)

    def calculate_margin(
        self, symbol: str, order_type: OrderType, volume: float, price: float
    ) -> float | None:
        """Estimates required margin from real symbol metadata and leverage.

        Paper mode must apply the SAME pre-trade margin ceiling as live (see
        TradeManager.open_trade), otherwise paper results would include
        entries the real account could never afford. Uses the venue's own
        contract size with the account's leverage rather than
        mt5.order_calc_margin(), keeping PaperBroker's rule of touching only
        read-only MT5 metadata.

        Args:
            symbol: Trading instrument symbol.
            order_type: The order type being sized (unused: initial margin is
                symmetric for long and short on the instruments traded here).
            volume: Lot size to price the margin for.
            price: Price to compute the notional at.

        Returns:
            Estimated margin in account currency, or None if the leverage or
            symbol metadata is unavailable.
        """
        try:
            constraints = self._connector.fetch_symbol_info(symbol)
            leverage = self._connector.fetch_account_info().leverage
        except Exception as exc:  # metadata unavailable -> caller skips the check
            logger.warning("calculate_margin: could not price margin for %s (%s).", symbol, type(exc).__name__)
            return None
        if not leverage:
            return None
        return volume * constraints.contract_size * price / leverage

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submits an order to the paper account.

        Market orders (BUY_MARKET/SELL_MARKET) fill synchronously against
        the latest real MT5 bar's open/spread via execution.fill_simulator,
        opening a new virtual Position. Pending order types (*_LIMIT/*_STOP)
        are accepted and stored as PENDING -- Sprint 2 does not implement
        bar-by-bar trigger monitoring for them (that is BacktestEngine's
        job for historical data; replicating it for live paper trading is
        out of scope here), so they remain open until cancel_order() is
        called.

        Args:
            order: The order to submit.

        Returns:
            An OrderResult describing the outcome: always success=True here,
            since PaperBroker does not model business-level rejections
            (e.g. insufficient virtual margin) this sprint.

        Raises:
            RuntimeError: If real price data cannot be fetched for a market
                order (propagated from MT5Connector.fetch_recent_bars()).
        """
        order_id = str(uuid.uuid4())
        internal_order = Order(order_id=order_id, request=order)
        self._orders[order_id] = internal_order

        if order.order_type not in _MARKET_ORDER_TYPES:
            self._save_state()
            logger.info("Pending order %s stored for %s (not yet triggered).", order_id, order.symbol)
            return OrderResult(
                success=True,
                order_id=order_id,
                comment="Pending order accepted (not yet triggered).",
            )

        fill_price, reference_price = self._simulate_fill(order.symbol, order.order_type)
        internal_order.fill(fill_price)

        position = Position(
            id=order_id,
            symbol=order.symbol,
            order_type=order.order_type,
            volume=order.volume,
            open_price=fill_price,
            current_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            timestamp=internal_order.filled_at or datetime.now(UTC),
            # Mirrors MT5Broker: the opening setup_id travels with the
            # position so ownership stays resolvable in paper mode too.
            comment=order.comment or "",
        )
        self._positions[order_id] = position
        self._save_state()

        logger.info("Filled paper order %s for %s @ %.5f", order_id, order.symbol, fill_price)
        log_fill(
            broker="PaperBroker",
            event="open",
            order_id=order_id,
            symbol=order.symbol,
            order_type=order.order_type,
            volume=order.volume,
            intended_price=reference_price,
            actual_price=fill_price,
        )
        return OrderResult(
            success=True,
            order_id=order_id,
            position_id=order_id,
            price=fill_price,
            volume=order.volume,
            comment="Filled",
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a still-PENDING order.

        Args:
            order_id: The id returned by a prior place_order() call.

        Returns:
            True if the order was canceled, False if order_id is unknown or
            the order is no longer PENDING (e.g. already filled).
        """
        order = self._orders.get(order_id)
        if order is None:
            logger.error("cancel_order: unknown order_id %r", order_id)
            return False

        try:
            order.cancel()
        except ValueError as exc:
            logger.error("cancel_order: %s", exc)
            return False

        self._save_state()
        return True

    def close_position(self, position_id: str) -> OrderResult:
        """Closes an open virtual position at a simulated current market price.

        Computes the closing fill via execution.fill_simulator (the same
        spread/slippage convention used to open it), realizes P&L into the
        virtual balance, and persists the close as a new Order -- a fresh
        Order object for the opposite-direction closing leg, filled via its
        own validated fill() transition (PENDING -> FILLED), exactly like
        an opening order. The original opening Order is left untouched:
        FILLED is already its correct, terminal status (Order has no
        separate "closed" status -- the closing transaction is its own Order).

        Args:
            position_id: The id of the position to close (same value as the
                order_id/position_id returned by the original place_order()
                call).

        Returns:
            An OrderResult describing the close. success is always True once
            a position is found -- PaperBroker does not model business-level
            close rejections.

        Raises:
            RuntimeError: If position_id is not a currently open position, or
                real price data cannot be fetched for its symbol (propagated
                from MT5Connector.fetch_recent_bars()).
        """
        position = self._positions.get(position_id)
        if position is None:
            raise RuntimeError(f"No open paper position found for position_id {position_id!r}.")

        closing_type = (
            OrderType.SELL_MARKET
            if position.order_type == OrderType.BUY_MARKET
            else OrderType.BUY_MARKET
        )
        fill_price, reference_price = self._simulate_fill(position.symbol, closing_type)

        closing_request = OrderRequest(
            symbol=position.symbol, order_type=closing_type, volume=position.volume
        )
        closing_order_id = str(uuid.uuid4())
        closing_order = Order(order_id=closing_order_id, request=closing_request)
        closing_order.fill(fill_price)
        self._orders[closing_order_id] = closing_order

        if position.order_type == OrderType.BUY_MARKET:
            pnl = (fill_price - position.open_price) * position.volume
        else:
            pnl = (position.open_price - fill_price) * position.volume
        self._balance += pnl

        del self._positions[position_id]
        self._save_state()

        logger.info(
            "Closed paper position %s for %s @ %.5f (pnl=%.2f)",
            position_id,
            position.symbol,
            fill_price,
            pnl,
        )
        log_fill(
            broker="PaperBroker",
            event="close",
            order_id=closing_order_id,
            symbol=position.symbol,
            order_type=closing_type,
            volume=position.volume,
            intended_price=reference_price,
            actual_price=fill_price,
        )
        return OrderResult(
            success=True,
            order_id=closing_order_id,
            position_id=position_id,
            price=fill_price,
            volume=position.volume,
            comment="Closed",
        )

    def get_open_positions(self) -> list[Position]:
        """Fetches all open virtual positions, mark-to-market against live MT5 prices.

        Returns:
            A list of currently open Position records, each with
            current_price/profit freshly recomputed from the latest real bar.

        Raises:
            RuntimeError: If real price data cannot be fetched for an open
                position's symbol (propagated from MT5Connector.fetch_recent_bars()).
        """
        return [self._mark_to_market(position) for position in self._positions.values()]

    def _simulate_fill(self, symbol: str, order_type: OrderType) -> tuple[float, float]:
        """Fetches the latest real bar for symbol and simulates a market fill against it.

        Shared by place_order() (opening fill) and close_position() (closing
        fill) so the fetch-bar + simulate_market_fill() sequence is not
        duplicated between them.

        Returns:
            (fill_price, reference_price): fill_price is the simulated fill
            (spread/slippage applied); reference_price is next_bar.open
            BEFORE spread/slippage -- the "intended price" used for
            structured slippage logging (see execution/event_log.py).
        """
        next_bar = self._connector.fetch_recent_bars(symbol, self._timeframe, count=1)[-1]
        fill_price = simulate_market_fill(order_type, next_bar.open, next_bar.spread, self._slippage)
        return fill_price, next_bar.open

    def _mark_to_market(self, position: Position) -> Position:
        """Returns a copy of position with current_price/profit refreshed from the latest real bar."""
        current_price = self._connector.fetch_recent_bars(position.symbol, self._timeframe, count=1)[
            -1
        ].close
        if position.order_type == OrderType.BUY_MARKET:
            profit = (current_price - position.open_price) * position.volume
        else:
            profit = (position.open_price - current_price) * position.volume
        return replace(position, current_price=current_price, profit=profit)

    def _load_state(self) -> None:
        """Loads persisted state from STATE_FILE, fail-open on any corruption.

        Mirrors risk/daily_risk_tracker.py's DailyRiskTracker fail-open
        pattern: a missing file is a fresh account (no error); a
        read/parse/schema error is logged at ERROR (a real degradation --
        silently losing paper-trading history should be visible) and also
        resets to a fresh account, exactly like DailyRiskTracker resets its
        baseline to the caller-supplied "current" value (here,
        self._initial_balance) rather than crashing or leaving stale state.
        """
        state = self._read_state()
        if state is None:
            self._balance = self._initial_balance
            self._orders = {}
            self._positions = {}
            return

        try:
            balance = float(state["balance"])
            orders = {oid: _order_from_dict(d) for oid, d in state["orders"].items()}
            positions = {pid: _position_from_dict(d) for pid, d in state["positions"].items()}
        except Exception as exc:  # must never raise; always logged
            logger.error(
                "Malformed paper broker state in %s: %s. Resetting to a fresh paper "
                "account (initial_balance=%.2f).",
                STATE_FILE,
                type(exc).__name__,
                self._initial_balance,
            )
            self._balance = self._initial_balance
            self._orders = {}
            self._positions = {}
            return

        self._balance = balance
        self._orders = orders
        self._positions = positions

    def _read_state(self) -> dict | None:
        """Reads and parses STATE_FILE. Never raises; returns None if missing/corrupt."""
        try:
            data: dict = json.loads(STATE_FILE.read_text())
            return data
        except FileNotFoundError:
            return None
        except Exception as exc:  # must never raise; always logged
            logger.error(
                "Could not read/parse %s: %s. Resetting to a fresh paper account.",
                STATE_FILE,
                type(exc).__name__,
            )
            return None

    def _save_state(self) -> None:
        """Persists the current balance/orders/positions. Never raises (logs ERROR on failure)."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(
                    {
                        "balance": self._balance,
                        "orders": {oid: _order_to_dict(o) for oid, o in self._orders.items()},
                        "positions": {
                            pid: _position_to_dict(p) for pid, p in self._positions.items()
                        },
                    }
                )
            )
        except Exception as exc:  # must never raise; always logged
            logger.error("Could not persist paper broker state to %s: %s", STATE_FILE, type(exc).__name__)
